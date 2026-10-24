"""Read-only structural song checks. Results never authorize automatic removal."""
from __future__ import annotations

import json
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from metadata.metadata import HELPER, default_toolkit_directory
from toolkit_runtime import resolve_toolkit_helper

STATUSES = frozenset({"issue_found", "needs_review", "no_issue_found"})


@dataclass(frozen=True)
class HealthResult:
    path: Path
    status: str
    summary: str
    issues: tuple[dict[str, str], ...]
    document: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {**self.document, "archive_path": str(self.path), "status": self.status,
                "summary": self.summary, "issues": list(self.issues)}


def _identity(path: Path) -> tuple[int, int, int, int]:
    value = path.stat()
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns


def inspect_archive_health(archive: str | Path, toolkit: str | Path | None = None,
                           helper: str | Path = HELPER, timeout_seconds: int = 180) -> dict[str, Any]:
    """Inflate manifests/charts/audio read-only; no issue found is not playback proof.

    Missing embedded banks/media are review findings because official packs may
    share assets across archives. Audio headers are checked, not decoded/played.
    """
    path = Path(archive).resolve(strict=True)
    if path.suffix.casefold() != ".psarc":
        raise ValueError("Health inspection requires a .psarc archive")
    before = _identity(path)
    toolkit_path = Path(toolkit) if toolkit is not None else default_toolkit_directory()
    helper_path = resolve_toolkit_helper(helper, toolkit_path)
    result = subprocess.run(
        [str(helper_path), str(toolkit_path), "--health", str(path)], capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=timeout_seconds,
        check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if before != _identity(path):
        raise RuntimeError("Archive changed during inspection; rescan after Rocksmith and other file tools close")
    if result.returncode:
        raise RuntimeError(f"Health helper failed ({result.returncode}): {result.stderr.strip()}")
    try:
        document = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise ValueError("Health helper returned invalid JSON") from error
    _validate_health_document(document, path)
    for issue in document["issues"]:
        issue["recommended_action"] = recommended_action(issue["code"])
    document["file_identity"] = list(before)
    document["limitations"] = "Structural checks only. Audio is not decoded; game playback and external dependencies are not verified."
    return document


def recommended_action(code: str) -> str:
    if code == "missing_installed_audio_bank":
        return "Required soundbank was absent from every successfully inventoried active/base-game archive. Reinstall owned DLC through its authorized platform; charts cannot recreate missing audio. Preserve the compatibility pack."
    if code in {"external_audio_bank", "external_audio_media", "unverified_audio_references"}:
        return "Verify required base-game/DLC dependencies. Do not delete solely because shared audio is absent from this archive."
    if code == "scan_limit":
        return "Entry exceeds the bounded inspection limit. Verify playback or inspect using a compatible Toolkit; size is not a corruption verdict."
    if code in {"empty_chart", "missing_chart", "unreadable_chart", "unreadable_audio_media", "unreadable_audio_bank", "unreadable_manifest", "duplicate_entry", "invalid_audio_reference"}:
        return "Compare with a known-good backup or reinstall/redownload from the original authorized source. Review all pack tracks before reversible removal."
    return "Review package dependencies and test in Rocksmith. Preserve the original; queue reversible removal only after confirming the affected archive."


def _validate_health_document(document: Any, path: Path) -> None:
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise ValueError("Unsupported health report schema")
    if document.get("archive_path") != str(path):
        raise ValueError("Health report does not match selected archive")
    if document.get("status") not in STATUSES:
        raise ValueError("Invalid health status")
    if type(document.get("manifest_count")) is not int or document["manifest_count"] < 0:
        raise ValueError("Invalid manifest count")
    if not isinstance(document.get("arrangements"), list) or not isinstance(document.get("issues"), list):
        raise ValueError("Incomplete health report")
    for issue in document["issues"]:
        if (not isinstance(issue, dict) or issue.get("severity") not in {"error", "review"}
                or any(not isinstance(issue.get(key), str) for key in ("code", "detail", "path"))
                or not issue["code"] or not issue["detail"]):
            raise ValueError("Invalid health finding")
    for row in document["arrangements"]:
        if (not isinstance(row, dict) or type(row.get("note_count")) is not int or row["note_count"] < 0
                or any(not isinstance(row.get(key), str) for key in ("title", "artist", "song_key", "arrangement_name"))):
            raise ValueError("Invalid health arrangement")
    expected = ("issue_found" if any(i["severity"] == "error" for i in document["issues"])
                else "needs_review" if document["issues"] else "no_issue_found")
    if document["status"] != expected:
        raise ValueError("Health status contradicts findings")


def inventory_audio_banks(paths: Iterable[Path], toolkit_path: str | Path,
                          helper: str | Path = HELPER) -> dict[str, Any]:
    """One bounded read-only TOC inventory; any failure prevents absence proof."""
    paths = list(dict.fromkeys(Path(p).resolve(strict=False) for p in paths))
    if len(paths) > 5000:
        raise ValueError("Audio inventory exceeds 5000 archive limit")
    identities = {}
    failures = []
    for path in paths:
        try:
            identities[path] = _identity(path)
        except OSError as error:
            failures.append({"archive_path": str(path), "detail": str(error)})
    toolkit = Path(toolkit_path)
    executable = resolve_toolkit_helper(helper, toolkit)
    with tempfile.TemporaryDirectory(prefix="jons-audio-inventory-") as directory:
        input_path = Path(directory) / "archives.json"
        input_path.write_text(json.dumps([str(p) for p in paths]), encoding="utf-8")
        result = subprocess.run([str(executable), str(toolkit), "--audio-inventory", str(input_path)],
                                capture_output=True, text=True, encoding="utf-8", errors="replace",
                                timeout=120, check=False,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode:
        raise RuntimeError(f"Audio inventory helper failed ({result.returncode}): {result.stderr.strip()}")
    document = json.loads(result.stdout)
    if (not isinstance(document, dict) or document.get("schema_version") != 1
            or document.get("requested_count") != len(paths)
            or not isinstance(document.get("banks"), list) or len(document["banks"]) > 20000
            or not isinstance(document.get("failures"), list)
            or any(not isinstance(name, str) or len(name) > 1024
                   or not name.casefold().startswith("audio/windows/") or not name.casefold().endswith(".bnk")
                   for name in document["banks"])
            or any(not isinstance(row, dict) or not isinstance(row.get("archive_path"), str)
                   or not isinstance(row.get("detail"), str) for row in document["failures"])):
        raise ValueError("Invalid audio inventory report")
    failures.extend(document["failures"])
    for path, before in identities.items():
        try:
            if _identity(path) != before:
                failures.append({"archive_path": str(path), "detail": "Archive changed during audio inventory"})
        except OSError as error:
            failures.append({"archive_path": str(path), "detail": str(error)})
    return {"complete": not failures, "archive_count": len(paths),
            "banks": {name.casefold() for name in document["banks"]}, "failures": failures}


def _installation_audio_inventory(paths: list[Path], toolkit_path: str | Path) -> dict[str, Any]:
    roots = set()
    for path in paths:
        for parent in path.parents:
            if parent.name.casefold() == "dlc":
                roots.add(parent.parent)
                break
    if len(roots) != 1:
        return {"complete": False, "archive_count": 0, "banks": set(),
                "failures": [{"detail": "Cannot identify one installation root from active DLC paths"}]}
    root = roots.pop()
    excluded = {"donotwant", "do not want", "backups", "backup", "originals", "jonsrocksmithmods", "splitpacks"}
    candidates = set(paths)
    candidates.update(root.glob("*.psarc"))
    candidates.update(p for p in (root / "dlc").rglob("*.psarc")
                      if not any(part.casefold() in excluded for part in p.relative_to(root).parts[:-1])
                      and not p.is_symlink())
    def active(path: Path) -> bool:
        try:
            relative = path.relative_to(root)
        except ValueError:
            return False
        return (not any(part.casefold() in excluded for part in relative.parts[:-1])
                and not path.is_symlink()
                and not path.name.casefold().endswith((".org.psarc", ".backup.psarc", ".bak.psarc")))
    return inventory_audio_banks(sorted(path for path in candidates if active(path)), toolkit_path)


def _bank_path(value: str) -> str:
    name = value.replace("\\", "/").lstrip("/").casefold()
    return name if name.startswith("audio/") else "audio/windows/" + name


def _apply_audio_inventory(document: dict[str, Any], inventory: dict[str, Any]) -> None:
    issues = document["issues"]
    missing = []
    for issue in issues:
        if issue["code"] != "external_audio_bank":
            continue
        name = issue["path"].casefold()
        if name in inventory["banks"]:
            issue["detail"] += " Matching bank path exists elsewhere in the installation; decoding/playback remains unverified."
        elif inventory["complete"]:
            issue["code"] = "missing_installed_audio_bank"
            issue["detail"] = "Required soundbank path was absent from all successfully inventoried active/base-game archives. Missing dependency; this does not establish archive corruption."
            issue["recommended_action"] = recommended_action(issue["code"])
            missing.append(name)
    arrangements = document["arrangements"]
    affected = sorted({row["song_key"] for row in arrangements
                       if any(row.get(field) and _bank_path(row[field]) in missing
                              for field in ("song_bank", "preview_bank"))})
    tracks = {(row["song_key"], row["artist"], row["title"]) for row in arrangements}
    document["track_count"] = len(tracks)
    document["unique_song_key_count"] = len({row["song_key"] for row in arrangements})
    document["instrumental_arrangement_count"] = len(arrangements)
    document["audio_inventory"] = {key: value for key, value in inventory.items() if key != "banks"}
    document["missing_installed_audio_bank_count"] = len(missing)
    document["affected_song_keys"] = affected


def scan_library(rows: Iterable[Any], toolkit_path: str | Path,
                 progress_callback: Callable[[int, int, Path], None] | None = None,
                 cancel_event: Any = None) -> list[HealthResult]:
    """Scan supplied paths/SongFile rows; keep failed scans visible, never mutate songs."""
    paths = list(dict.fromkeys(Path(getattr(row, "path", row)).resolve(strict=False) for row in rows))
    reports: list[HealthResult] = []
    if not paths or (cancel_event is not None and cancel_event.is_set()):
        return reports
    if progress_callback is not None:
        progress_callback(0, len(paths), Path("Indexing installed audio dependencies"))
    if cancel_event is not None and cancel_event.is_set():
        return reports
    try:
        inventory = _installation_audio_inventory(paths, toolkit_path)
    except Exception:
        import traceback
        import logging
        logging.exception("Installation audio inventory incomplete")
        inventory = {"complete": False, "archive_count": 0, "banks": set(),
                     "failures": [{"detail": traceback.format_exc()}]}
    for index, path in enumerate(paths):
        if cancel_event is not None and cancel_event.is_set():
            break
        try:
            document = inspect_archive_health(path, toolkit_path)
            _apply_audio_inventory(document, inventory)
            issues = tuple(document["issues"])
            status = document["status"]
            summary = {"issue_found": "Structural problems found; inspect details before replacement/removal.",
                       "needs_review": "Needs review; shared dependencies or unsupported content may be valid.",
                       "no_issue_found": "No structural issue found; playback has not been verified."}[status]
            if document["missing_installed_audio_bank_count"]:
                count = document["missing_installed_audio_bank_count"]
                affected = len(document["affected_song_keys"])
                summary = f"Missing audio dependencies: {count} banks absent from installed archives; {affected} song keys affected."
                if status == "issue_found":
                    summary += " Additional structural problems found."
        except Exception as error:
            # Full exception text stays in the report for GUI export/logging.
            import traceback
            import logging
            logging.exception("Song health inspection incomplete: archive=%s toolkit=%s", path, toolkit_path)
            issues = ({"severity": "review", "code": "inspection_failed", "path": str(path),
                       "detail": traceback.format_exc(), "recommended_action": recommended_action("inspection_failed")},)
            status = "needs_review"
            summary = f"Inspection incomplete: {type(error).__name__}: {error}"
            document = {"schema_version": 1, "archive_path": str(path), "manifest_count": 0,
                        "arrangements": [], "issues": list(issues), "status": status}
        reports.append(HealthResult(path, status, summary, issues, document))
        if progress_callback is not None:
            progress_callback(index + 1, len(paths), path)
    return reports
