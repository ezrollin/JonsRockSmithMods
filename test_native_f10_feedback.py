from __future__ import annotations

import unittest
from pathlib import Path


class NativeF10FeedbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        patch_path = Path(__file__).parent / "native" / "jons-fixed-song-speed.patch"
        cls.patch_text = patch_path.read_text(encoding="utf-8")
        cls.keybindings = cls.patch_text.split("diff --git a/DLL/Keybindings.cpp", 1)[1].split(
            "diff --git ", 1
        )[0]
        cls.overlay = cls.patch_text.split("diff --git a/DLL/D3DOverlay.cpp", 1)[1].split(
            "diff --git ", 1
        )[0]

    def test_hint_says_f10_marks_move_to_do_not_want_after_game_close(self):
        self.assertIn("to mark this song for removal when Rocksmith closes", self.overlay)
        self.assertIn("It moves to Do Not Want, so you can undo it", self.overlay)
        self.assertNotIn("mark preview for removal after exit", self.overlay)

    def test_success_is_acknowledged_only_after_atomic_request_publish(self):
        publish = self.keybindings.index("MoveFileExW(temporaryPath.c_str(), requestPath.c_str()")
        success = self.keybindings.index("F10 RECEIVED:")
        self.assertLess(publish, success)
        self.assertIn("verified single-song file to Do Not Want", self.keybindings[success:])
        self.assertIn("Packs must be split first", self.keybindings[success:])
        self.assertIn("RejectSongRemovalRequest", self.keybindings)
        self.assertNotIn("Could not save removal mark. Check the RSMods log", self.keybindings)

    def test_status_remains_visible_long_enough_for_user_feedback(self):
        self.assertIn("message.substr(0, 256)", self.overlay)
        self.assertIn("GetTickCount64() + 15000", self.overlay)


if __name__ == "__main__":
    unittest.main()
