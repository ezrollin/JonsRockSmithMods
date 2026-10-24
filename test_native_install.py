import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from native_install import install, restore, sha256, support_status
from song_store import StoreError


class NativeInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.game = root / "game"
        self.game.mkdir()
        (self.game / "Rocksmith2014.exe").write_bytes(b"game")
        (self.game / "RSMods.ini").write_bytes(b"[Mod Settings]\n")
        self.original = self.game / "xinput1_3.dll"
        self.original.write_bytes(b"original RSMods")
        self.patched = root / "patched.dll"
        self.patched.write_bytes(b"speed extension")
        self.backups = root / "backups"

    def test_install_and_restore(self):
        original_hash = sha256(self.original)
        backup = install(self.game, self.patched, self.backups, lambda: False, original_hash)
        self.assertEqual(b"original RSMods", backup.read_bytes())
        self.assertEqual(b"speed extension", self.original.read_bytes())
        restore(self.game, self.patched, self.backups, lambda: False)
        self.assertEqual(b"original RSMods", self.original.read_bytes())

    def test_unknown_dll_and_running_game_block_install(self):
        with self.assertRaises(StoreError):
            install(self.game, self.patched, self.backups, lambda: True, sha256(self.original))
        with self.assertRaises(StoreError):
            install(self.game, self.patched, self.backups, lambda: False, "other")
        self.assertEqual(b"original RSMods", self.original.read_bytes())

    def test_reinstall_current_extension_is_success_without_replacing_files(self):
        install(self.game, self.patched, self.backups, lambda: False, sha256(self.original))
        before = self.original.stat().st_mtime_ns
        record = (self.backups / "latest_install.json").read_bytes()
        self.assertIsNone(install(self.game, self.patched, self.backups, lambda: False))
        self.assertEqual(self.original.stat().st_mtime_ns, before)
        self.assertEqual((self.backups / "latest_install.json").read_bytes(), record)

    def test_managed_extension_upgrade_keeps_original_backup_and_remains_reversible(self):
        original_hash = sha256(self.original)
        original_backup = install(self.game, self.patched, self.backups, lambda: False, original_hash)
        old_patch_hash = sha256(self.original)
        next_patch = Path(self.temp.name) / "next-patched.dll"
        next_patch.write_bytes(b"song request and speed extension")

        self.assertEqual(
            "Jons RSMods extension update ready to install",
            support_status(self.game, next_patch, self.backups, original_hash),
        )
        returned_backup = install(self.game, next_patch, self.backups, lambda: False, original_hash)

        self.assertEqual(original_backup, returned_backup)
        self.assertEqual(b"song request and speed extension", self.original.read_bytes())
        self.assertNotEqual(old_patch_hash, sha256(self.original))
        restore(self.game, next_patch, self.backups, lambda: False)
        self.assertEqual(b"original RSMods", self.original.read_bytes())

    def test_known_previous_jons_hash_can_be_upgraded_and_restored_exactly(self):
        previous_jons_bytes = b"previous verified Jons build"
        self.original.write_bytes(previous_jons_bytes)
        previous_hash = sha256(self.original)
        self.patched.write_bytes(b"new Jons extension")
        stock_hash = "0" * 64

        with patch("native_install.KNOWN_PREVIOUS_JONS_SHA256", previous_hash):
            self.assertEqual(
                "Previous verified Jons extension found; update ready to install",
                support_status(self.game, self.patched, self.backups),
            )
            backup = install(
                self.game, self.patched, self.backups, lambda: False, stock_hash
            )
            self.assertEqual(previous_jons_bytes, backup.read_bytes())
            self.assertEqual(b"new Jons extension", self.original.read_bytes())
            restore(self.game, self.patched, self.backups, lambda: False)

        self.assertEqual(previous_jons_bytes, self.original.read_bytes())


if __name__ == "__main__":
    unittest.main()
