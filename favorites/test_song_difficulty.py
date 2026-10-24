"""Profile phrase-difficulty tests; these use synthetic data only."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import zlib
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from favorites.profile import (
    _SAVE_KEY,
    _decode_profile,
    read_sightreading_level,
    read_song_difficulty,
    set_all_song_difficulty,
    set_song_difficulty,
)


def _phrase_record(level: int) -> dict[str, int]:
    return {"Avg": level, "Diff": level, "Inc": 0}


def _profile_document() -> dict:
    return {
        "Options": {
            "DDEnabled": True,
            "DDOverrideMax": True,
            "DDSightreadLevel": -1.0,
            "UnrelatedOption": "preserve this",
        },
        "Songs": {
            "ArrangementA": {
                "UnrelatedSongValue": 42,
                "DynamicDifficulty": {
                    "Phrases": {
                        "intro": _phrase_record(1),
                        "verse": _phrase_record(2),
                    },
                    "UnrelatedDifficultyValue": True,
                },
            },
            "RemovedSongArrangement": {
                "DynamicDifficulty": {
                    "Phrases": {"old-phrase": _phrase_record(2)}
                },
            },
        },
        "UnrelatedRootValue": {"keep": [1, 2, 3]},
    }


def _encode_document(document: dict) -> bytes:
    plain = json.dumps(document, separators=(",", ":")).encode("utf-8")
    compressed = zlib.compress(plain)
    compressed += b"\x00" * (512 - len(compressed) % 512)
    encryptor = Cipher(algorithms.AES(_SAVE_KEY), modes.ECB()).encryptor()
    header = (
        b"EVAS\x01\x00\x00\x00"
        + b"\x01\x02\x03\x04"
        + b"\x00\x00\x10\x01"
        + len(plain).to_bytes(4, "little")
    )
    return header + encryptor.update(compressed) + encryptor.finalize()


class SongDifficultyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "profile_PRFLDB"
        self.path.write_bytes(_encode_document(_profile_document()))
        self.backups = Path(self.folder.name) / "backups"
        self.arrangements = {
            "ArrangementA": {"intro": 4, "verse": 8},
            "ArrangementB": {"chorus": 5},
        }

    def test_global_read_alias_keeps_existing_settings_type(self) -> None:
        settings = read_sightreading_level(self.path)
        self.assertTrue(settings.automatic_leveling_enabled)
        self.assertEqual(-1.0, settings.sightreading_level)
        self.assertTrue(settings.override_to_max)

    def test_read_selected_reports_current_and_mixed_phrase_values(self) -> None:
        raw = self.path.read_bytes()
        report = read_song_difficulty(
            self.path, self.arrangements, ["arrangementa"]
        )

        self.assertEqual(hashlib.sha256(raw).hexdigest(), report.profile_sha256)
        self.assertEqual(("ArrangementA",), tuple(item.arrangement_id for item in report.arrangements))
        status = report.arrangements[0]
        self.assertTrue(status.mixed)
        self.assertEqual({"intro": 1.0, "verse": 2.0}, status.phrase_difficulties)
        self.assertEqual({"intro": 4, "verse": 8}, status.phrase_max_difficulties)
        self.assertEqual(raw, self.path.read_bytes())

    def test_selected_update_sets_saved_arrangements_and_skips_unplayed_ones(self) -> None:
        original = self.path.read_bytes()
        result = set_song_difficulty(
            self.path,
            self.arrangements,
            ["ArrangementA", "ArrangementB"],
            10,
            expected_profile_sha256=hashlib.sha256(original).hexdigest(),
            automatic_leveling_enabled=False,
            backup_dir=self.backups,
            running_check=lambda: False,
        )

        self.assertEqual(("ArrangementA",), result.changed_arrangement_ids)
        self.assertEqual(2, result.changed_phrase_count)
        self.assertEqual(("ArrangementB",), result.unavailable_arrangement_ids)
        self.assertFalse(result.automatic_leveling_enabled)
        self.assertTrue(result.backup_path.is_file())
        self.assertEqual(original, result.backup_path.read_bytes())
        document = _decode_profile(self.path.read_bytes())
        phrases = document["Songs"]["ArrangementA"]["DynamicDifficulty"]["Phrases"]
        self.assertEqual(4, phrases["intro"]["Diff"])
        self.assertEqual(8, phrases["verse"]["Diff"])
        self.assertNotIn("ArrangementB", document["Songs"])
        self.assertIn("RemovedSongArrangement", document["Songs"])
        self.assertEqual(42, document["Songs"]["ArrangementA"]["UnrelatedSongValue"])
        self.assertTrue(document["Songs"]["ArrangementA"]["DynamicDifficulty"]["UnrelatedDifficultyValue"])
        self.assertEqual("preserve this", document["Options"]["UnrelatedOption"])
        self.assertEqual({"keep": [1, 2, 3]}, document["UnrelatedRootValue"])

    def test_selected_update_requires_at_least_one_saved_arrangement(self) -> None:
        original = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "None of the selected arrangements"):
            set_song_difficulty(
                self.path,
                self.arrangements,
                ["ArrangementB"],
                5,
                expected_profile_sha256=hashlib.sha256(original).hexdigest(),
                backup_dir=self.backups,
                running_check=lambda: False,
            )
        self.assertEqual(original, self.path.read_bytes())
        self.assertFalse(self.backups.exists())

    def test_selected_update_rejects_unverified_phrase_map_without_partial_write(self) -> None:
        document = _profile_document()
        phrases = document["Songs"]["ArrangementA"]["DynamicDifficulty"]["Phrases"]
        phrases["not-in-sng"] = phrases.pop("verse")
        original = _encode_document(document)
        self.path.write_bytes(original)

        with self.assertRaisesRegex(ValueError, "does not match its SNG"):
            set_song_difficulty(
                self.path,
                self.arrangements,
                ["ArrangementA", "ArrangementB"],
                7,
                expected_profile_sha256=hashlib.sha256(original).hexdigest(),
                backup_dir=self.backups,
                running_check=lambda: False,
            )
        self.assertEqual(original, self.path.read_bytes())
        self.assertFalse(self.backups.exists())

    def test_selected_update_refuses_stale_hash_and_open_game(self) -> None:
        original = self.path.read_bytes()
        kwargs = {
            "profile_path": self.path,
            "arrangements": self.arrangements,
            "arrangement_ids": ["ArrangementA"],
            "level": 4,
            "backup_dir": self.backups,
        }
        with self.assertRaisesRegex(OSError, "changed after confirmation"):
            set_song_difficulty(
                **kwargs,
                expected_profile_sha256=hashlib.sha256(b"stale").hexdigest(),
                running_check=lambda: False,
            )
        with self.assertRaisesRegex(OSError, "Close Rocksmith"):
            set_song_difficulty(
                **kwargs,
                expected_profile_sha256=hashlib.sha256(original).hexdigest(),
                running_check=lambda: True,
            )
        self.assertEqual(original, self.path.read_bytes())
        self.assertFalse(self.backups.exists())

    def test_global_update_sets_starting_level_and_active_saved_phrases_atomically(self) -> None:
        original = self.path.read_bytes()
        result = set_all_song_difficulty(
            self.path,
            self.arrangements,
            active_arrangement_ids=["ArrangementA", "ArrangementB"],
            level=7,
            automatic_leveling_enabled=False,
            expected_profile_sha256=hashlib.sha256(original).hexdigest(),
            backup_dir=self.backups,
            running_check=lambda: False,
        )

        self.assertEqual(("ArrangementA",), result.changed_arrangement_ids)
        self.assertEqual(2, result.changed_phrase_count)
        self.assertEqual(("ArrangementB",), result.unavailable_arrangement_ids)
        self.assertFalse(result.automatic_leveling_enabled)
        self.assertEqual(original, result.backup_path.read_bytes())
        document = _decode_profile(self.path.read_bytes())
        self.assertEqual(7.0, document["Options"]["DDSightreadLevel"])
        self.assertFalse(document["Options"]["DDEnabled"])
        self.assertFalse(document["Options"]["DDOverrideMax"])
        phrases = document["Songs"]["ArrangementA"]["DynamicDifficulty"]["Phrases"]
        self.assertEqual(3, phrases["intro"]["Diff"])
        self.assertEqual(6, phrases["verse"]["Diff"])
        self.assertNotIn("ArrangementB", document["Songs"])
        self.assertEqual(
            _profile_document()["Songs"]["RemovedSongArrangement"],
            document["Songs"]["RemovedSongArrangement"],
        )
        self.assertEqual("preserve this", document["Options"]["UnrelatedOption"])
        self.assertEqual({"keep": [1, 2, 3]}, document["UnrelatedRootValue"])

    def test_global_update_requires_complete_active_metadata_before_writing(self) -> None:
        original = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "lack verified SNG phrase metadata"):
            set_all_song_difficulty(
                self.path,
                {"ArrangementA": self.arrangements["ArrangementA"]},
                active_arrangement_ids=["ArrangementA", "ArrangementB"],
                level=6,
                automatic_leveling_enabled=True,
                expected_profile_sha256=hashlib.sha256(original).hexdigest(),
                backup_dir=self.backups,
                running_check=lambda: False,
            )
        self.assertEqual(original, self.path.read_bytes())
        self.assertFalse(self.backups.exists())

    def test_global_update_is_idempotent(self) -> None:
        original = self.path.read_bytes()
        set_all_song_difficulty(
            self.path,
            self.arrangements,
            active_arrangement_ids=["ArrangementA", "ArrangementB"],
            level=7,
            automatic_leveling_enabled=False,
            expected_profile_sha256=hashlib.sha256(original).hexdigest(),
            backup_dir=self.backups,
            running_check=lambda: False,
        )
        updated = self.path.read_bytes()
        result = set_all_song_difficulty(
            self.path,
            self.arrangements,
            active_arrangement_ids=["ArrangementA", "ArrangementB"],
            level=7,
            automatic_leveling_enabled=False,
            expected_profile_sha256=hashlib.sha256(updated).hexdigest(),
            backup_dir=self.backups,
            running_check=lambda: False,
        )
        self.assertIsNone(result.backup_path)
        self.assertEqual((), result.changed_arrangement_ids)
        self.assertEqual(0, result.changed_phrase_count)
        self.assertEqual(updated, self.path.read_bytes())


if __name__ == "__main__":
    unittest.main()
