import tempfile
import unittest
from pathlib import Path

from game_tuning_settings import (
    DEFAULT_PITCH_REFERENCE,
    DEFAULT_SKIP_FIRST_TUNER,
    PITCH_REFERENCE_A440,
    PITCH_REFERENCE_ROCKSMITH,
    read_game_tuning_settings,
    write_game_tuning_settings,
)
from song_store import StoreError


class GameTuningSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.game = Path(self.temp.name)
        (self.game / "Rocksmith2014.exe").write_bytes(b"test")
        self.ini = self.game / "JonsRockSmithMods.ini"

    def test_missing_settings_default_to_requested_standard_and_first_skip(self):
        self.assertEqual(
            (DEFAULT_PITCH_REFERENCE, DEFAULT_SKIP_FIRST_TUNER),
            read_game_tuning_settings(self.ini),
        )

    def test_writes_and_reads_both_settings_preserving_other_values_and_backup(self):
        original = (
            "[Mod Settings]\nJonsFixedSongSpeedPercent = 85\n"
            "[Toggle Switches]\nShowSongRemovalHint = on\n"
        ).encode()
        self.ini.write_bytes(original)
        backup = write_game_tuning_settings(
            self.ini, PITCH_REFERENCE_ROCKSMITH, False, lambda: False
        )
        self.assertEqual(original, backup.read_bytes())
        self.assertEqual((PITCH_REFERENCE_ROCKSMITH, False), read_game_tuning_settings(self.ini))
        written = self.ini.read_text()
        self.assertIn("JonsFixedSongSpeedPercent = 85", written)
        self.assertIn("ShowSongRemovalHint = on", written)

    def test_a440_mode_round_trips_without_running_game(self):
        write_game_tuning_settings(self.ini, PITCH_REFERENCE_A440, True, lambda: False)
        self.assertEqual((PITCH_REFERENCE_A440, True), read_game_tuning_settings(self.ini))

    def test_rejects_running_game_invalid_values_and_wrong_paths(self):
        with self.assertRaisesRegex(StoreError, "Close Rocksmith"):
            write_game_tuning_settings(self.ini, PITCH_REFERENCE_A440, True, lambda: True)
        with self.assertRaises(StoreError):
            write_game_tuning_settings(self.ini, "other", True, lambda: False)
        with self.assertRaises(StoreError):
            write_game_tuning_settings(self.ini, PITCH_REFERENCE_A440, 1, lambda: False)
        with self.assertRaises(StoreError):
            write_game_tuning_settings(self.game / "wrong.ini", PITCH_REFERENCE_A440, True, lambda: False)

    def test_rejects_malformed_stored_values(self):
        self.ini.write_text(
            "[Mod Settings]\nJonsPitchReference = surprise\n", encoding="utf-8"
        )
        with self.assertRaisesRegex(StoreError, "Invalid pitch reference"):
            read_game_tuning_settings(self.ini)


if __name__ == "__main__":
    unittest.main()
