"""Build and safely install verified single-song PSARC archives from a pack."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import queue
import re
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

from metadata.metadata import HELPER as METADATA_HELPER
from metadata.metadata import extract_metadata
from song_store import SongStore, StoreError, game_is_running
from toolkit_runtime import resolve_toolkit_helper


SPLIT_HELPER = Path(__file__).with_name("split_helper") / "bin" / "SplitPackHelper.exe"
MAX_RUN_SECONDS = 2 * 60 * 60


class SplitPackError(RuntimeError):
    """A split failed without changing or deleting its source archive."""


class SplitPackInstallError(SplitPackError):
    """Installation failed; already published outputs remain listed and intact."""

    def __init__(self, message: str, installed_paths: Iterable[Path] = ()) -> None:
        super().__init__(message)
        self.installed_paths = tuple(Path(path) for path in installed_paths)


@dataclass(frozen=True)
class SplitOutput:
    artist: str
    title: str
    song_key: str
    path: Path
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class SplitResult:
    output_directory: Path
    outputs: tuple[SplitOutput, ...]
    source_sha256: str
    source_backup: Path | None = None


def resolve_rocksmith_dlc_directory(game_directory: str | Path) -> Path:
    """Resolve the configured Rocksmith install's existing DLC directory."""
    try:
        game_root = Path(game_directory).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise SplitPackError(f"Rocksmith folder is unavailable: {game_directory}") from exc
    if not game_root.is_dir() or not (game_root / "Rocksmith2014.exe").is_file():
        raise SplitPackError(
            f"Configured folder is not a Rocksmith 2014 install: {game_root}. "
            "Set the Rocksmith folder at the top of the app first."
        )
    dlc_directory = game_root / "dlc"
    try:
        resolved_dlc = dlc_directory.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise SplitPackError(
            f"Rocksmith DLC folder does not exist: {dlc_directory}. "
            "Check the configured game folder; the app will not create a replacement."
        ) from exc
    if not resolved_dlc.is_dir() or resolved_dlc.parent != game_root:
        raise SplitPackError(
            f"Rocksmith DLC folder must be the direct dlc subfolder of {game_root}."
        )
    return resolved_dlc


def validate_split_metadata(metadata: object) -> tuple[dict, ...]:
    """Return safe song records; reject incomplete or warning-bearing indexes."""
    if not isinstance(metadata, dict):
        raise SplitPackError("Current song metadata is unavailable. Run Full refresh, then try again.")
    warnings = metadata.get("warnings")
    if not isinstance(warnings, list) or warnings:
        note = "; ".join(str(value) for value in (warnings or [])[:5]) or "warning status is unknown"
        raise SplitPackError(f"Song metadata has warnings ({note}). Refresh titles before splitting.")
    songs = metadata.get("songs")
    if not isinstance(songs, list) or len(songs) < 2:
        raise SplitPackError("This archive does not have at least two verified song records to split.")
    for index, song in enumerate(songs):
        if not isinstance(song, dict):
            raise SplitPackError(f"Song record {index + 1} has an invalid metadata shape.")
        for field in ("artist", "title", "song_key", "dlc_key"):
            if not str(song.get(field) or "").strip():
                raise SplitPackError(f"Song record {index + 1} is missing {field}.")
        manifests = song.get("manifest_files")
        if not isinstance(manifests, list) or not manifests:
            raise SplitPackError(f"{song['artist']} - {song['title']} has no verified source manifests.")
        for manifest in manifests:
            normalized = str(manifest).replace("\\", "/")
            if (not normalized.casefold().startswith("manifests/")
                    or Path(normalized).is_absolute()
                    or ".." in normalized.split("/")):
                raise SplitPackError(f"Unsafe or invalid manifest path in metadata: {manifest}")
    return tuple(songs)


def split_pack_unavailable_reason(metadata: object) -> str | None:
    """Return user-facing reason a source archive cannot be split as a multi-song pack."""
    try:
        validate_split_metadata(metadata)
    except SplitPackError as exc:
        return str(exc)
    return None


