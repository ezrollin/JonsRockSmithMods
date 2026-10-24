from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from pending_moves import (
    cancel_pending_moves,
    load_pending_moves,
    process_pending_moves,
    queue_song_moves,
)
from song_store import SongStore, StoreError


class PendingMoveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.game = root / "game"
        self.review = root / "review"
        self.queue_path = root / "user-data" / "pending_moves.json"
        (self.game / "dlc" / "artist").mkdir(parents=True)
        (self.game / "Rocksmith2014.exe").write_bytes(b"test executable")
        (self.game / "dlc" / "artist" / "song_p.psarc").write_bytes(b"original archive")
        (self.game / "dlc" / "artist" / "pack_p.psarc").write_bytes(b"multi-song pack")
        self.store = SongStore(self.game, self.review, lambda: False)

    def test_queue_persists_without_moving_while_game_runs(self) -> None:
        row = self._row("artist/song_p.psarc")
        running_store = SongStore(self.game, self.review, lambda: True)
        self.assertEqual(1, queue_song_moves(self.queue_path, running_store, [row]))
        self.assertTrue(row.path.is_file())
        self.assertEqual([row.relative_path], [item.relative_path for item in load_pending_moves(self.queue_path)])

        with self.assertRaisesRegex(StoreError, "still running"):
            process_pending_moves(self.queue_path, running_check=lambda: True)

        self.assertTrue(row.path.is_file())
        self.assertEqual(1, len(load_pending_moves(self.queue_path)))

    def test_closed_game_process_moves_whole_archive_and_queue_is_reversible(self) -> None:
        row = self._row("artist/pack_p.psarc")
        self.assertEqual(1, queue_song_moves(self.queue_path, self.store, [row]))

        result = process_pending_moves(self.queue_path, running_check=lambda: False)

        target = self.review / "songs" / row.relative_path
        self.assertEqual(1, result.completed_count)
        self.assertEqual(1, result.moved_count)
        self.assertEqual(b"multi-song pack", target.read_bytes())
        self.assertFalse(row.path.exists())
        self.assertEqual([], load_pending_moves(self.queue_path))
        self.assertEqual(1, self.store.restore([row.relative_path]))
        self.assertEqual(b"multi-song pack", row.path.read_bytes())

    def test_changed_archive_stays_in_game_and_request_stays_queued(self) -> None:
        row = self._row("artist/song_p.psarc")
        queue_song_moves(self.queue_path, self.store, [row])
        replacement = self.game / "dlc" / "replacement.psarc"
        original_stat = row.path.stat()
        replacement.write_bytes(b"changed archive")
        os.utime(replacement, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        replacement.replace(row.path)

        with self.assertRaisesRegex(StoreError, "changed after selection"):
            process_pending_moves(self.queue_path, running_check=lambda: False)

        self.assertEqual(b"changed archive", row.path.read_bytes())
        self.assertEqual(1, len(load_pending_moves(self.queue_path)))
        self.assertFalse((self.review / "songs" / row.relative_path).exists())

    def test_destination_collision_preserves_queue_and_both_files(self) -> None:
        row = self._row("artist/song_p.psarc")
        queue_song_moves(self.queue_path, self.store, [row])
        target = self.review / "songs" / row.relative_path
        target.parent.mkdir(parents=True)
        target.write_bytes(b"unrelated destination")

        with self.assertRaisesRegex(StoreError, "occupied"):
            process_pending_moves(self.queue_path, running_check=lambda: False)

        self.assertEqual(b"original archive", row.path.read_bytes())
        self.assertEqual(b"unrelated destination", target.read_bytes())
        self.assertEqual(1, len(load_pending_moves(self.queue_path)))

    def test_reconciles_archive_already_moved_before_queue_cleanup(self) -> None:
        row = self._row("artist/song_p.psarc")
        queue_song_moves(self.queue_path, self.store, [row])
        self.store.move_to_review([row.relative_path], {row.relative_path: row.identity})

        result = process_pending_moves(self.queue_path, running_check=lambda: False)

        self.assertEqual(1, result.completed_count)
        self.assertEqual(0, result.moved_count)
        self.assertEqual([], load_pending_moves(self.queue_path))

    def test_duplicate_archive_cannot_be_queued_twice(self) -> None:
        row = self._row("artist/song_p.psarc")
        queue_song_moves(self.queue_path, self.store, [row])

        with self.assertRaisesRegex(StoreError, "already queued"):
            queue_song_moves(self.queue_path, self.store, [row])

        self.assertEqual(1, len(load_pending_moves(self.queue_path)))

    def test_stale_selection_cannot_be_queued(self) -> None:
        row = self._row("artist/song_p.psarc")
        row.path.write_bytes(b"replacement archive")

        with self.assertRaisesRegex(StoreError, "changed since it was selected"):
            queue_song_moves(self.queue_path, self.store, [row])

        self.assertFalse(self.queue_path.exists())

    def test_drive_relative_queued_path_is_rejected(self) -> None:
        row = self._row("artist/song_p.psarc")
        queue_song_moves(self.queue_path, self.store, [row])
        document = json.loads(self.queue_path.read_text(encoding="utf-8"))
        document["items"][0]["relative_path"] = "C:escape.psarc"
        self.queue_path.write_text(json.dumps(document), encoding="utf-8")

        with self.assertRaisesRegex(StoreError, "Invalid queued archive path"):
            load_pending_moves(self.queue_path)

        self.assertTrue(row.path.exists())

    def test_cancel_queue_does_not_touch_archive(self) -> None:
        row = self._row("artist/song_p.psarc")
        queue_song_moves(self.queue_path, self.store, [row])

        self.assertEqual(1, cancel_pending_moves(self.queue_path))

        self.assertFalse(self.queue_path.exists())
        self.assertEqual(b"original archive", row.path.read_bytes())
        self.assertEqual([], load_pending_moves(self.queue_path))

    def test_malformed_queue_fails_closed(self) -> None:
        self.queue_path.parent.mkdir(parents=True)
        self.queue_path.write_text(json.dumps({"version": 77, "items": []}), encoding="utf-8")

        with self.assertRaisesRegex(StoreError, "format is unsupported"):
            process_pending_moves(self.queue_path, running_check=lambda: False)

        self.assertTrue(self.queue_path.exists())
        self.assertTrue((self.game / "dlc" / "artist" / "song_p.psarc").exists())

    def _row(self, relative_path: str):
        return next(row for row in self.store.inventory() if row.relative_path == relative_path)


if __name__ == "__main__":
    unittest.main()
