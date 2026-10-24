"""Whole-pack replacement must retain songs and roll back incomplete installs."""

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from song_store import SongStore, StoreError
from split_pack import SplitOutput, SplitResult, SplitPackError, SplitPackInstallError, split_and_install_pack
from test_split_pack import game_directory, metadata


class PackReplacementTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.game = game_directory(self.root)
        self.source = self.game / "dlc" / "pack.psarc"
        self.source.write_bytes(b"original complete pack")
        self.review = self.root / "DoNotWant"

    def fake_split(self, *args, output_parent, **kwargs):
        stage = Path(output_parent)
        outputs = []
        for number in (1, 2):
            content = f"verified single {number}".encode()
            path = stage / f"song{number}.psarc"
            path.write_bytes(content)
            outputs.append(SplitOutput("Artist", f"Song {number}", f"Key{number}", path,
                                       len(content), hashlib.sha256(content).hexdigest().upper()))
        return SplitResult(stage, tuple(outputs), hashlib.sha256(self.source.read_bytes()).hexdigest().upper())

    def run_split(self, **kwargs):
        with patch("split_pack.split_pack_songs", side_effect=self.fake_split):
            return split_and_install_pack(
                self.source, metadata(), [0, 1], "toolkit", self.game,
                review_directory=self.review, running_check=lambda: False, **kwargs
            )

    def test_complete_replacements_move_original_reversibly_after_verification(self):
        result = self.run_split()
        self.assertFalse(self.source.exists())
        self.assertEqual(result.source_backup, self.review / "songs" / "pack.psarc")
        self.assertEqual(result.source_backup.read_bytes(), b"original complete pack")
        active = [row for row in SongStore(self.game, self.review, lambda: False).inventory()
                  if row.location == "Active"]
        self.assertEqual({row.relative_path for row in active}, {"song1.psarc", "song2.psarc"})
        self.assertTrue(all(item.path.is_file() for item in result.outputs))

    def test_backup_failure_rolls_back_singles_and_keeps_original(self):
        with patch.object(SongStore, "move_to_review", side_effect=StoreError("locked backup")):
            with self.assertRaisesRegex(SplitPackError, "rolled back"):
                self.run_split()
        self.assertEqual(list((self.game / "dlc").glob("*.psarc")), [self.source])
        self.assertEqual(self.source.read_bytes(), b"original complete pack")

    def test_partial_publication_rolls_back_before_original_is_moved(self):
        original_rename = Path.rename

        def fail_second(path, destination):
            if Path(destination).name == "song2.psarc":
                raise OSError("disk failure fixture")
            return original_rename(path, destination)

        with patch.object(Path, "rename", new=fail_second):
            with self.assertRaisesRegex(SplitPackError, "rolled back"):
                self.run_split()
        self.assertEqual(list((self.game / "dlc").glob("*.psarc")), [self.source])

    def test_subset_cannot_remove_unsplit_tracks_from_the_game(self):
        with patch("split_pack.split_pack_songs") as helper:
            with self.assertRaisesRegex(SplitPackError, "every song"):
                split_and_install_pack(self.source, metadata(), [0], "toolkit", self.game,
                                       review_directory=self.review, running_check=lambda: False)
        helper.assert_not_called()
        self.assertTrue(self.source.is_file())

    def test_unknown_backup_failure_keeps_verified_singles_if_original_was_already_moved(self):
        backup = self.review / "songs" / "pack.psarc"

        def fail_after_move(store, *args, **kwargs):
            backup.parent.mkdir(parents=True)
            self.source.rename(backup)
            raise StoreError("backup index and source rollback both failed")

        with patch.object(SongStore, "move_to_review", new=fail_after_move):
            with self.assertRaisesRegex(SplitPackInstallError, "remain installed") as caught:
                self.run_split()
        self.assertEqual(backup.read_bytes(), b"original complete pack")
        self.assertEqual(len(caught.exception.installed_paths), 2)
        self.assertTrue(all(path.is_file() for path in caught.exception.installed_paths))

    def test_running_game_blocks_whole_pack_replacement_before_work(self):
        with patch("split_pack.split_pack_songs") as helper:
            with self.assertRaisesRegex(SplitPackError, "Close Rocksmith"):
                split_and_install_pack(self.source, metadata(), [0, 1], "toolkit", self.game,
                                       review_directory=self.review, running_check=lambda: True)
        helper.assert_not_called()
        self.assertTrue(self.source.is_file())


if __name__ == "__main__":
    unittest.main()
