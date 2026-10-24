"""Read-only adapter selecting a helper compatible with the installed Toolkit."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from toolkit_runtime import resolve_toolkit_helper

HELPER = Path(__file__).with_name("bin") / "MetadataExtractor.exe"


def default_toolkit_directory() -> Path:
    """Return the Toolkit folder under the current Windows user's home."""

    return Path.home() / "Desktop" / "RocksmithToolkit"


def find_toolkit_path(game_root: str | Path, preferred: str | Path | None = None) -> Path:
    """Find a usable Rocksmith Toolkit root, preferring the selected game install."""

    candidates = [
        Path(game_root) / "RocksmithToolkit",
        Path(preferred) if preferred else default_toolkit_directory(),
        Path(r"C:\Program Files\Rocksmith 2014 DLC Builder"),
    ]
    seen: set[str] = set()
    checked: list[str] = []
    for candidate in candidates:
        key = str(candidate.resolve(strict=False)).casefold()
        if key in seen:
            continue
        seen.add(key)
        checked.append(str(candidate))
        if (candidate / "RocksmithToolkitLib.dll").is_file():
            return candidate.resolve(strict=True)
    searched = "\n".join(f"- {path}" for path in checked)
    raise FileNotFoundError(
        "Could not find RocksmithToolkitLib.dll. Checked these Toolkit folders:\n"
        f"{searched}\nSet up Rocksmith Toolkit, then scan titles/favorites again."
    )


def extract_metadata(
    archive: str | Path,
    toolkit: str | Path | None = None,
    helper: str | Path = HELPER,
    timeout_seconds: int = 60,
) -> dict[str, Any]:
    """Return manifest-derived song rows; never infer play counts from filenames."""
    toolkit_path = Path(toolkit) if toolkit is not None else default_toolkit_directory()
    if not (toolkit_path / "RocksmithToolkitLib.dll").is_file():
        raise FileNotFoundError(
            f"Rocksmith Toolkit library not found in {toolkit_path}; use find_toolkit_path() "
            "with the selected game folder before reading archive metadata."
        )
    helper_path = resolve_toolkit_helper(helper, toolkit_path)
    result = subprocess.run(
        [str(helper_path), str(toolkit_path), str(archive)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
    )
    if result.returncode:
        raise RuntimeError(f"Metadata helper failed ({result.returncode}): {result.stderr.strip()}")
    return json.loads(result.stdout)


def extract_difficulty_metadata(
    archive: str | Path,
    toolkit: str | Path | None = None,
    helper: str | Path = HELPER,
    timeout_seconds: int = 120,
) -> dict[str, Any]:
    """Read SNG phrase maxima on demand, bypassing the title metadata cache."""

    from metadata.difficulty import extract_difficulty_metadata as extract

    return extract(archive, toolkit, helper, timeout_seconds)
