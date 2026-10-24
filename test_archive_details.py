"""Read-only presentation tests for multi-song PSARC details."""

from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace

from archive_details import (
    archive_search_text,
    archive_table_values,
    duplicate_song_keys,
    archive_track_rows,
    format_archive_move_line,
)


class ArchiveDetailsTests(unittest.TestCase):
    def setUp(self):
        self.metadata = {
            "songs": [
                {"artist": "Avenged Sevenfold", "title": "Afterlife", "song_key": "Afterlife"},
                {"artist": "Avenged Sevenfold", "title": "Beast and the Harlot", "song_key": "BeastAndHarlot"},
                {"artist": "Pantera", "title": "Walk", "song_key": "WalkPantera"},
            ]
        }

    def test_pack_row_is_compact_and_search_still_includes_every_track(self):
        artist, title = archive_table_values(self.metadata)
        self.assertEqual("Multiple artists (2)", artist)
        self.assertEqual("Afterlife (+2 more tracks)", title)
        self.assertIn("Beast and the Harlot", archive_search_text(self.metadata))
        self.assertIn("Pantera", archive_search_text(self.metadata))

    def test_single_track_keeps_full_artist_and_title(self):
        artist, title = archive_table_values({"songs": [self.metadata["songs"][0]]})
        self.assertEqual(("Avenged Sevenfold", "Afterlife"), (artist, title))

    def test_track_favorites_are_unknown_without_current_profile_keys(self):
        rows = archive_track_rows(self.metadata, favorite_keys=None)
        self.assertEqual(["Unknown"] * 3, [row.favorite_status for row in rows])

    def test_track_favorites_follow_current_profile_song_keys(self):
        rows = archive_track_rows(self.metadata, favorite_keys={"afterlife", "walkpantera"})
        self.assertEqual(["Favorite", "Not favorite", "Favorite"],
                         [row.favorite_status for row in rows])

    def test_duplicate_song_keys_are_reported_without_collapsing_track_records(self):
        metadata = {"songs": [
            {"artist": "Pantera", "title": "Walk", "song_key": "WalkPantera"},
            {"artist": "Pantera", "title": "Walk (Drop D)", "song_key": "walkpantera"},
        ]}
        self.assertEqual((("WalkPantera", 2),), duplicate_song_keys(metadata))
        self.assertEqual(2, len(archive_track_rows(metadata, {"walkpantera"})))

    def test_move_review_line_names_entire_archive_and_record_count(self):
        path = Path("C:/Rocksmith/dlc/compatibility.psarc")
        line = format_archive_move_line(path, 4 * 1048576, self.metadata)
        self.assertIn(str(path), line)
        self.assertIn("3 metadata-listed song records", line)
        self.assertIn("entire PSARC moves", line)
        self.assertIn("Beast and the Harlot", line)

    def test_move_review_line_marks_unknown_track_count(self):
        line = format_archive_move_line("/game/unknown.psarc", 0, None)
        self.assertIn("track list unknown", line)
        self.assertIn("entire PSARC moves", line)


if __name__ == "__main__":
    unittest.main()
