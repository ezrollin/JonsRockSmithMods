"""Validated local bridge for RSMods in-game song-list requests."""

from __future__ import annotations

import json
import os
import re
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from archive_details import metadata_song_records
from song_store import FileIdentity, SongFile, StoreError


REQUEST_VERSION = 1
MAX_REQUEST_BYTES = 4096
MAX_REQUEST_FILES = 64
MAX_FUTURE_SKEW_MS = 60 * 1000
_REQUEST_ID = re.compile(r"^[A-Za-z0-9_.-]{1,160}$")
_SONG_KEY = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


def default_request_directory() -> Path:
    """Return one per-Windows-user IPC folder shared with the native RSMods extension."""

    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        root = Path(local_app_data)
    else:
        root = Path.home() / "AppData" / "Local"
    return root / "JonsRockSmithMods" / "InGameSongRequests"


REQUEST_DIRECTORY = default_request_directory()


@dataclass(frozen=True)
class InGameSongRequest:
    request_id: str
    song_key: str
    captured_at_unix_ms: int
    path: Path
    identity: FileIdentity


@dataclass(frozen=True)
class InGameRequestBatch:
    requests: tuple[InGameSongRequest, ...]
    expired_count: int = 0
    invalid_count: int = 0


def load_pending_game_song_requests(
    directory: str | Path = REQUEST_DIRECTORY,
    *,
    now_unix_ms: int | None = None,
) -> InGameRequestBatch:
    """Read durable marks; reject invalid/future records and collapse repeated SongKeys.

    Age never expires a valid user mark. Archive identity is revalidated separately
    before a mark can become a reversible removal queue entry.
    """

    root = Path(directory)
    if root.is_symlink():
        raise StoreError("In-game request folder cannot be a symbolic link")
    if not root.exists():
        return InGameRequestBatch(())
    if not root.is_dir():
        raise StoreError("In-game request path is not a folder")

    try:
        paths = sorted(root.glob("request-*.json"), key=lambda path: path.name.casefold())
    except OSError as exc:
        raise StoreError(f"Cannot list in-game song requests: {exc}") from exc
    if len(paths) > MAX_REQUEST_FILES:
        raise StoreError("Too many in-game song requests; clear the request folder before retrying")

    now = int(time.time() * 1000) if now_unix_ms is None else now_unix_ms
    requests: list[InGameSongRequest] = []
    expired_count = 0
    invalid_count = 0
    for path in paths:
        if path.is_symlink():
            raise StoreError(f"In-game request cannot be a symbolic link: {path.name}")
        try:
            before = path.lstat()
            if not stat.S_ISREG(before.st_mode):
                raise StoreError(f"In-game request is not a regular file: {path.name}")
            identity = _file_identity(before)
            if before.st_size > MAX_REQUEST_BYTES:
                _unlink_if_same(path, identity)
                invalid_count += 1
                continue
            raw = path.read_text(encoding="utf-8")
            after = path.lstat()
        except FileNotFoundError:
            continue
        except (OSError, UnicodeError) as exc:
            raise StoreError(f"Cannot read in-game song request {path.name}: {exc}") from exc

        if _file_identity(after) != identity or stat.S_ISLNK(after.st_mode):
            raise StoreError(f"In-game song request changed while being read: {path.name}")
        try:
            request_id, song_key, captured_at = _parse_request(path, raw)
        except StoreError:
            _unlink_if_same(path, identity)
            invalid_count += 1
            continue

        age = now - captured_at
        if age < -MAX_FUTURE_SKEW_MS:
            _unlink_if_same(path, identity)
            expired_count += 1
            continue
        requests.append(InGameSongRequest(request_id, song_key, captured_at, path, identity))

    requests.sort(key=lambda request: (request.captured_at_unix_ms, request.request_id))
    unique: list[InGameSongRequest] = []
    seen: set[str] = set()
    for request in requests:
        key = request.song_key.casefold()
        if key in seen:
            _unlink_if_same(request.path, request.identity)
        else:
            seen.add(key)
            unique.append(request)
    return InGameRequestBatch(tuple(unique), expired_count, invalid_count)


def discard_game_song_request(request: InGameSongRequest) -> None:
    """Acknowledge one request only if its exact file identity is unchanged."""

    path = request.path
    if path.is_symlink() or not _is_safe_request_filename(path, request.request_id):
        raise StoreError("In-game request path is unsafe; no song was queued")
    try:
        current = _file_identity(path.lstat())
    except FileNotFoundError:
        return
    except OSError as exc:
        raise StoreError(f"Cannot acknowledge in-game request: {exc}") from exc
    if current != request.identity:
        raise StoreError("In-game request changed before confirmation; no song was queued")
    try:
        path.unlink()
    except OSError as exc:
        raise StoreError(f"Could not acknowledge in-game request: {exc}") from exc


