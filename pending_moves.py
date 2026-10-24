"""Persistent, identity-checked queue for reversible post-session archive moves."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from song_store import FileIdentity, SongFile, SongStore, StoreError, game_is_running


QUEUE_VERSION = 1
MAX_QUEUE_ITEMS = 1000
MAX_QUEUE_BYTES = 1_000_000


@dataclass(frozen=True)
class PendingMove:
    game_root: str
    review_root: str
    relative_path: str
    identity: FileIdentity
    queued_at: str


@dataclass(frozen=True)
class PendingMoveResult:
    completed_count: int
    remaining_count: int
    moved_paths: tuple[tuple[Path, Path], ...]

    @property
    def moved_count(self) -> int:
        return len(self.moved_paths)


def load_pending_moves(queue_path: str | Path) -> list[PendingMove]:
    path = Path(queue_path)
    if not path.exists():
        return []
    try:
        if path.is_symlink():
            raise StoreError("Queued-removal file cannot be a symbolic link")
        if path.stat().st_size > MAX_QUEUE_BYTES:
            raise StoreError("Queued-removal file exceeds the supported size limit")
        document = json.loads(path.read_text(encoding="utf-8"))
    except StoreError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StoreError(f"Cannot read queued removals: {exc}") from exc
    if (not isinstance(document, dict) or type(document.get("version")) is not int
            or document.get("version") != QUEUE_VERSION
            or not isinstance(document.get("items"), list)):
        raise StoreError("Queued-removal file format is unsupported")
    if len(document["items"]) > MAX_QUEUE_ITEMS:
        raise StoreError("Queued-removal file contains too many archives")

    result: list[PendingMove] = []
    seen: set[tuple[str, str, str]] = set()
    for index, raw in enumerate(document["items"]):
        if not isinstance(raw, dict):
            raise StoreError(f"Queued-removal entry {index + 1} is invalid")
        game_root = _absolute_root(raw.get("game_root"), "game")
        review_root = _absolute_root(raw.get("review_root"), "Do Not Want")
        relative_path = _safe_relative_archive(raw.get("relative_path"))
        identity_value = raw.get("identity")
        if (not isinstance(identity_value, list) or len(identity_value) != 4
                or any(type(part) is not int or part < 0 for part in identity_value)):
            raise StoreError(f"Queued-removal identity is invalid for {relative_path}")
        queued_at = raw.get("queued_at")
        if not isinstance(queued_at, str):
            raise StoreError(f"Queued-removal timestamp is invalid for {relative_path}")
        try:
            datetime.fromisoformat(queued_at)
        except ValueError as exc:
            raise StoreError(f"Queued-removal timestamp is invalid for {relative_path}") from exc
        item = PendingMove(
            game_root, review_root, relative_path, tuple(identity_value), queued_at
        )
        key = _move_key(item)
        if key in seen:
            raise StoreError(f"Duplicate archive in queued removals: {relative_path}")
        seen.add(key)
        result.append(item)
    return result


def queue_song_moves(
    queue_path: str | Path,
    store: SongStore,
    candidates: list[SongFile],
) -> int:
    """Persist selected archive identities without touching game files."""

    if not candidates:
        raise StoreError("Check one or more active archives before queueing a removal")
    current = {
        row.relative_path: row
        for row in store.inventory()
        if row.location == "Active"
    }
    existing = load_pending_moves(queue_path)
    existing_keys = {_move_key(item) for item in existing}
    now = datetime.now(timezone.utc).isoformat()
    additions: list[PendingMove] = []
    candidate_paths: set[str] = set()
    game_root = str(store.game_root)
    review_root = str(store.review_root)
    for candidate in candidates:
        relative_path = _safe_relative_archive(candidate.relative_path)
        if relative_path in candidate_paths:
            raise StoreError(f"Archive was selected more than once: {relative_path}")
        candidate_paths.add(relative_path)
        if candidate.location != "Active" or candidate.identity is None:
            raise StoreError(f"Only verified active archives can be queued: {relative_path}")
        verified = current.get(relative_path)
        if verified is None or verified.identity != candidate.identity:
            raise StoreError(f"Archive changed since it was selected; refresh before queueing: {relative_path}")
        item = PendingMove(game_root, review_root, relative_path, candidate.identity, now)
        key = _move_key(item)
        if key in existing_keys:
            raise StoreError(f"Archive is already queued: {relative_path}")
        existing_keys.add(key)
        additions.append(item)
    _write_pending_moves(queue_path, [*existing, *additions])
    return len(existing) + len(additions)


def cancel_pending_moves(queue_path: str | Path) -> int:
    """Cancel queued operations; never changes song archives."""

    path = Path(queue_path)
    items = load_pending_moves(path)
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        raise StoreError(f"Could not cancel queued removals: {exc}") from exc
    return len(items)


def process_pending_moves(
    queue_path: str | Path,
    running_check: Callable[[], bool] = game_is_running,
) -> PendingMoveResult:
    """Move verified archives only after Rocksmith is closed; retain failures for review."""

    path = Path(queue_path)
    items = load_pending_moves(path)
    if not items:
        return PendingMoveResult(0, 0, ())
    if running_check():
        raise StoreError("Rocksmith is still running; queued archives remain untouched")

    remaining = list(items)
    moved_paths: list[tuple[Path, Path]] = []
    completed = 0
    store_cache: dict[tuple[str, str], tuple[SongStore, dict[str, SongFile]]] = {}
    for item in items:
        store_key = (os.path.normcase(item.game_root), os.path.normcase(item.review_root))
        cached = store_cache.get(store_key)
        if cached is None:
            store = SongStore(Path(item.game_root), Path(item.review_root), running_check)
            rows_by_path: dict[str, SongFile] = {}
            for row in store.inventory():
                if row.relative_path in rows_by_path:
                    raise StoreError(f"Duplicate archive path in library: {row.relative_path}")
                rows_by_path[row.relative_path] = row
            cached = (store, rows_by_path)
            store_cache[store_key] = cached
        store, rows_by_path = cached
        row = rows_by_path.get(item.relative_path)
        if row is None:
            raise StoreError(
                f"Queued archive is missing; no queued move made for {item.relative_path}"
            )
        if row.identity != item.identity:
            raise StoreError(
                f"Queued archive changed after selection; no move made for {item.relative_path}. "
                "Cancel this queue entry and refresh the library."
            )
        source = store.dlc_root / item.relative_path
        target = store.songs_root / item.relative_path
        if row.location == "Active":
            store.move_to_review(
                [item.relative_path],
                expected_identities={item.relative_path: item.identity},
            )
            moved_paths.append((source, target))
            rows_by_path[item.relative_path] = SongFile(
                item.relative_path, target, row.size, "Do Not Want", row.identity
            )
        elif row.location != "Do Not Want":
            raise StoreError(f"Queued archive has an unsupported location: {item.relative_path}")

        remaining.remove(item)
        _write_pending_moves(path, remaining)
        completed += 1
    return PendingMoveResult(completed, len(remaining), tuple(moved_paths))


def _absolute_root(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise StoreError(f"Queued {label} folder is invalid")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise StoreError(f"Queued {label} folder must be an absolute path")
    return str(path.resolve(strict=False))


def _safe_relative_archive(value: object) -> str:
    if (not isinstance(value, str) or not value or "\x00" in value
            or value.startswith(("/", "\\"))):
        raise StoreError(f"Invalid queued archive path: {value}")
    relative = Path(value)
    if (relative.is_absolute() or relative.drive or relative.root or ":" in value
            or ".." in relative.parts
            or relative.suffix.casefold() != ".psarc"):
        raise StoreError(f"Invalid queued archive path: {value}")
    return relative.as_posix()


def _move_key(item: PendingMove) -> tuple[str, str, str]:
    return (
        os.path.normcase(item.game_root),
        os.path.normcase(item.review_root),
        item.relative_path.casefold(),
    )


def _write_pending_moves(queue_path: str | Path, items: list[PendingMove]) -> None:
    path = Path(queue_path)
    if len(items) > MAX_QUEUE_ITEMS:
        raise StoreError("Queued-removal file would contain too many archives")
    if not items:
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise StoreError(f"Could not update queued removals: {exc}") from exc
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise StoreError(f"Could not create queued-removal folder: {exc}") from exc
    document = {
        "version": QUEUE_VERSION,
        "items": [
            {
                "game_root": item.game_root,
                "review_root": item.review_root,
                "relative_path": item.relative_path,
                "identity": list(item.identity),
                "queued_at": item.queued_at,
            }
            for item in items
        ],
    }
    descriptor, temporary = tempfile.mkstemp(
        prefix=".pending-moves-", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(document, stream, indent=2, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        raise StoreError(f"Could not save queued removals: {exc}") from exc
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
