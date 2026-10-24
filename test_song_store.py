import json
import os
import tempfile
import unittest
from pathlib import Path

from song_store import MANIFEST_NAME, SongStore, StoreError, normalize_review_root


class SongStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.game = root / "game"
        self.review = root / "review"
        (self.game / "dlc" / "artist").mkdir(parents=True)
        (self.game / "Rocksmith2014.exe").write_bytes(b"test")
        (self.game / "dlc" / "artist" / "song_p.psarc").write_bytes(b"song")
        self.store = SongStore(self.game, self.review, lambda: False)

    def test_move_and_restore_preserve_song_bytes(self):
        song = "artist/song_p.psarc"
        original = (self.game / "dlc" / song).read_bytes()
        self.assertEqual(1, self.store.move_to_review([song]))
        self.assertEqual("Do Not Want", self.store.inventory()[0].location)
        self.assertFalse((self.game / "dlc" / song).exists())
        self.assertEqual(original, (self.review / "songs" / song).read_bytes())
        self.assertEqual(1, self.store.restore([song]))
        self.assertEqual("Active", self.store.inventory()[0].location)
        self.assertEqual(original, (self.game / "dlc" / song).read_bytes())

    def test_game_running_blocks_mutations(self):
        blocked = SongStore(self.game, self.review, lambda: True)
        with self.assertRaisesRegex(StoreError, "Close Rocksmith"):
            blocked.move_to_review(["artist/song_p.psarc"])
        self.assertTrue((self.game / "dlc" / "artist" / "song_p.psarc").exists())

    def test_destination_collision_does_not_overwrite(self):
        song = "artist/song_p.psarc"
        target = self.review / "songs" / song
        target.parent.mkdir(parents=True)
        target.write_bytes(b"other")
        with self.assertRaisesRegex(StoreError, "occupied"):
            self.store.move_to_review([song])
        self.assertEqual(b"other", target.read_bytes())

    def test_automatic_move_rejects_replaced_archive_even_with_same_size_and_timestamp(self):
        row = self.store.inventory()[0]
        replacement = self.game / "dlc" / "replacement.psarc"
        original_stat = row.path.stat()
        replacement.write_bytes(b"evil")
        os.utime(replacement, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        replacement.replace(row.path)
        with self.assertRaisesRegex(StoreError, "changed since favorite verification"):
            self.store.move_to_review([row.relative_path], {row.relative_path: row.identity})
        self.assertEqual(b"evil", row.path.read_bytes())
        self.assertFalse((self.review / "songs" / row.relative_path).exists())

    def test_unsafe_paths_and_overlapping_folder(self):
        with self.assertRaises(StoreError):
            self.store.move_to_review(["../song.psarc"])
        with self.assertRaises(StoreError):
            SongStore(self.game, self.game / "review", lambda: False)

    def test_invalid_manifest_fails_closed(self):
        self.review.mkdir()
        (self.review / ".jonsrocksmithmods.json").write_text("broken", encoding="utf-8")
        with self.assertRaisesRegex(StoreError, "Cannot read review manifest"):
            self.store.inventory()

    def test_manifest_backed_songs_child_normalizes_to_review_root(self):
        root = self.review
        songs = root / "songs"
        songs.mkdir(parents=True)
        (songs / "artist").mkdir()
        (songs / "artist" / "held.psarc").write_bytes(b"reviewed song")
        (root / MANIFEST_NAME).write_text(
            json.dumps({"version": 1, "songs": {"artist/held.psarc": {"size": 13}}}),
            encoding="utf-8",
        )

        normalized = normalize_review_root(songs)
        store = SongStore(self.game, songs, lambda: False)

        self.assertEqual(root.resolve(), normalized)
        self.assertEqual(root.resolve(), store.review_root)
        self.assertEqual((root / "songs").resolve(), store.songs_root)
        reviewed = [row for row in store.inventory() if row.location == "Do Not Want"]
        self.assertEqual(["artist/held.psarc"], [row.relative_path for row in reviewed])

    def test_ambiguous_songs_path_fails_closed_without_touching_files(self):
        songs = self.review / "songs"
        songs.mkdir(parents=True)
        archived = songs / "held.psarc"
        archived.write_bytes(b"keep exactly")

        with self.assertRaisesRegex(StoreError, "parent has no"):
            SongStore(self.game, songs, lambda: False)

        self.assertEqual(b"keep exactly", archived.read_bytes())


if __name__ == "__main__":
    unittest.main()
