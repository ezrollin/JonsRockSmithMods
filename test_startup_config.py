import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ini_settings import atomic_write_ini
from song_store import StoreError
from startup_config import read_startup_config, write_startup_config


class StartupConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ini = Path(self.temp.name) / "RSMods.ini"
        (self.ini.parent / "Rocksmith2014.exe").write_bytes(b"test")
        self.ini.write_bytes(b"; User settings\r\n[Toggle Switches]\r\nForceProfileLoad = off\r\n"
                             b"ProfileToLoad = Old name\r\nForceReEnumeration = on\r\n"
                             b"[Mod Settings]\r\nCheckForNewSongsInterval = 5000\r\n")

    def test_exact_local_profile_preserves_other_settings_and_verified_backup(self):
        original = self.ini.read_bytes()
        backup = write_startup_config(self.ini, True, "Jon's Profile", lambda: False)
        self.assertEqual(original, backup.read_bytes())
        self.assertEqual((True, "Jon's Profile"), read_startup_config(self.ini))
        self.assertIn(b"ForceReEnumeration = on\r\n", self.ini.read_bytes())
        self.assertIn(b"CheckForNewSongsInterval = 5000\r\n", self.ini.read_bytes())
        self.assertNotIn(b"\r\r\n", self.ini.read_bytes())

    def test_ambiguous_blank_profile_and_running_game_never_write(self):
        original = self.ini.read_bytes()
        for name, running in (("", False), ("   ", False), ("Jon", True), ("Jon\nForceProfileLoad = on", False)):
            with self.subTest(name=name), self.assertRaises(StoreError):
                write_startup_config(self.ini, True, name, lambda: running)
        self.assertEqual(original, self.ini.read_bytes())

    def test_disable_and_missing_ini(self):
        write_startup_config(self.ini, False, "", lambda: False)
        self.assertEqual((False, ""), read_startup_config(self.ini))
        self.ini.unlink()
        self.assertEqual((False, ""), read_startup_config(self.ini))
        self.assertIsNone(write_startup_config(self.ini, True, "Jon", lambda: False))
        self.assertEqual((True, "Jon"), read_startup_config(self.ini))

    def test_encoding_and_duplicate_keys_preserved(self):
        self.ini.write_bytes(b"; Andr\xe9\r\n[Toggle Switches]\r\nForceProfileLoad=off\r\n"
                             b"ProfileToLoad=Old\r\nProfileToLoad=Stale\r\n")
        write_startup_config(self.ini, True, "Andr\u00e9", lambda: False)
        self.assertEqual((True, "Andr\u00e9"), read_startup_config(self.ini))
        self.assertIn(b"; Andr\xe9\r\n", self.ini.read_bytes())

    def test_failed_atomic_replace_leaves_original_and_backup(self):
        original = self.ini.read_bytes()
        with patch("ini_settings.os.replace", side_effect=PermissionError("locked")):
            with self.assertRaises(PermissionError):
                write_startup_config(self.ini, True, "Jon", lambda: False)
        self.assertEqual(original, self.ini.read_bytes())
        backups = list(self.ini.parent.glob("RSMods.ini.*.bak"))
        self.assertEqual(1, len(backups))
        self.assertEqual(original, backups[0].read_bytes())
        self.assertEqual([], list(self.ini.parent.glob(".RSMods-*.tmp")))

    def test_changed_file_is_never_overwritten(self):
        original = self.ini.read_bytes()
        self.ini.write_bytes(original + b"; changed\r\n")
        with self.assertRaises(StoreError):
            atomic_write_ini(self.ini, original, "replacement", "utf-8")
        self.assertEqual(original + b"; changed\r\n", self.ini.read_bytes())

    def test_unchanged_save_keeps_bytes_mtime_and_creates_no_backup(self):
        original = self.ini.read_bytes()
        original_modified = self.ini.stat().st_mtime_ns
        with patch("ini_settings.os.replace") as replace:
            self.assertIsNone(write_startup_config(self.ini, False, "Old name", lambda: False))
        replace.assert_not_called()
        self.assertEqual(original, self.ini.read_bytes())
        self.assertEqual(original_modified, self.ini.stat().st_mtime_ns)
        self.assertEqual([], list(self.ini.parent.glob("RSMods.ini.*.bak")))


if __name__ == "__main__":
    unittest.main()
