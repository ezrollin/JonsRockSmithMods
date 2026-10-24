"""Collect verified difficulty metadata without letting one bad archive abort a batch.

This module performs no profile writes. Ambiguous charts are excluded, never
merged by choosing a larger phrase maximum. The caller checks library identity
before applying ``arrangements`` in one guarded profile transaction.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from favorites.profile import SongDifficultyUpdateResult
from metadata.difficulty import _validate_difficulty_document
from song_store import SongFile


@dataclass(frozen=True)
class DifficultyArchiveResult:
    path: Path
    relative_path: str
    arrangement_ids: tuple[str, ...]
    error: str = ""
    title: str = ""

    @property
    def succeeded(self) -> bool:
        return not self.error


@dataclass(frozen=True)
class DifficultyBatch:
    arrangements: dict[str, dict[str, int]]
    archives: tuple[DifficultyArchiveResult, ...]

    @property
    def failed_archives(self) -> tuple[DifficultyArchiveResult, ...]:
        return tuple(item for item in self.archives if item.error)

    @property
    def successful_archive_count(self) -> int:
        return sum(item.succeeded for item in self.archives)

    @property
    def failed_archive_count(self) -> int:
        return len(self.failed_archives)

    @property
    def status(self) -> str:
        if not self.arrangements:
            return "FAILED"
        return "PARTIAL" if self.failed_archives else "SUCCESS"

    def details(self) -> str:
        lines = [
            f"Metadata {self.status}: {self.successful_archive_count} archive(s) ready, "
            f"{self.failed_archive_count} failed/skipped; {len(self.arrangements)} verified arrangement(s)."
        ]
        lines.extend(f"{item.title or item.relative_path}\n  {item.path}\n  {item.error}" for item in self.failed_archives)
        return "\n".join(lines)


def collect_global_difficulty(
    rows: Iterable[SongFile],
    toolkit: str | Path,
    extractor: Callable,
    progress: Callable[[int, int, SongFile], None] | None = None,
    title_lookup: Callable[[Path], str] | None = None,
) -> DifficultyBatch:
    """Collect current verified maps and every archive failure for later review.

    ``progress(index, total, row)`` is called before each extraction. Extractor
    uses ``extractor(row.path, toolkit=toolkit)``. Empty/failed batches return
    their complete diagnostics; callers must refuse writes when maps are empty.
    A warning skips the whole archive. Differing charts sharing an arrangement
    ID exclude all involved archives, even if each archive passed alone.
    """
    selected = list(rows)
    results: list[DifficultyArchiveResult] = []
    maps_by_archive: list[dict[str, dict[str, int]]] = []
    owners: dict[str, list[int]] = {}
    conflict_messages: dict[int, list[str]] = {}
    for index, row in enumerate(selected):
        try:
            title = title_lookup(row.path) if title_lookup is not None else ""
            title = title if isinstance(title, str) else ""
        except Exception:
            # A title-cache failure cannot invalidate independently verified SNG data.
            title = ""
        if progress is not None:
            progress(index + 1, len(selected), row)
        try:
            document = extractor(row.path, toolkit=toolkit)
            _validate_difficulty_document(document, row.path.resolve(strict=True))
            if document["warnings"]:
                raise ValueError("; ".join(document["warnings"]))
            records = document["arrangements"]
            if not records:
                raise ValueError("No instrumental arrangements with verified phrase maxima")
            maps = {item["arrangement_id"]: dict(item["phrase_max_difficulties"]) for item in records}
            results.append(DifficultyArchiveResult(row.path, row.relative_path, tuple(maps), title=title))
            maps_by_archive.append(maps)
            for key in maps:
                owners.setdefault(key.casefold(), []).append(index)
        except Exception as error:
            # Preserve archive-specific extractor/parse failures so healthy archives
            # can still be applied. Process interruption is not swallowed.
            results.append(DifficultyArchiveResult(
                row.path, row.relative_path, (), f"{type(error).__name__}: {error}", title=title
            ))
            maps_by_archive.append({})
    for normalized_id, archive_indices in owners.items():
        if len(archive_indices) < 2:
            continue
        values = [
            next(value for key, value in maps_by_archive[index].items() if key.casefold() == normalized_id)
            for index in archive_indices
        ]
        # Phrase spelling is case-insensitive in the profile matching backend.
        normalized_values = [{key.casefold(): value for key, value in maxima.items()} for maxima in values]
        if all(value == normalized_values[0] for value in normalized_values[1:]):
            continue
        paths = ", ".join(results[index].relative_path for index in archive_indices)
        message = f"Conflicting installed charts for arrangement {normalized_id}: {paths}; no maximum was guessed"
        for index in archive_indices:
            conflict_messages.setdefault(index, []).append(message)
    merged: dict[str, dict[str, int]] = {}
    seen: set[str] = set()
    for index, item in enumerate(results):
        if index in conflict_messages:
            results[index] = DifficultyArchiveResult(item.path, item.relative_path, item.arrangement_ids,
                                                     "; ".join(conflict_messages[index]), title=item.title)
            continue
        for key, maxima in maps_by_archive[index].items():
            if key.casefold() not in seen:
                merged[key] = maxima
                seen.add(key.casefold())
    return DifficultyBatch(merged, tuple(results))

@dataclass(frozen=True)
class BulkDifficultyResult:
    """One guarded write plus preflight coverage; counts are archives/arrangements, not songs."""
    update: SongDifficultyUpdateResult
    batch: DifficultyBatch

    @property
    def status(self) -> str:
        return self.batch.status

    @property
    def unchanged_arrangement_count(self) -> int:
        changed = {key.casefold() for key in self.update.changed_arrangement_ids}
        unavailable = {key.casefold() for key in self.update.unavailable_arrangement_ids}
        return len({key.casefold() for key in self.batch.arrangements} - changed - unavailable)

    def summary(self) -> str:
        status = "PARTIAL SUCCESS" if self.status == "PARTIAL" else self.status
        saved = (f"Saved global level {self.update.level}/10" if self.update.backup_path is not None
                 else f"Global level {self.update.level}/10 already matches; no write needed")
        return (
            f"{status}: {saved}. {self.batch.successful_archive_count} archive(s) processed; "
            f"{self.batch.failed_archive_count} failed/skipped. "
            f"{len(self.update.changed_arrangement_ids)} arrangement(s), "
            f"{self.update.changed_phrase_count} phrase(s) changed; "
            f"{self.unchanged_arrangement_count} arrangement(s) already match; "
            f"{len(self.update.unavailable_arrangement_ids)} unplayed arrangement(s) use the starting level."
        )

    def details(self) -> str:
        return (
            self.summary() + "\n" + self.batch.details()
            + "\nSkipped saved phrase records were preserved. The global starting level and automatic "
            "difficulty option still apply to every song, including skipped/unplayed songs."
            + f"\nProfile: {self.update.profile_path}\nBackup: {self.update.backup_path or 'No write needed'}"
        )
