"""Read-only refresh pipeline tests with isolated song/profile fixtures."""

from __future__ import annotations

import tempfile
import unittest
import os
from pathlib import Path
from unittest.mock import patch

from favorites.profile import FavoriteResult
from library_refresh import (
    library_signature,
    profile_file_signature,
    refresh_library,
    validate_move_snapshot,
)
from song_store import SongFile, StoreError


def metadata(song_key: str) -> dict:
    return {
        "song_count": 1,
        "manifest_count": 1,
        "songs": [{"song_key": song_key, "artist": "Test Artist", "title": "Test Song"}],
        "warnings": [],
    }


class FakeStore:
    def __init__(self, snapshots: list[list[SongFile]]):
        self.snapshots = snapshots
        self.calls = 0

    def inventory(self) -> list[SongFile]:
        index = min(self.calls, len(self.snapshots) - 1)
        self.calls += 1
        return self.snapshots[index]


class FakeMetadata:
    def __init__(self, entries: dict[str, dict | None]):
        self.entries = entries
        self.scanned: list[Path] = []

    @staticmethod
    def key(path: Path) -> str:
        return str(path.resolve()).casefold()

    def get(self, path: Path):
        return self.entries.get(self.key(path))

    def scan(self, path: Path, toolkit: Path):
        self.scanned.append(path)
        value = metadata(path.stem)
        self.entries[self.key(path)] = value
        return value


class LibraryRefreshTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.profile = self.root / "profile_PRFLDB"
        self.profile.write_bytes(b"profile fixture")
        self.keep = self.root / "dlc" / "keep.psarc"
        self.keep.parent.mkdir()
        self.keep.write_bytes(b"keep")
        self.other = self.root / "dlc" / "other.psarc"
        self.other.write_bytes(b"other")
        self.rows = [
            SongFile("keep.psarc", self.keep, self.keep.stat().st_size, "Active"),
            SongFile("other.psarc", self.other, self.other.stat().st_size, "Active"),
        ]
        self.favorites = FavoriteResult(
            True, frozenset({"keep.psarc"}), "Read one favorite key", self.profile
        )

    def test_full_refresh_reuses_validated_titles_and_classifies_both_states(self) -> None:
        cache = FakeMetadata({
            FakeMetadata.key(self.keep): metadata("keep.psarc"),
            FakeMetadata.key(self.other): metadata("other.psarc"),
        })
        events = []
        with patch("library_refresh.load_favorites", return_value=self.favorites):
            result = refresh_library(
                FakeStore([self.rows]), cache, self.root, self.profile,
                progress=lambda stage, detail: events.append((stage, detail)),
            )
        self.assertEqual(2, result.metadata_ready)
        self.assertEqual(0, result.titles_read)
        self.assertEqual("Favorite", result.favorite_states["keep.psarc"])
        self.assertEqual("Not favorite", result.favorite_states["other.psarc"])
        self.assertEqual(2, len(result.rows))
        self.assertTrue(events)

    def test_unavailable_profile_keeps_every_favorite_state_unknown(self) -> None:
        unavailable = FavoriteResult(False, frozenset(), "profile unreadable", self.profile)
        cache = FakeMetadata({
            FakeMetadata.key(self.keep): metadata("keep.psarc"),
            FakeMetadata.key(self.other): metadata("other.psarc"),
        })
        with patch("library_refresh.load_favorites", return_value=unavailable):
            result = refresh_library(FakeStore([self.rows]), cache, self.root, self.profile)
        self.assertTrue(all(state == "Unknown" for state in result.favorite_states.values()))
        self.assertIsNone(result.profile_signature)

    def test_library_change_during_refresh_discards_favorite_results(self) -> None:
        cache = FakeMetadata({
            FakeMetadata.key(self.keep): metadata("keep.psarc"),
            FakeMetadata.key(self.other): metadata("other.psarc"),
        })
        changed_other = SongFile("other.psarc", self.other, self.other.stat().st_size + 1, "Active")
        store = FakeStore([self.rows, [self.rows[0], changed_other]])
        with patch("library_refresh.load_favorites", return_value=self.favorites):
            with self.assertRaisesRegex(StoreError, "Song library changed"):
                refresh_library(store, cache, self.root, self.profile)

    def test_profile_change_during_refresh_discards_favorite_results(self) -> None:
        cache = FakeMetadata({
            FakeMetadata.key(self.keep): metadata("keep.psarc"),
            FakeMetadata.key(self.other): metadata("other.psarc"),
        })

        def mutate_profile(_path):
            self.profile.write_bytes(b"changed profile fixture with a different length")
            return self.favorites

        with patch("library_refresh.load_favorites", side_effect=mutate_profile):
            with self.assertRaisesRegex(StoreError, "profile changed"):
                refresh_library(FakeStore([self.rows]), cache, self.root, self.profile)

    def test_profile_content_change_is_detected_when_size_and_timestamp_are_preserved(self) -> None:
        before_stat = self.profile.stat()
        before = profile_file_signature(self.profile)
        self.profile.write_bytes(b"PROFILE fixture")
        os.utime(self.profile, ns=(before_stat.st_atime_ns, before_stat.st_mtime_ns))
        after = profile_file_signature(self.profile)
        self.assertEqual(before[:3], after[:3])
        self.assertNotEqual(before[3], after[3])

    def test_archive_replacement_with_same_size_and_timestamp_changes_library_signature(self) -> None:
        original_stat = self.other.stat()
        before = library_signature(self.rows)
        replacement = self.root / "replacement.psarc"
        replacement.write_bytes(b"OTHER")
        os.utime(replacement, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        replacement.replace(self.other)
        after = library_signature(self.rows)
        self.assertNotEqual(before, after)

    def test_move_snapshot_rejects_profile_or_archive_change_after_confirmation(self) -> None:
        profile_signature = profile_file_signature(self.profile)
        library_snapshot = library_signature(self.rows)
        with self.assertRaisesRegex(StoreError, "profile changed after confirmation"):
            validate_move_snapshot(profile_signature, None, library_snapshot, library_snapshot)
        with self.assertRaisesRegex(StoreError, "archive identity changed after confirmation"):
            validate_move_snapshot(profile_signature, profile_signature, library_snapshot, ())


if __name__ == "__main__":
    unittest.main()
