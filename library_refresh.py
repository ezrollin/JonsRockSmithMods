"""Background library refresh with conservative profile and metadata checks."""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from favorites import FavoriteResult, classify_archive, load_favorites
from metadata.metadata import find_toolkit_path
from metadata_cache import MetadataCache
from song_store import FileIdentity, SongFile, SongStore, StoreError


ProgressCallback = Callable[[str, str], None]


@dataclass(frozen=True)
class LibraryRefreshResult:
    rows: list[SongFile]
    favorites: FavoriteResult
    favorite_states: dict[str, str]
    profile_signature: tuple[str, int, int, str] | None
    library_signature: tuple[tuple[str, str, int, int, int, int, int], ...]
    toolkit_path: str | None
    titles_read: int
    metadata_failures: int
    metadata_ready: int
    metadata_diagnostic: str


def profile_file_signature(path: str | Path) -> tuple[str, int, int, str] | None:
    path = Path(path).expanduser()
    try:
        resolved = path.resolve(strict=True)
        digest = hashlib.sha256()
        with resolved.open("rb") as stream:
            before = os.fstat(stream.fileno())
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
            after = os.fstat(stream.fileno())
        current = resolved.stat()
    except OSError:
        return None
    signature_fields = lambda stat: (stat.st_size, stat.st_mtime_ns, stat.st_dev, stat.st_ino)
    if signature_fields(before) != signature_fields(after) or signature_fields(after) != signature_fields(current):
        return None
    return str(resolved), after.st_size, after.st_mtime_ns, digest.hexdigest()


def _row_identity(row: SongFile) -> FileIdentity:
    try:
        stat = row.path.stat()
    except OSError as exc:
        raise StoreError(f"Song changed or disappeared during refresh: {row.path}") from exc
    actual = (stat.st_size, stat.st_mtime_ns, stat.st_dev, stat.st_ino)
    if row.identity is not None and actual != row.identity:
        raise StoreError(f"Song changed or disappeared during refresh: {row.path}")
    return actual


def library_signature(
    rows: list[SongFile],
) -> tuple[tuple[str, str, int, int, int, int, int], ...]:
    snapshot = []
    for row in rows:
        identity = _row_identity(row)
        snapshot.append((row.relative_path, row.location, row.size, *identity))
    return tuple(snapshot)


def validate_move_snapshot(
    expected_profile: tuple[str, int, int, str] | None,
    current_profile: tuple[str, int, int, str] | None,
    expected_library: tuple[tuple[str, str, int, int, int, int, int], ...] | None,
    current_library: tuple[tuple[str, str, int, int, int, int, int], ...] | None,
) -> None:
    """Fail closed if profile or archive evidence changed during user review."""
    if expected_profile is None or expected_library is None:
        raise StoreError("Verified profile or archive snapshot is missing; no songs moved. Run Full refresh.")
    if current_library != expected_library:
        raise StoreError("Song library or archive identity changed after confirmation; no songs moved. Run Full refresh.")
    if current_profile != expected_profile:
        raise StoreError("Rocksmith profile changed after confirmation; no songs moved. Run Full refresh.")


def refresh_library(
    store: SongStore,
    metadata: MetadataCache,
    game_root: str | Path,
    profile_path: str | Path,
    progress: ProgressCallback | None = None,
) -> LibraryRefreshResult:
    """Refresh file inventory, changed/missing titles, and profile favorite states.

    This function performs reads only. Metadata is reused only when the cache's
    size, mtime, volume, and file ID match the archive. A changing song library
    or profile during the scan fails closed.
    """

    profile = Path(profile_path).expanduser()
    initial_profile_signature = profile_file_signature(profile)
    rows = store.inventory()
    if not isinstance(rows, list) or any(not isinstance(row, SongFile) for row in rows):
        raise StoreError("Song inventory returned invalid rows during full refresh")
    initial_library_signature = library_signature(rows)

    favorites = load_favorites(profile)
    if favorites.available and initial_profile_signature is None:
        raise StoreError("Rocksmith profile disappeared while favorites were being read")

    missing_metadata = [row for row in rows if metadata.get(row.path) is None]
    toolkit_path: Path | None = None
    metadata_diagnostic = ""
    if missing_metadata:
        try:
            toolkit_path = find_toolkit_path(game_root)
        except Exception as exc:
            metadata_diagnostic = str(exc)
            logging.exception("Toolkit lookup failed during full library refresh")

    titles_read = 0
    metadata_failures = 0
    for index, row in enumerate(rows, start=1):
        if metadata.get(row.path) is None:
            if toolkit_path is None:
                metadata_failures += 1
            else:
                try:
                    metadata.scan(row.path, toolkit_path, save=False)
                    titles_read += 1
                except Exception as exc:
                    metadata_failures += 1
                    logging.exception("Metadata scan failed during full refresh: %s", row.path)
                    if not metadata_diagnostic:
                        metadata_diagnostic = f"{row.path.name}: {exc}"
        if progress and (index % 10 == 0 or index == len(rows)):
            progress("Refreshing titles", f"{index}/{len(rows)} archives; {metadata_failures} errors")

    states = {
        row.relative_path: classify_archive(metadata.get(row.path), favorites)
        for row in rows
    }

    final_profile_signature = profile_file_signature(profile)
    if favorites.available and final_profile_signature != initial_profile_signature:
        raise StoreError("Rocksmith profile changed during refresh; favorite results were discarded. Refresh again.")

    final_rows = store.inventory()
    final_library_signature = library_signature(final_rows)
    if final_library_signature != initial_library_signature:
        raise StoreError("Song library changed during refresh; favorite results were discarded. Refresh again.")

    if titles_read:
        metadata.save()

    metadata_ready = sum(metadata.get(row.path) is not None for row in rows)
    if progress:
        progress("Refreshing favorites", f"Classified {len(rows)} archives from the selected profile")
    return LibraryRefreshResult(
        rows=rows,
        favorites=favorites,
        favorite_states=states,
        profile_signature=final_profile_signature if favorites.available else None,
        library_signature=final_library_signature,
        toolkit_path=str(toolkit_path) if toolkit_path else None,
        titles_read=titles_read,
        metadata_failures=metadata_failures,
        metadata_ready=metadata_ready,
        metadata_diagnostic=metadata_diagnostic,
    )