def selected_song_payload(songs: Iterable[dict], selected_indices: Iterable[int]) -> list[dict]:
    """Build validated helper input from checked zero-based song rows."""
    records = tuple(songs)
    indices = tuple(dict.fromkeys(int(index) for index in selected_indices))
    if not indices:
        raise SplitPackError("Check at least one song to create as an individual PSARC.")
    payload = []
    identities = set()
    for index in indices:
        if index < 0 or index >= len(records):
            raise SplitPackError("Song selection changed. Close this dialog and reopen Archive details.")
        song = records[index]
        identity = tuple(str(song.get(key) or "").strip().casefold() for key in
                         ("dlc_key", "song_key", "artist", "title"))
        if identity in identities:
            raise SplitPackError(f"Duplicate song identity selected: {song.get('artist')} - {song.get('title')}")
        identities.add(identity)
        payload.append({
            "artist": str(song["artist"]).strip(),
            "title": str(song["title"]).strip(),
            "song_key": str(song["song_key"]).strip(),
            "dlc_key": str(song["dlc_key"]).strip(),
            "manifest_files": [str(path).replace("\\", "/") for path in song["manifest_files"]],
            "arrangement_ids": [str(value) for value in song.get("arrangement_ids", [])],
        })
    return payload


def update_visible_song_selection(
    existing_indices: Iterable[int],
    visible_indices: Iterable[int],
    selected_visible_indices: Iterable[int],
) -> set[int]:
    """Replace selection for visible rows while preserving tracks hidden by search."""
    existing = {int(index) for index in existing_indices}
    visible = {int(index) for index in visible_indices}
    selected_visible = {int(index) for index in selected_visible_indices}
    if any(index < 0 for index in existing | visible | selected_visible):
        raise SplitPackError("Song selection contains a negative row index.")
    if not selected_visible.issubset(visible):
        raise SplitPackError("Selected song rows changed while the list was refreshing.")
    return (existing - visible) | selected_visible


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except ValueError:
        return False


def create_split_output_directory(
    source: str | Path,
    output_parent: str | Path | None = None,
    forbidden_root: str | Path | None = None,
    now: datetime | None = None,
) -> Path:
    """Create a fresh review folder outside the game tree; never reuse or overwrite."""
    source_path = Path(source).resolve(strict=True)
    parent = Path(output_parent) if output_parent is not None else (
        Path.home() / "Documents" / "JonsRockSmithMods" / "SplitPacks"
    )
    parent = parent.expanduser().resolve(strict=False)
    if forbidden_root is not None and _is_within(parent, Path(forbidden_root)):
        raise SplitPackError("Choose an output folder outside the Rocksmith game folder. Split files are review copies, not installed songs.")
    if _is_within(parent, source_path.parent):
        raise SplitPackError("Choose an output folder outside the source archive folder. Original PSARC must stay separate from split copies.")
    parent.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", source_path.stem).strip("._-") or "RocksmithPack"
    base = parent / f"{stem}_individual_songs_{stamp}"
    candidate = base
    suffix = 2
    while candidate.exists():
        candidate = parent / f"{base.name}_{suffix:02d}"
        suffix += 1
    candidate.mkdir()
    return candidate


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def _identity(song: dict) -> tuple[str, str, str, str]:
    return tuple(str(song.get(key) or "").strip().casefold() for key in
                 ("dlc_key", "song_key", "artist", "title"))


def _read_helper_lines(process: subprocess.Popen, lines: queue.Queue) -> None:
    try:
        assert process.stdout is not None
        for line in process.stdout:
            lines.put(line.rstrip("\r\n"))
    finally:
        lines.put(None)


def _kill_at_timeout(process: subprocess.Popen, timed_out: threading.Event) -> None:
    if process.poll() is None:
        timed_out.set()
        try:
            process.kill()
        except OSError:
            pass


def _progress_text(line: str) -> str | None:
    parts = line.split("|", 4)
    if len(parts) >= 5 and parts[0] == "PROGRESS":
        stage, current, total, detail = parts[1:]
        labels = {"extract": "Reading source archive", "pack": "Building individual song"}
        return f"{labels.get(stage, stage)} {current}/{total}: {detail}"
    if line.startswith("CREATED|"):
        return "Individual archive created; verifying contents."
    return None


