"""On-demand SNG phrase difficulty metadata reader for PSARC archives."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from metadata.metadata import HELPER, default_toolkit_directory
from toolkit_runtime import resolve_toolkit_helper


def extract_difficulty_metadata(
    archive: str | Path,
    toolkit: str | Path | None = None,
    helper: str | Path = HELPER,
    timeout_seconds: int = 120,
) -> dict[str, Any]:
    """Return phrase names and SNG maxima without using the title metadata cache.

    The caller must reject non-empty ``warnings`` before making profile writes.
    This keeps partial archive scans visible and avoids claiming complete
    difficulty coverage when an arrangement SNG could not be read.
    """

    archive_path = Path(archive).resolve(strict=True)
    if archive_path.suffix.casefold() != ".psarc":
        raise ValueError("Difficulty metadata requires a Rocksmith .psarc archive")
    toolkit_path = Path(toolkit) if toolkit is not None else default_toolkit_directory()
    if not (toolkit_path / "RocksmithToolkitLib.dll").is_file():
        raise FileNotFoundError(
            f"Rocksmith Toolkit library not found in {toolkit_path}; supply its folder before reading phrase difficulty."
        )
    helper_path = resolve_toolkit_helper(helper, toolkit_path)

    result = subprocess.run(
        [str(helper_path), str(toolkit_path), "--difficulty", str(archive_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
    )
    if result.returncode:
        raise RuntimeError(
            f"Difficulty metadata helper failed ({result.returncode}): {result.stderr.strip()}"
        )
    try:
        document = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("Difficulty metadata helper returned invalid JSON") from error
    _validate_difficulty_document(document, archive_path)
    return document


def _validate_difficulty_document(document: Any, archive_path: Path) -> None:
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise ValueError("Difficulty metadata has an unsupported schema")
    if document.get("archive_path") != str(archive_path):
        raise ValueError("Difficulty metadata does not match the selected archive")
    if type(document.get("manifest_count")) is not int or document["manifest_count"] < 1:
        raise ValueError("Difficulty metadata has no verified manifests")
    arrangements = document.get("arrangements")
    warnings = document.get("warnings")
    if not isinstance(arrangements, list) or not isinstance(warnings, list):
        raise ValueError("Difficulty metadata is incomplete")
    seen_ids: set[str] = set()
    for arrangement in arrangements:
        if not isinstance(arrangement, dict):
            raise ValueError("Difficulty metadata contains an invalid arrangement")
        arrangement_id = arrangement.get("arrangement_id")
        arrangement_name = arrangement.get("arrangement_name")
        maxima = arrangement.get("phrase_max_difficulties")
        if (not isinstance(arrangement_id, str) or not arrangement_id
                or arrangement_id != arrangement_id.strip()):
            raise ValueError("Difficulty metadata contains an invalid arrangement id")
        if arrangement_id.casefold() in seen_ids:
            raise ValueError("Difficulty metadata contains duplicate arrangement ids")
        seen_ids.add(arrangement_id.casefold())
        if not isinstance(arrangement_name, str) or not arrangement_name:
            raise ValueError("Difficulty metadata contains an invalid arrangement name")
        if not isinstance(maxima, dict) or not maxima:
            raise ValueError(
                f"Arrangement {arrangement_id} has no verified phrase maxima; rescan its archive"
            )
        normalized_names: set[str] = set()
        for phrase_name, maximum in maxima.items():
            if (not isinstance(phrase_name, str) or not phrase_name
                    or phrase_name != phrase_name.strip()
                    or type(maximum) is not int or maximum < 0):
                raise ValueError(
                    f"Arrangement {arrangement_id} has invalid phrase maximum metadata"
                )
            if phrase_name.casefold() in normalized_names:
                raise ValueError(
                    f"Arrangement {arrangement_id} contains duplicate phrase names"
                )
            normalized_names.add(phrase_name.casefold())
    if any(not isinstance(warning, str) or not warning for warning in warnings):
        raise ValueError("Difficulty metadata contains an invalid warning")
