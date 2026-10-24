"""Short, in-app audio previews extracted read-only from Rocksmith PSARC files."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import wave
from collections import Counter
from dataclasses import dataclass
from pathlib import Path


PREVIEW_SECONDS = 30
_TEMP_PREFIX = "JonsRockSmithMods_preview_"


class PreviewError(RuntimeError):
    """A preview failure with user-facing context and separate log diagnostics."""

    def __init__(self, message: str, *, archive: str | Path | None = None,
                 stage: str = "Preview validation", diagnostics: str = "") -> None:
        self.archive = str(archive) if archive is not None else "Unknown archive"
        self.stage = stage
        self.diagnostics = diagnostics
        super().__init__(f"{message}\nStage: {stage}\nArchive: {self.archive}")


@dataclass(frozen=True)
class PreviewAsset:
    wav_path: Path
    working_directory: Path
    seconds: float


def _output_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _tool_diagnostics(toolkit: Path, stage: str, *, timeout: int,
                      stdout="", stderr="", returncode=None) -> str:
    fields = [f"Toolkit={toolkit}", f"stage={stage}", f"timeout_seconds={timeout}"]
    if returncode is not None:
        fields.append(f"returncode={returncode}")
    stdout_text = _output_text(stdout).strip()
    stderr_text = _output_text(stderr).strip()
    if stdout_text:
        fields.append("stdout_tail=" + stdout_text[-2000:])
    if stderr_text:
        fields.append("stderr_tail=" + stderr_text[-2000:])
    return "\n".join(fields)


def _run_tool(args, *, cwd: Path, toolkit: Path, archive: Path,
              stage: str, timeout: int):
    try:
        return subprocess.run(
            args,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as exc:
        diagnostics = _tool_diagnostics(
            toolkit, stage, timeout=timeout, stdout=exc.stdout, stderr=exc.stderr,
        )
        raise PreviewError(
            f"RocksmithToolkit did not finish the {stage.lower()} step within {timeout} seconds.",
            archive=archive, stage=stage, diagnostics=diagnostics,
        ) from exc
    except OSError as exc:
        diagnostics = _tool_diagnostics(toolkit, stage, timeout=timeout)
        raise PreviewError(
            f"Could not start the RocksmithToolkit tool for {stage.lower()}: {exc}",
            archive=archive, stage=stage, diagnostics=diagnostics,
        ) from exc


def _failure(message: str, source: Path, toolkit: Path, stage: str,
             diagnostics: str = "") -> PreviewError:
    return PreviewError(message, archive=source, stage=stage,
                        diagnostics=diagnostics or f"Toolkit={toolkit}\nstage={stage}")


def prepare_preview(archive: str | Path, toolkit_root: str | Path,
                    seconds: int = PREVIEW_SECONDS, *,
                    track_count: int | None = None) -> PreviewAsset:
    """Extract and convert uniquely identifiable audio; never writes beside the PSARC."""

    source_input = Path(archive)
    if source_input.is_symlink():
        raise PreviewError("Song archive is a symbolic link and cannot be previewed safely.",
                           archive=source_input, stage="Archive validation")
    try:
        source = source_input.resolve(strict=True)
    except OSError as exc:
        raise PreviewError(f"Song archive cannot be opened: {exc}", archive=source_input,
                           stage="Archive validation") from exc
    try:
        toolkit = Path(toolkit_root).resolve(strict=True)
    except OSError as exc:
        raise PreviewError(f"RocksmithToolkit folder cannot be opened: {exc}", archive=source,
                           stage="Toolkit discovery") from exc
    if source.suffix.casefold() != ".psarc" or not source.is_file():
        raise _failure("Choose one existing Rocksmith .psarc archive.", source, toolkit, "Archive validation")
    if isinstance(seconds, bool) or not isinstance(seconds, int) or not 1 <= seconds <= 60:
        raise _failure("Preview length must be a whole number from 1 to 60 seconds.", source, toolkit,
                       "Preview validation")
    if track_count is not None and (isinstance(track_count, bool) or not isinstance(track_count, int) or track_count < 1):
        track_count = None

    packer = toolkit / "packer.exe"
    ogg_cut = toolkit / "tools" / "oggCut.exe"
    ogg_decode = toolkit / "tools" / "oggdec.exe"
    codebooks = toolkit / "tools" / "packed_codebooks_aoTuV_603.bin"
    if not codebooks.is_file():
        codebooks = toolkit / "tools" / "packed_codebooks.bin"
    required = (packer, toolkit / "tools" / "ww2ogg.exe", toolkit / "tools" / "revorb.exe",
                codebooks, ogg_cut, ogg_decode)
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise _failure("Toolkit preview components are missing: " + ", ".join(missing),
                       source, toolkit, "Toolkit validation")

    work = Path(tempfile.mkdtemp(prefix=_TEMP_PREFIX))
    stage = "Unpack"
    try:
        extracted = work / "extracted"
        extracted.mkdir()
        unpack = _run_tool(
            [str(packer), "-u", "-d", f"-i={source}", f"-o={extracted}", "-v=RS2014", "-f=Pc"],
            cwd=toolkit, toolkit=toolkit, archive=source, stage=stage, timeout=300,
        )
        if unpack.returncode != 0:
            diagnostics = _tool_diagnostics(
                toolkit, stage, timeout=300, stdout=unpack.stdout, stderr=unpack.stderr,
                returncode=unpack.returncode,
            )
            raise _failure("RocksmithToolkit reported an error while unpacking this archive.",
                           source, toolkit, stage, diagnostics)

        stage = "Audio enumeration"
        extracted_files = [path for path in extracted.rglob("*") if path.is_file()]
        audio = sorted(
            (path for path in extracted_files if path.suffix.casefold() == ".ogg"),
            key=lambda path: str(path.relative_to(extracted)).casefold(),
        )
        file_types = Counter(path.suffix.casefold() or "<no extension>" for path in extracted_files)
        type_summary = ", ".join(f"{extension}={count}" for extension, count in file_types.most_common(12))
        extract_summary = (
            f"extracted_files={len(extracted_files)}; extracted_bytes="
            f"{sum(path.stat().st_size for path in extracted_files)}; file_types={type_summary or 'none'}"
        )
        unpack_diagnostics = _tool_diagnostics(
            toolkit, "Unpack", timeout=300, stdout=unpack.stdout, stderr=unpack.stderr,
            returncode=unpack.returncode,
        )
        if not audio:
            raise _failure(
                "Toolkit unpacked the archive but produced no decoded .ogg audio for preview. "
                "This does not prove the PSARC is corrupt or that Rocksmith cannot play it. "
                "The archive was not modified. Open Archive details to inspect its listed songs.",
                source, toolkit, stage, f"{extract_summary}\n{unpack_diagnostics}",
            )
        if len(audio) != 1:
            names = ", ".join(str(path.relative_to(extracted)) for path in audio[:12])
            if len(audio) > 12:
                names += f", ... and {len(audio) - 12} more"
            raise _failure(
                f"Toolkit produced {len(audio)} OGG audio files, and the app cannot safely map one to the selected song. "
                "No arbitrary track was played.",
                source, toolkit, stage, f"{extract_summary}\naudio_candidates={names}\n{unpack_diagnostics}",
            )
        source_ogg = audio[0]
        if "preview" in source_ogg.stem.casefold():
            raise _failure(
                "The only decoded audio is labeled as a short menu preview, not a uniquely identified song sample.",
                source, toolkit, stage,
                f"{extract_summary}\naudio_candidate={source_ogg.relative_to(extracted)}\n{unpack_diagnostics}",
            )
        if track_count is not None and track_count != 1:
            raise _failure(
                f"This archive lists {track_count} song records but has only one decoded audio file; "
                "the app cannot prove which song it represents. No audio was played.",
                source, toolkit, stage,
                f"{extract_summary}\naudio_candidate={source_ogg.relative_to(extracted)}\n{unpack_diagnostics}",
            )

        clipped = work / "song_sample.ogg"
        wav_path = work / "song_sample.wav"
        stage = "Clip audio"
        clip = _run_tool(
            [str(ogg_cut), "-s", "0", "-l", str(seconds * 1000), str(source_ogg), str(clipped)],
            cwd=toolkit / "tools", toolkit=toolkit, archive=source, stage=stage, timeout=60,
        )
        if clip.returncode != 0 or not clipped.is_file() or clipped.stat().st_size == 0:
            diagnostics = _tool_diagnostics(
                toolkit, stage, timeout=60, stdout=clip.stdout, stderr=clip.stderr,
                returncode=clip.returncode,
            )
            raise _failure("Toolkit could not trim the selected audio sample.", source, toolkit, stage, diagnostics)

        stage = "Decode audio"
        decode = _run_tool(
            [str(ogg_decode), "-Q", "-o", str(wav_path), str(clipped)],
            cwd=toolkit / "tools", toolkit=toolkit, archive=source, stage=stage, timeout=60,
        )
        if decode.returncode != 0 or not wav_path.is_file():
            diagnostics = _tool_diagnostics(
                toolkit, stage, timeout=60, stdout=decode.stdout, stderr=decode.stderr,
                returncode=decode.returncode,
            )
            raise _failure("Toolkit could not decode the audio sample to WAV.", source, toolkit, stage, diagnostics)

        stage = "WAV validation"
        try:
            with wave.open(str(wav_path), "rb") as stream:
                channels = stream.getnchannels()
                sample_width = stream.getsampwidth()
                rate = stream.getframerate()
                duration = stream.getnframes() / max(rate, 1)
        except (OSError, wave.Error) as exc:
            raise _failure(f"Decoded WAV could not be read: {exc}", source, toolkit, stage,
                           f"wav_path={wav_path}") from exc
        if channels not in (1, 2) or sample_width != 2 or not 0 < duration <= seconds + 1:
            raise _failure(
                f"Converted audio has unsupported format or length ({channels} channels, "
                f"{sample_width * 8}-bit, {duration:.1f}s).",
                source, toolkit, stage, f"wav_path={wav_path}; {extract_summary}",
            )
        return PreviewAsset(wav_path, work, duration)
    except Exception as exc:
        cleanup_preview_directory(work)
        if isinstance(exc, PreviewError):
            raise
        diagnostics = f"Toolkit={toolkit}\nstage={stage}\nexception={type(exc).__name__}: {exc}"
        raise _failure(f"Preview preparation failed during {stage.lower()}: {exc}",
                       source, toolkit, stage, diagnostics) from exc


def cleanup_preview_directory(path: str | Path) -> None:
    """Remove only a directory created by this module directly under temp."""

    candidate = Path(path).resolve(strict=False)
    temp_root = Path(tempfile.gettempdir()).resolve(strict=True)
    if (candidate.parent == temp_root
            and candidate.name.startswith(_TEMP_PREFIX)
            and candidate.is_dir()):
        shutil.rmtree(candidate)
