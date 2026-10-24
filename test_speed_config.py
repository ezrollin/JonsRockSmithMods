import tempfile
import unittest
from pathlib import Path

from speed_config import (
    effective_speed_for_song, read_song_speed_config, read_speed_config,
    write_song_speed_config, write_song_speed_configs, write_speed_config,
)
from song_store import StoreError


class SpeedConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ini = Path(self.temp.name) / "JonsRockSmithMods.ini"
        (self.ini.parent / "Rocksmith2014.exe").write_bytes(b"test")
        self.ini.write_bytes(b"[Toggle Switches]\r\nRRSpeedAboveOneHundred = off\r\n[Mod Settings]\r\nRRSpeedInterval = 2\r\n")

    def test_preserves_other_settings_and_backs_up(self):
        original = self.ini.read_bytes()
        backup = write_speed_config(self.ini, True, 75, lambda: False)
        self.assertEqual(original, backup.read_bytes())
        self.assertEqual((True, 75), read_speed_config(self.ini))
        updated = self.ini.read_bytes()
        self.assertIn(b"RRSpeedAboveOneHundred = off\r\n", updated)
        self.assertIn(b"RRSpeedInterval = 2\r\n", updated)
        self.assertNotIn(b"\r\r\n", updated)

    def test_running_or_invalid_speed_blocks_write(self):
        with self.assertRaises(StoreError):
            write_speed_config(self.ini, True, 75, lambda: True)
        with self.assertRaises(StoreError):
            write_speed_config(self.ini, True, 401, lambda: False)

    def test_unconfigured_and_invalid_speed_use_85_percent_default(self):
        self.assertEqual((False, 85), read_speed_config(self.ini))
        self.ini.write_text(
            "[Toggle Switches]\nJonsFixedSongSpeedEnabled = on\n"
            "[Mod Settings]\nJonsFixedSongSpeedPercent = invalid\n",
            encoding="utf-8",
        )
        self.assertEqual((True, 85), read_speed_config(self.ini))

    def test_existing_saved_speed_is_retained(self):
        self.ini.write_text(
            "[Toggle Switches]\nJonsFixedSongSpeedEnabled = on\n"
            "[Mod Settings]\nJonsFixedSongSpeedPercent = 125\n",
            encoding="utf-8",
        )
        self.assertEqual((True, 125), read_speed_config(self.ini))

    def test_new_config_file(self):
        self.ini.unlink()
        self.assertIsNone(write_speed_config(self.ini, True, 80, lambda: False))
        self.assertEqual((True, 80), read_speed_config(self.ini))

    def test_song_override_live_write_preserves_other_songs_global_and_backup(self):
        write_speed_config(self.ini, True, 125, lambda: False)
        write_song_speed_config(self.ini, "First_Song", 70, lambda: True)
        original = self.ini.read_bytes()
        backup = write_song_speed_config(self.ini, "OtherSong", 85, lambda: True)
        self.assertEqual(original, backup.read_bytes())
        self.assertEqual({"first_song": 70, "othersong": 85}, read_song_speed_config(self.ini))
        self.assertEqual(70, effective_speed_for_song(self.ini, "FIRST_SONG"))
        self.assertEqual(125, effective_speed_for_song(self.ini, "UnchangedSong"))
        self.assertIn(b"RRSpeedInterval = 2\r\n", self.ini.read_bytes())

    def test_removing_override_returns_to_global_or_normal_speed(self):
        write_song_speed_config(self.ini, "MySong", 75)
        self.assertEqual(75, effective_speed_for_song(self.ini, "MySong"))
        write_song_speed_config(self.ini, "mySONG", None)
        self.assertIsNone(effective_speed_for_song(self.ini, "MySong"))
        write_speed_config(self.ini, True, 90, lambda: False)
        self.assertEqual(90, effective_speed_for_song(self.ini, "MySong"))

    def test_invalid_identity_speed_and_overrides_never_write(self):
        original = self.ini.read_bytes()
        for key, value in (("Song\nOther = 85", 75), ("", 85), ("Song", 401), ("Song", True)):
            with self.subTest(key=key, value=value), self.assertRaises(StoreError):
                write_song_speed_config(self.ini, key, value)
        self.assertEqual(original, self.ini.read_bytes())

    def test_invalid_stored_overrides_are_ignored_and_invalid_global_uses_default(self):
        self.ini.write_text("[Mod Settings]\nJonsFixedSongSpeedPercent=5000\n"
                            "[Song Speed Overrides]\nGoodSong=85\nInvalid=zero\nFast=401\n",
                            encoding="utf-8")
        self.assertEqual((False, 85), read_speed_config(self.ini))
        self.assertEqual({"goodsong": 85}, read_song_speed_config(self.ini))

    def test_duplicate_section_keys_cannot_resurrect_stale_override(self):
        self.ini.write_bytes(b"[Song Speed Overrides]\r\nSong=60\r\n[Other]\r\nKeep=yes\r\n"
                             b"[Song Speed Overrides]\r\nSONG=90\r\n")
        write_song_speed_config(self.ini, "Song", 85)
        self.assertEqual(85, effective_speed_for_song(self.ini, "Song"))
        write_song_speed_config(self.ini, "Song", None)
        self.assertEqual({}, read_song_speed_config(self.ini))
        self.assertIn(b"Keep=yes\r\n", self.ini.read_bytes())

    def test_bulk_song_selection_is_one_atomic_write_and_preserves_other_songs(self):
        write_song_speed_config(self.ini, "KeepSong", 120)
        original = self.ini.read_bytes()
        before_backups = len(list(self.ini.parent.glob("JonsRockSmithMods.ini.*.bak")))
        backup = write_song_speed_configs(self.ini, {"FirstSong": 85, "OtherSong": 85}, lambda: True)
        self.assertEqual(original, backup.read_bytes())
        self.assertEqual(before_backups + 1, len(list(self.ini.parent.glob("JonsRockSmithMods.ini.*.bak"))))
        self.assertEqual({"keepsong": 120, "firstsong": 85, "othersong": 85}, read_song_speed_config(self.ini))
        write_song_speed_configs(self.ini, {"FirstSong": None, "OtherSong": 75})
        self.assertEqual({"keepsong": 120, "othersong": 75}, read_song_speed_config(self.ini))

    def test_invalid_second_song_in_bulk_does_not_write_first_song(self):
        original = self.ini.read_bytes()
        with self.assertRaises(StoreError):
            write_song_speed_configs(self.ini, {"GoodSong": 85, "InvalidSong": 401})
        with self.assertRaises(StoreError):
            write_song_speed_configs(self.ini, {"Song": 85, "SONG": 75})
        self.assertEqual(original, self.ini.read_bytes())
        self.assertEqual([], list(self.ini.parent.glob("JonsRockSmithMods.ini.*.bak")))


if __name__ == "__main__":
    unittest.main()