def split_pack_songs(
    source: str | Path,
    metadata: dict,
    selected_indices: Iterable[int],
    toolkit_directory: str | Path,
    output_parent: str | Path | None = None,
    forbidden_root: str | Path | None = None,
    progress: Callable[[str], None] | None = None,
    helper: str | Path = SPLIT_HELPER,
    metadata_helper: str | Path = METADATA_HELPER,
    timeout_seconds: int = MAX_RUN_SECONDS,
) -> SplitResult:
    """Build selected single-song PSARCs, verify each independently, retain all outputs."""
    source_path = Path(source).resolve(strict=True)
    toolkit_path = Path(toolkit_directory).resolve(strict=True)
    helper_path = resolve_toolkit_helper(helper, toolkit_path).resolve(strict=True)
    metadata_helper_path = Path(metadata_helper).resolve(strict=True)
    if source_path.suffix.casefold() != ".psarc":
        raise SplitPackError("Source must be a PSARC archive.")
    if not (toolkit_path / "RocksmithToolkitLib.dll").is_file():
        raise SplitPackError(f"RocksmithToolkitLib.dll is missing from {toolkit_path}.")
    source_songs = validate_split_metadata(metadata)
    payload = selected_song_payload(source_songs, selected_indices)
    source_hash_before = _sha256(source_path)
    output_directory = create_split_output_directory(
        source_path, output_parent=output_parent, forbidden_root=forbidden_root,
    )
    log_lines: list[str] = []
    timed_out = threading.Event()
    with tempfile.TemporaryDirectory(prefix="JonsRockSmithMods_split_request_") as request_root:
        selection_path = Path(request_root) / "selection.json"
        selection_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        process = subprocess.Popen(
            [str(helper_path), str(toolkit_path), str(source_path), str(selection_path), str(output_directory)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=flags,
        )
        lines: queue.Queue[str | None] = queue.Queue()
        reader = threading.Thread(target=_read_helper_lines, args=(process, lines), daemon=True)
        reader.start()
        timer = threading.Timer(timeout_seconds, _kill_at_timeout, args=(process, timed_out))
        timer.daemon = True
        timer.start()
        last_progress_emit = 0.0
        try:
            while True:
                try:
                    line = lines.get(timeout=0.25)
                except queue.Empty:
                    if process.poll() is not None and not reader.is_alive():
                        break
                    continue
                if line is None:
                    break
                log_lines.append(line)
                logging.info("SplitPackHelper: %s", line)
                status = _progress_text(line)
                now = time.monotonic()
                if status and progress and now - last_progress_emit >= 0.25:
                    progress(status)
                    last_progress_emit = now
            try:
                return_code = process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                return_code = process.wait(timeout=5)
        finally:
            timer.cancel()
            if process.poll() is None:
                process.kill()
                process.wait()
            reader.join(timeout=2)
    if timed_out.is_set():
        raise SplitPackError(
            f"Split timed out after {timeout_seconds} seconds. Any completed files remain in {output_directory}. "
            "No source archive was deleted."
        )
    if return_code != 0:
        detail = "\n".join(log_lines[-12:]) or f"helper exit code {return_code}"
        raise SplitPackError(
            f"Split stopped safely: {detail}\nReview folder: {output_directory}\n"
            "The source PSARC remains in place. Remove no files unless you choose to do so."
        )

    report_path = output_directory / "split-report.json"
    if not report_path.is_file():
        raise SplitPackError(f"Helper finished without a split report. Any created files remain in {output_directory}.")
    try:
        report = json.loads(report_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SplitPackError(f"Split report could not be read: {exc}. Output remains in {output_directory}.") from exc
    if not isinstance(report, dict) or report.get("selected_song_count") != len(payload):
        raise SplitPackError(f"Split report count does not match selected songs. Output remains in {output_directory}.")
    if Path(str(report.get("source", ""))).resolve(strict=False) != source_path:
        raise SplitPackError(f"Split report names a different source archive. Output remains in {output_directory}.")
    if str(report.get("source_sha256_before", "")).upper() != source_hash_before:
        raise SplitPackError(f"Helper source hash does not match pre-split hash. Output remains in {output_directory}.")
    if str(report.get("source_sha256_after", "")).upper() != source_hash_before:
        raise SplitPackError(f"Helper detected source archive change. Output remains in {output_directory}.")
    records = report.get("outputs")
    if not isinstance(records, list) or len(records) != len(payload):
        raise SplitPackError(f"Split report has invalid output records. Output remains in {output_directory}.")

    expected = {_identity(song): song for song in payload}
    verified_outputs = []
    seen = set()
    for record in records:
        if not isinstance(record, dict):
            raise SplitPackError(f"Split report has malformed output row. Output remains in {output_directory}.")
        output_path = Path(str(record.get("path", ""))).resolve(strict=False)
        if not _is_within(output_path, output_directory) or not output_path.is_file() or output_path.suffix.casefold() != ".psarc":
            raise SplitPackError(f"Reported output path is missing or unsafe: {output_path}. Output remains in {output_directory}.")
        identity = tuple(str(record.get(key) or "").strip().casefold() for key in
                         ("dlc_key", "song_key", "artist", "title"))
        if identity not in expected:
            raise SplitPackError(f"Output identity does not match one selected song: {output_path.name}.")
        if identity in seen:
            raise SplitPackError(f"A selected song appears more than once in split report: {output_path.name}.")
        seen.add(identity)
        if _sha256(output_path) != str(record.get("sha256", "")).upper():
            raise SplitPackError(f"Output hash does not match split report: {output_path.name}.")
        if progress:
            progress(f"Reading back {output_path.name} with Toolkit metadata reader.")
        readback = extract_metadata(
            output_path, toolkit=toolkit_path, helper=metadata_helper_path, timeout_seconds=240,
        )
        readback_songs = validate_single_song_readback(readback, expected[identity], output_path)
        expected_arrangements = set(expected[identity].get("arrangement_ids", []))
        found_arrangements = set(readback_songs[0].get("arrangement_ids", []))
        if expected_arrangements and found_arrangements != expected_arrangements:
            raise SplitPackError(
                f"Arrangement readback mismatch for {output_path.name}: "
                f"expected {len(expected_arrangements)}, found {len(found_arrangements)}."
            )
        verified_outputs.append(SplitOutput(
            artist=str(record["artist"]),
            title=str(record["title"]),
            song_key=str(record["song_key"]),
            path=output_path,
            size_bytes=output_path.stat().st_size,
            sha256=_sha256(output_path),
        ))

    if seen != set(expected):
        raise SplitPackError(f"Not every selected song was verified. Output remains in {output_directory}.")
    source_hash_after = _sha256(source_path)
    if source_hash_after != source_hash_before:
        raise SplitPackError(f"Source hash changed during split. Stop using output and check archive: {source_path}")
    if progress:
        progress("All individual archives and source hash verified.")
    return SplitResult(output_directory, tuple(verified_outputs), source_hash_after)


def install_split_outputs(
    result: SplitResult,
    game_directory: str | Path,
    progress: Callable[[str], None] | None = None,
) -> SplitResult:
    """Atomically publish verified PSARCs into the configured Rocksmith DLC folder."""
    game_root = Path(game_directory).expanduser().resolve(strict=True)
    dlc_directory = resolve_rocksmith_dlc_directory(game_root)
    staging_directory = Path(result.output_directory).resolve(strict=True)
    if _is_within(staging_directory, game_root):
        raise SplitPackInstallError("Split staging must stay outside the Rocksmith folder.")
    if not result.outputs:
        raise SplitPackInstallError("The verified split result contains no PSARC files.")

    destinations: list[tuple[SplitOutput, Path]] = []
    names: set[str] = set()
    conflicts: list[Path] = []
    for output in result.outputs:
        try:
            source_path = Path(output.path).resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise SplitPackInstallError(f"Verified staged archive disappeared: {output.path}") from exc
        if (
            not _is_within(source_path, staging_directory)
            or source_path.suffix.casefold() != ".psarc"
            or not source_path.is_file()
        ):
            raise SplitPackInstallError(f"Unsafe staged song archive: {source_path}")
        if source_path.stat().st_size != output.size_bytes or _sha256(source_path) != output.sha256.upper():
            raise SplitPackInstallError(f"Staged song archive changed after verification: {source_path}")
        filename = source_path.name
        filename_key = filename.casefold()
        if filename in (".", "..") or filename_key in names:
            raise SplitPackInstallError(f"Duplicate or invalid split output filename: {filename}")
        names.add(filename_key)
        destination = dlc_directory / filename
        if len(str(destination)) >= 240:
            raise SplitPackInstallError(
                f"Rocksmith DLC path is too long for this Toolkit build: {destination}"
            )
        if destination.exists() or destination.is_symlink():
            conflicts.append(destination)
        destinations.append((output, destination))
    if conflicts:
        shown = "\n".join(str(path) for path in conflicts[:8])
        more = f"\n…and {len(conflicts) - 8} more" if len(conflicts) > 8 else ""
        raise SplitPackInstallError(
            f"No split songs were installed because destination files already exist:\n{shown}{more}\n"
            "Move or rename the existing files, then retry. Existing files were not changed."
        )

    temporary_files: list[Path] = []
    installed: list[SplitOutput] = []
    try:
        total_outputs = len(destinations)
        for index, (output, destination) in enumerate(destinations, start=1):
            if progress:
                progress(f"Staging {index}/{total_outputs}: {destination.name}")
            file_descriptor, temporary_name = tempfile.mkstemp(
                prefix=".JonsRockSmithMods_split_",
                suffix=".tmp",
                dir=dlc_directory,
            )
            temporary_path = Path(temporary_name)
            temporary_files.append(temporary_path)
            copied = 0
            last_reported = 0
            with os.fdopen(file_descriptor, "wb") as target, Path(output.path).open("rb") as source:
                while True:
                    block = source.read(4 * 1024 * 1024)
                    if not block:
                        break
                    target.write(block)
                    copied += len(block)
                    if progress and (copied - last_reported >= 64 * 1024 * 1024 or copied == output.size_bytes):
                        progress(
                            f"Staging {index}/{total_outputs}: {destination.name} "
                            f"({copied / 1048576:.0f}/{output.size_bytes / 1048576:.0f} MiB)"
                        )
                        last_reported = copied
                target.flush()
                os.fsync(target.fileno())
            if copied != output.size_bytes or _sha256(temporary_path) != output.sha256.upper():
                raise OSError(f"Staged copy verification failed for {destination.name}.")

        for index, (output, destination) in enumerate(destinations, start=1):
            if destination.exists() or destination.is_symlink():
                raise FileExistsError(f"Destination appeared during install: {destination}")
            temporary_path = temporary_files[index - 1]
            if progress:
                progress(f"Installing verified song {index}/{len(destinations)}: {destination.name}")
            temporary_path.rename(destination)
            installed.append(SplitOutput(
                artist=output.artist,
                title=output.title,
                song_key=output.song_key,
                path=destination,
                size_bytes=output.size_bytes,
                sha256=output.sha256.upper(),
            ))
    except Exception as exc:
        installed_paths = tuple(output.path for output in installed)
        if installed:
            installed_names = ", ".join(path.name for path in installed_paths)
            message = (
                f"Partial install: {len(installed)} of {len(destinations)} verified song(s) were installed in "
                f"{dlc_directory}. Installed files: {installed_names}. Those files remain intact; "
                f"remaining songs were not installed. Details: {exc}"
            )
        else:
            message = f"No split songs were installed in {dlc_directory}. Details: {exc}"
        raise SplitPackInstallError(message, installed_paths) from exc
    finally:
        for temporary_path in temporary_files:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                logging.exception("Could not remove temporary split file %s", temporary_path)

    if progress:
        progress(f"Installed and verified {len(installed)} song PSARC(s) in {dlc_directory}.")
    return SplitResult(dlc_directory, tuple(installed), result.source_sha256)


def _rollback_generated_outputs(outputs: Iterable[SplitOutput]) -> tuple[Path, ...]:
    """Remove only unchanged files created by the current failed replacement."""
    remaining = []
    for output in outputs:
        try:
            if not output.path.exists():
                continue
            if output.path.stat().st_size != output.size_bytes or _sha256(output.path) != output.sha256:
                raise OSError("replacement was changed by another program")
            output.path.unlink()
        except OSError:
            logging.exception("Cannot roll back generated split song %s", output.path)
            remaining.append(output.path)
    return tuple(remaining)


def split_and_install_pack(
    source: str | Path,
    metadata: dict,
    selected_indices: Iterable[int],
    toolkit_directory: str | Path,
    game_directory: str | Path,
    progress: Callable[[str], None] | None = None,
    helper: str | Path = SPLIT_HELPER,
    metadata_helper: str | Path = METADATA_HELPER,
    timeout_seconds: int = MAX_RUN_SECONDS,
    review_directory: str | Path | None = None,
    running_check: Callable[[], bool] = game_is_running,
) -> SplitResult:
    """Build verified singles and optionally replace a complete active pack reversibly."""
    game_root = Path(game_directory).expanduser().resolve(strict=True)
    resolve_rocksmith_dlc_directory(game_root)
    selected_indices = tuple(selected_indices)
    source_path = Path(source).resolve(strict=review_directory is not None)
    store = None
    relative_source = None
    source_backup = None
    source_identity = None
    if review_directory is not None:
        if set(selected_indices) != set(range(len(validate_split_metadata(metadata)))):
            raise SplitPackError("Select every song before replacing the source pack; unselected songs must stay available.")
        if running_check():
            raise SplitPackError("Close Rocksmith before replacing a pack with individual songs, then restart it after splitting.")
        store = SongStore(game_root, Path(review_directory), running_check=running_check)
        matches = [row for row in store.inventory() if row.path.resolve(strict=True) == source_path]
        if len(matches) != 1:
            raise SplitPackError("Source pack must be in this game's DLC or its managed Do Not Want folder. Refresh first.")
        row = matches[0]
        source_identity = row.identity
        if row.location == "Active":
            relative_source = row.relative_path
            backup_target = store.songs_root / relative_source
            if backup_target.exists() or backup_target.is_symlink():
                raise SplitPackError(f"Original-pack backup destination is occupied: {backup_target}. No files were changed.")
        else:
            source_backup = source_path
    if progress:
        progress("Preparing temporary workspace; the original pack is retained until every replacement is verified.")
    with tempfile.TemporaryDirectory(prefix="JRS_Split_") as temporary_root:
        try:
            staged_result = split_pack_songs(
                source,
                metadata,
                selected_indices,
                toolkit_directory=toolkit_directory,
                output_parent=temporary_root,
                forbidden_root=game_root,
                progress=progress,
                helper=helper,
                metadata_helper=metadata_helper,
                timeout_seconds=timeout_seconds,
            )
            if store is not None and running_check():
                raise SplitPackError("Rocksmith started during splitting. Close it and retry; no songs were installed.")
            try:
                installed_result = install_split_outputs(staged_result, game_root, progress)
            except SplitPackInstallError as exc:
                if store is None or not exc.installed_paths:
                    raise
                published = [
                    replace(output, path=path)
                    for output in staged_result.outputs
                    for path in exc.installed_paths if path.name == output.path.name
                ]
                remaining = _rollback_generated_outputs(published)
                if remaining:
                    raise SplitPackInstallError(
                        f"Partial install: source pack remains active. Review these generated files:\n"
                        + "\n".join(str(path) for path in remaining), remaining
                    ) from exc
                raise SplitPackError(
                    f"Replacement failed: {exc}. Published singles were rolled back; original pack stays active."
                ) from exc
            if store is None:
                return installed_result
            try:
                if _sha256(source_path) != staged_result.source_sha256:
                    raise StoreError("Source pack changed after verification; the original will stay active.")
                if relative_source is not None:
                    if progress:
                        progress("Every single-song file is verified. Moving the original pack to Do Not Want...")
                    store.move_to_review([relative_source], {relative_source: source_identity})
                    source_backup = store.songs_root / relative_source
            except Exception as exc:
                if not source_path.is_file():
                    recovery_path = store.songs_root / relative_source if relative_source is not None else source_path
                    raise SplitPackInstallError(
                        f"Partial install: the original pack is no longer at {source_path}. "
                        f"Verified individual songs remain installed to preserve the playable library. "
                        f"Check the original-pack recovery location: {recovery_path}. Details: {exc}",
                        tuple(output.path for output in installed_result.outputs),
                    ) from exc
                remaining = _rollback_generated_outputs(installed_result.outputs)
                if remaining:
                    detail = "\n".join(str(path) for path in remaining)
                    raise SplitPackInstallError(
                        f"Partial install: original pack could not be backed up ({exc}). "
                        f"These generated replacements need manual review:\n{detail}", remaining
                    ) from exc
                raise SplitPackError(
                    f"Original pack could not be backed up: {exc}. Generated replacements were rolled back; "
                    "the original pack remains available."
                ) from exc
            return replace(installed_result, source_backup=source_backup)
        except SplitPackInstallError:
            raise
        except SplitPackError as exc:
            raise SplitPackError(
                f"{exc}\n\nNo split PSARCs were installed. Temporary staging files will be removed."
            ) from exc


def validate_single_song_readback(metadata: object, expected: dict, output_path: Path) -> tuple[dict, ...]:
    if not isinstance(metadata, dict) or metadata.get("warnings"):
        raise SplitPackError(f"Toolkit metadata readback reported warnings for {output_path.name}.")
    songs = metadata.get("songs")
    if not isinstance(songs, list) or len(songs) != 1:
        raise SplitPackError(f"Output must contain exactly one song record: {output_path.name}.")
    if _identity(songs[0]) != _identity(expected):
        raise SplitPackError(f"Output artist/title/SongKey mismatch: {output_path.name}.")
    return tuple(songs)
