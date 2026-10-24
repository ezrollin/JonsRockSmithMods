"""Persistent, read-only archive title cache."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from metadata.metadata import extract_metadata


class MetadataCache:
    def __init__(self, path: Path):
        self.path = path
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                self.entries = data if isinstance(data, dict) else {}
            except (OSError, json.JSONDecodeError):
                self.entries = {}
        else:
            self.entries = {}

    def get(self, archive: Path) -> dict | None:
        key = str(archive.resolve(strict=False)).casefold()
        entry = self.entries.get(key)
        try:
            stat = archive.stat()
        except OSError:
            return None
        if (
            entry
            and entry.get("size") == stat.st_size
            and entry.get("mtime_ns") == stat.st_mtime_ns
            and entry.get("device") == stat.st_dev
            and entry.get("file_id") == stat.st_ino
        ):
            return entry.get("metadata")
        return None

    def scan(self, archive: Path, toolkit: Path | None = None, *, save: bool = True) -> dict:
        before = archive.stat()
        metadata = extract_metadata(archive, toolkit=toolkit)
        stat = archive.stat()
        before_identity = (before.st_size, before.st_mtime_ns, before.st_dev, before.st_ino)
        after_identity = (stat.st_size, stat.st_mtime_ns, stat.st_dev, stat.st_ino)
        if before_identity != after_identity:
            raise RuntimeError(f"Archive changed during title scan: {archive}")
        key = str(archive.resolve(strict=False)).casefold()
        self.entries[key] = {
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "device": stat.st_dev,
            "file_id": stat.st_ino,
            "metadata": metadata,
        }
        if save:
            self.save()
        return metadata

    def relocate(self, source: Path, target: Path, *, save: bool = True) -> None:
        old_key = str(source.resolve(strict=False)).casefold()
        new_key = str(target.resolve(strict=False)).casefold()
        entry = self.entries.pop(old_key, None)
        if entry is not None:
            self.entries[new_key] = entry
            if save:
                self.save()

    def forget(self, archive: Path) -> None:
        if self.entries.pop(str(archive.resolve(strict=False)).casefold(), None) is not None:
            self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".metadata-", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(self.entries, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
