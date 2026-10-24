"""Preserve unrelated INI content while replacing one or more guarded settings."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime
from pathlib import Path

from song_store import StoreError

MAX_INI_BYTES = 1024 * 1024


def read_ini_text(path: Path) -> tuple[bytes, str, str, str]:
    path = Path(path)
    if path.is_symlink():
        raise StoreError(f"Refusing a linked settings file: {path}")
    if path.exists() and path.stat().st_size > MAX_INI_BYTES:
        raise StoreError(f"Settings file exceeds the 1 MB limit: {path}")
    data = path.read_bytes() if path.exists() else b""
    encoding = "utf-8-sig" if data.startswith(b"\xef\xbb\xbf") else "utf-8"
    try:
        text = data.decode(encoding)
    except UnicodeDecodeError:
        encoding = "cp1252"
        text = data.decode(encoding)
    return data, text, encoding, "\r\n" if "\r\n" in text else "\n"


def ini_values(text: str) -> dict[tuple[str, str], str]:
    section = ""
    values = {}
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith((";", "#")):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1].casefold()
        elif "=" in line:
            key, value = line.split("=", 1)
            values[(section, key.strip().casefold())] = value.strip()
    return values


def replace_ini_key(text: str, section: str, key: str, value: str | None, newline: str) -> str:
    """Replace every duplicate occurrence so readers cannot find a stale later value."""
    lines = text.splitlines(keepends=True)
    output = []
    in_section = False
    section_seen = False
    changed = False
    insertion_index = None
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if in_section:
                insertion_index = len(output)
            in_section = stripped[1:-1].casefold() == section.casefold()
            section_seen |= in_section
        elif in_section and not stripped.startswith((";", "#")) and "=" in line:
            name = line.split("=", 1)[0].strip()
            if name.casefold() == key.casefold():
                if value is not None:
                    output.append(f"{key} = {value}{newline}")
                changed = True
                continue
        output.append(line)
    if changed or value is None:
        return "".join(output)
    if in_section:
        insertion_index = len(output)
    if section_seen:
        index = len(output) if insertion_index is None else insertion_index
        if index > 0 and not output[index - 1].endswith(("\n", "\r")):
            output[index - 1] += newline
        output.insert(index, f"{key} = {value}{newline}")
    else:
        if output and not output[-1].endswith(("\n", "\r")):
            output.append(newline)
        output.extend([f"[{section}]{newline}", f"{key} = {value}{newline}"])
    return "".join(output)


def validate_game_ini(path: Path, expected_name: str) -> Path:
    path = Path(path)
    if path.name.casefold() != expected_name.casefold() or not (path.parent / "Rocksmith2014.exe").is_file():
        raise StoreError("Select the Rocksmith 2014 folder before saving settings")
    if path.is_symlink() or path.parent.is_symlink():
        raise StoreError(f"Refusing linked settings path: {path}")
    return path


def atomic_write_ini(path: Path, original: bytes, text: str, encoding: str) -> Path | None:
    updated = text.encode(encoding)
    if len(updated) > MAX_INI_BYTES:
        raise StoreError("Updated settings exceed the 1 MB limit")
    exists = path.exists()
    if path.is_symlink() or (path.read_bytes() if exists else b"") != original:
        raise StoreError("Settings changed while saving; reload and try again")
    if updated == original:
        return None
    backup = None
    if exists:
        if path.is_symlink() or path.read_bytes() != original:
            raise StoreError("Settings changed while saving; reload and try again")
        backup = path.with_name(f"{path.name}.{datetime.now():%Y%m%d_%H%M%S_%f}.bak")
        with backup.open("xb") as stream:
            stream.write(original)
            stream.flush()
            os.fsync(stream.fileno())
        if backup.read_bytes() != original:
            raise StoreError(f"Settings backup verification failed: {backup}")
    elif original:
        raise StoreError("Settings file disappeared while saving; reload and try again")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.stem}-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(updated)
            stream.flush()
            os.fsync(stream.fileno())
        if path.is_symlink() or (path.read_bytes() if path.exists() else b"") != original:
            raise StoreError("Settings changed while saving; reload and try again")
        os.replace(temporary, path)
        if path.read_bytes() != updated:
            raise StoreError(f"Written settings verification failed; recover from {backup}")
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return backup