def resolve_active_archive_for_song_key(
    rows: Iterable[SongFile],
    song_key: str,
    metadata_lookup: Callable[[Path], dict | None],
    *,
    require_single_song_archive: bool = False,
) -> SongFile:
    """Resolve a preview SongKey to one fully verified active PSARC or fail closed.

    In-game one-song removal may require a standalone archive so a mark cannot
    move unrelated tracks from a multi-song pack.
    """

    if not isinstance(song_key, str) or not _SONG_KEY.fullmatch(song_key):
        raise StoreError("Rocksmith supplied an invalid SongKey; no archive was selected")
    wanted = song_key.casefold()
    active_rows = [row for row in rows if row.location == "Active"]
    if not active_rows:
        raise StoreError("No active song archives are loaded; run Full refresh and try again")

    incomplete: list[str] = []
    matches: dict[str, tuple[SongFile, int]] = {}
    for row in active_rows:
        if row.identity is None:
            incomplete.append(row.relative_path)
            continue
        try:
            if row.path.is_symlink() or not row.path.is_file():
                incomplete.append(row.relative_path)
                continue
            if _file_identity(row.path.lstat()) != row.identity:
                raise StoreError(
                    f"Archive changed since the library scan: {row.relative_path}. Run Full refresh."
                )
            metadata = metadata_lookup(row.path)
        except StoreError:
            raise
        except OSError as exc:
            raise StoreError(f"Cannot verify active archive {row.relative_path}: {exc}") from exc
        if not _metadata_is_complete(metadata):
            incomplete.append(row.relative_path)
            continue
        records = metadata_song_records(metadata)
        if any(
            str(song.get("song_key") or "").strip().casefold() == wanted
            for song in records
        ):
            key = str(row.path.resolve(strict=False)).casefold()
            matches[key] = (row, len(records))

    if incomplete:
        sample = ", ".join(incomplete[:3])
        more = f" (+{len(incomplete) - 3} more)" if len(incomplete) > 3 else ""
        raise StoreError(
            f"Cannot prove a unique archive because {len(incomplete)} active archive(s) lack current, "
            f"complete metadata: {sample}{more}. Run Full refresh / Read titles, then try again."
        )
    if not matches:
        raise StoreError(
            f"SongKey {song_key} was not found in the active library's verified metadata. "
            "Run Full refresh / Read titles and try again."
        )
    if len(matches) > 1:
        paths = ", ".join(row.relative_path for row, _song_count in matches.values())
        raise StoreError(
            f"SongKey {song_key} appears in multiple active archives ({paths}); "
            "no archive was queued. Select an archive manually in the app."
        )
    row, song_count = next(iter(matches.values()))
    if require_single_song_archive and song_count != 1:
        raise StoreError(
            f"SongKey {song_key} is inside a {song_count}-track pack. No archive moved. "
            "Split the pack into individual songs, then retry the saved F10 mark."
        )
    return row


def _metadata_is_complete(metadata: dict | None) -> bool:
    if not isinstance(metadata, dict) or metadata.get("warnings"):
        return False
    songs = metadata_song_records(metadata)
    if not songs or any(
        not isinstance(song, dict) or not str(song.get("song_key") or "").strip()
        for song in songs
    ):
        return False
    song_count = metadata.get("song_count")
    return song_count is None or (type(song_count) is int and song_count == len(songs))


def _parse_request(path: Path, raw: str) -> tuple[str, str, int]:
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise StoreError("Request JSON is malformed") from exc
    if not isinstance(document, dict):
        raise StoreError("Request must be a JSON object")
    if type(document.get("version")) is not int or document["version"] != REQUEST_VERSION:
        raise StoreError("Request version is unsupported")
    request_id = document.get("request_id")
    song_key = document.get("song_key")
    captured_at = document.get("captured_at_unix_ms")
    if not isinstance(request_id, str) or not _REQUEST_ID.fullmatch(request_id):
        raise StoreError("Request ID is invalid")
    if not _is_safe_request_filename(path, request_id):
        raise StoreError("Request ID does not match its filename")
    if not isinstance(song_key, str) or not _SONG_KEY.fullmatch(song_key):
        raise StoreError("SongKey is invalid")
    if type(captured_at) is not int or captured_at <= 0:
        raise StoreError("Request timestamp is invalid")
    return request_id, song_key, captured_at


def _is_safe_request_filename(path: Path, request_id: str) -> bool:
    return (
        _REQUEST_ID.fullmatch(request_id) is not None
        and path.name == f"request-{request_id}.json"
    )


def _file_identity(value) -> FileIdentity:
    return value.st_size, value.st_mtime_ns, value.st_dev, value.st_ino


def _unlink_if_same(path: Path, identity: FileIdentity) -> None:
    try:
        current = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(current.st_mode) or _file_identity(current) != identity:
        raise StoreError(f"In-game request changed before cleanup: {path.name}")
    try:
        path.unlink()
    except OSError as exc:
        raise StoreError(f"Could not clean in-game request {path.name}: {exc}") from exc
