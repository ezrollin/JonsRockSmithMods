"""Reversible Rocksmith 2014 DLC file management. No game or profile parsing."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


MANIFEST_NAME = ".jonsrocksmithmods.json"
FileIdentity = tuple[int, int, int, int]


class StoreError(RuntimeError):
    pass


def normalize_review_root(review_root: Path) -> Path:
    """Resolve a manifest-backed songs child to its review root.

    The public setting is the Do Not Want root. If the user selects the
    existing ``songs`` child instead, accept it only when the sibling manifest
    proves that this is the app-managed store. Ambiguous ``songs`` folders fail
    closed so the app never silently creates ``songs\\songs``.
    """

    root = Path(review_root).expanduser().resolve(strict=False)
    if root.name.casefold() == "songs":
        parent = root.parent
        if (parent / MANIFEST_NAME).is_file():
            return parent.resolve(strict=False)
        raise StoreError(
            f"Selected Do Not Want path ends in 'songs', but its parent has no {MANIFEST_NAME}. "
            "Select the Do Not Want root folder instead. No songs were moved."
        )
    return root


@dataclass(frozen=True)
class SongFile:
    relative_path: str
    path: Path
    size: int
    location: str
    identity: FileIdentity | None = None


def game_is_running() -> bool:
    """Fail closed if Windows cannot report the Rocksmith process state."""
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq Rocksmith2014.exe", "/NH", "/FO", "CSV"],
            capture_output=True, text=True, timeout=10, check=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise StoreError(f"Cannot check if Rocksmith is running: {exc}") from exc
    return "rocksmith2014.exe" in result.stdout.casefold()


def _inside(path: Path, directory: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(directory.resolve(strict=False))
        return True
    except ValueError:
        return False


def _relative_song(value: str) -> Path:
    relative = Path(value)
    if (not value or relative.is_absolute() or ".." in relative.parts
            or relative.suffix.casefold() != ".psarc" or value.startswith(("/", "\\"))):
        raise StoreError(f"Invalid song path: {value}")
    return relative


def _file_identity(path: Path) -> FileIdentity:
    """Return file size, modification time, volume, and file ID for change checks."""
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns, stat.st_dev, stat.st_ino


class SongStore:
    def __init__(self, game_root: Path, review_root: Path,
                 running_check: Callable[[], bool] = game_is_running):
        self.game_root = Path(game_root).resolve(strict=False)
        self.dlc_root = self.game_root / "dlc"
        self.review_root = normalize_review_root(review_root)
        self.songs_root = self.review_root / "songs"
        self.manifest_path = self.review_root / MANIFEST_NAME
        self.running_check = running_check
        if not (self.game_root / "Rocksmith2014.exe").is_file() or not self.dlc_root.is_dir():
            raise StoreError("Select a Rocksmith 2014 folder containing Rocksmith2014.exe and dlc")
        if (_inside(self.review_root, self.game_root)
                or _inside(self.game_root, self.review_root)):
            raise StoreError("Review folder must be separate from the game folder")

    def _assert_game_closed(self) -> None:
        if self.running_check():
            raise StoreError("Close Rocksmith 2014 before moving or restoring songs")

    def _read_manifest(self) -> dict:
        if not self.manifest_path.exists():
            return {"version": 1, "songs": {}}
        try:
            data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StoreError(f"Cannot read review manifest: {exc}") from exc
        if data.get("version") != 1 or not isinstance(data.get("songs"), dict):
            raise StoreError("Review manifest format is unsupported")
        for key in data["songs"]:
            _relative_song(key)
        return data

    def _write_manifest(self, data: dict) -> None:
        self.review_root.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".jonsrocksmithmods-", suffix=".tmp", dir=self.review_root)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(data, stream, indent=2, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.manifest_path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def inventory(self) -> list[SongFile]:
        manifest = self._read_manifest()["songs"]
        rows: list[SongFile] = []
        for path in self.dlc_root.rglob("*.psarc"):
            if path.is_file() and not path.is_symlink() and _inside(path, self.dlc_root):
                rel = path.relative_to(self.dlc_root).as_posix()
                identity = _file_identity(path)
                rows.append(SongFile(rel, path, identity[0], "Active", identity))
        for rel in manifest:
            path = self.songs_root / _relative_song(rel)
            if path.is_file() and not path.is_symlink() and _inside(path, self.songs_root):
                identity = _file_identity(path)
                rows.append(SongFile(rel, path, identity[0], "Do Not Want", identity))
        return sorted(rows, key=lambda row: (row.relative_path.casefold(), row.location))

    def move_to_review(
        self,
        relative_paths: list[str],
        expected_identities: dict[str, FileIdentity] | None = None,
    ) -> int:
        self._assert_game_closed()
        data = self._read_manifest()
        count = 0
        for value in dict.fromkeys(relative_paths):
            rel = _relative_song(value)
            key = rel.as_posix()
            source = self.dlc_root / rel
            target = self.songs_root / rel
            if key in data["songs"] or target.exists():
                raise StoreError(f"Already in review or destination occupied: {key}")
            if not source.is_file() or source.is_symlink() or not _inside(source, self.dlc_root):
                raise StoreError(f"Song missing or unsafe: {key}")
            expected = (expected_identities or {}).get(key)
            if expected is not None and _file_identity(source) != expected:
                raise StoreError(f"Song changed since favorite verification; no move made for: {key}")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(target))
            data["songs"][key] = {"moved_at": datetime.now(timezone.utc).isoformat(), "size": target.stat().st_size}
            try:
                self._write_manifest(data)
            except Exception:
                shutil.move(str(target), str(source))
                del data["songs"][key]
                raise
            count += 1
        return count

    def restore(self, relative_paths: list[str]) -> int:
        self._assert_game_closed()
        data = self._read_manifest()
        count = 0
        for value in dict.fromkeys(relative_paths):
            rel = _relative_song(value)
            key = rel.as_posix()
            source = self.songs_root / rel
            target = self.dlc_root / rel
            if key not in data["songs"] or not source.is_file() or source.is_symlink() or not _inside(source, self.songs_root):
                raise StoreError(f"Reviewed song missing or unsafe: {key}")
            if target.exists():
                raise StoreError(f"Game destination occupied: {key}")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(target))
            previous = data["songs"].pop(key)
            try:
                self._write_manifest(data)
            except Exception:
                shutil.move(str(target), str(source))
                data["songs"][key] = previous
                raise
            count += 1
        return count

