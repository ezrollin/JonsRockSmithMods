"""Synthetic profile tests; never alter installed Rocksmith or Steam files."""

from __future__ import annotations

import json
import hashlib
import tempfile
import unittest
import zlib
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from favorites.profile import (
    _SAVE_KEY,
    _decode_profile,
    classify_archive,
    clear_all_song_favorites,
    load_favorites,
    read_difficulty_settings,
    read_override_to_max,
    set_difficulty_settings,
    set_song_favorites,
    set_override_to_max,
    song_keys_from_metadata,
)


def encoded_profile(favorites: list[str] | None = None, *, valid_list: bool = True) -> bytes:
    root = {
        "FavoritesListRoot": {"FavoritesList": favorites or []},
        "Options": {
            "DDEnabled": True,
            "DDOverrideMax": False,
            "DDSightreadLevel": -1.0,
            "DDLevelDownSpeed": 0.0,
            "OtherOption": "preserve",
        },
        "DynamicDifficulty": {"Lead": {"LevelUpThreshold": 75}},
        "Songs": {"Example": {"DynamicDifficulty": {"Lead": 4}}},
    }
    if not valid_list:
        root = {"FavoritesListRoot": {}}
    plain = json.dumps(root, separators=(",", ":")).encode("utf-8")
    compressed = zlib.compress(plain)
    compressed += b"\x00" * (512 - len(compressed) % 512)
    encryptor = Cipher(algorithms.AES(_SAVE_KEY), modes.ECB()).encryptor()
    header = b"EVAS\x01\x00\x00\x00" + b"\x01\x02\x03\x04" + b"\x00\x00\x10\x01" + len(plain).to_bytes(4, "little")
    return header + encryptor.update(compressed) + encryptor.finalize()


def archive(*song_keys: str) -> dict:
    return {
        "song_count": len(song_keys),
        "manifest_count": 1,
        "songs": [{"song_key": key} for key in song_keys],
        "warnings": [],
    }


class FavoritesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "profile_PRFLDB"

    def test_live_format_favorites_and_nonfavorites(self) -> None:
        self.path.write_bytes(encoded_profile(["SongOne", "sOnGtWo"]))
        result = load_favorites(self.path)
        self.assertTrue(result.available)
        self.assertEqual(classify_archive(archive("SONGONE"), result), "Favorite")
        self.assertEqual(classify_archive(archive("Other"), result), "Not favorite")
        self.assertEqual(classify_archive(archive("Other", "songtwo"), result), "Favorite")

    def test_missing_or_corrupt_profile_is_unknown(self) -> None:
        self.assertEqual(classify_archive(archive("Other"), load_favorites(self.path)), "Unknown")
        self.path.write_bytes(b"invalid")
        self.assertEqual(classify_archive(archive("Other"), load_favorites(self.path)), "Unknown")

    def test_missing_favorite_list_is_unknown(self) -> None:
        self.path.write_bytes(encoded_profile(valid_list=False))
        self.assertFalse(load_favorites(self.path).available)

    def test_unfavorite_all_clears_profile_keys_and_preserves_verified_backup(self) -> None:
        original = encoded_profile(["ActiveSong", "MissingSong", "ArchivedSong"])
        self.path.write_bytes(original)
        backup_dir = Path(self.folder.name) / "backups"

        result = clear_all_song_favorites(
            self.path,
            backup_dir,
            running_check=lambda: False,
            expected_profile_sha256=hashlib.sha256(original).hexdigest(),
        )

        self.assertEqual(("ActiveSong", "MissingSong", "ArchivedSong"), result.changed_song_keys)
        self.assertFalse(result.favorited)
        self.assertTrue(result.backup_path.is_file())
        self.assertEqual(original, result.backup_path.read_bytes())
        updated = load_favorites(self.path)
        self.assertTrue(updated.available)
        self.assertEqual(frozenset(), updated.favorite_song_keys)
        profile = _decode_profile(self.path.read_bytes())
        self.assertEqual("preserve", profile["Options"]["OtherOption"])

    def test_unfavorite_all_rejects_stale_profile_and_open_game(self) -> None:
        original = encoded_profile(["KeepSong"])
        self.path.write_bytes(original)
        with self.assertRaisesRegex(OSError, "profile changed"):
            clear_all_song_favorites(
                self.path,
                Path(self.folder.name) / "stale-backups",
                running_check=lambda: False,
                expected_profile_sha256="0" * 64,
            )
        with self.assertRaisesRegex(OSError, "Close Rocksmith"):
            clear_all_song_favorites(
                self.path,
                Path(self.folder.name) / "running-backups",
                running_check=lambda: True,
                expected_profile_sha256=hashlib.sha256(original).hexdigest(),
            )
        self.assertEqual(original, self.path.read_bytes())

    def test_unfavorite_all_empty_list_is_idempotent_without_backup(self) -> None:
        original = encoded_profile([])
        self.path.write_bytes(original)
        backup_dir = Path(self.folder.name) / "empty-backups"
        result = clear_all_song_favorites(
            self.path,
            backup_dir,
            running_check=lambda: False,
            expected_profile_sha256=hashlib.sha256(original).hexdigest(),
        )
        self.assertEqual((), result.changed_song_keys)
        self.assertIsNone(result.backup_path)
        self.assertFalse(backup_dir.exists())

    def test_bad_or_incomplete_archive_metadata_is_unknown(self) -> None:
        self.path.write_bytes(encoded_profile(["SongOne"]))
        result = load_favorites(self.path)
        self.assertEqual(classify_archive(archive(""), result), "Unknown")
        self.assertEqual(classify_archive(archive("Other", "OTHER"), result), "Unknown")
        self.assertEqual(classify_archive({**archive("Other"), "song_count": 2}, result), "Unknown")
        self.assertEqual(classify_archive({**archive("Other"), "warnings": ["Failed manifest"]}, result), "Unknown")
        self.assertEqual(classify_archive({**archive("Other"), "warnings": ["Missing song title"]}, result), "Unknown")
        self.assertEqual(classify_archive(archive("Other", ""), result), "Unknown")
        self.assertEqual(classify_archive(archive("SongOne", ""), result), "Favorite")

    def test_override_to_max_backup_and_round_trip_changes_only_requested_setting(self) -> None:
        self.path.write_bytes(encoded_profile(["KeepSong", "OtherSong"]))
        original = self.path.read_bytes()
        backup_dir = Path(self.folder.name) / "backups"

        backup = set_override_to_max(self.path, True, backup_dir, running_check=lambda: False)

        self.assertIsNotNone(backup)
        self.assertEqual(backup.read_bytes(), original)
        self.assertTrue(read_override_to_max(self.path))
        written = _decode_profile(self.path.read_bytes())
        self.assertTrue(written["Options"]["DDOverrideMax"])
        self.assertEqual(written["Options"]["DDLevelDownSpeed"], 0.0)
        self.assertEqual(written["Options"]["OtherOption"], "preserve")
        self.assertEqual(written["DynamicDifficulty"], {"Lead": {"LevelUpThreshold": 75}})
        self.assertEqual(written["Songs"], {"Example": {"DynamicDifficulty": {"Lead": 4}}})
        favorites = load_favorites(self.path)
        self.assertTrue(favorites.available)
        self.assertEqual(favorites.favorite_song_keys, frozenset({"keepsong", "othersong"}))

        unchanged = self.path.read_bytes()
        self.assertIsNone(set_override_to_max(self.path, True, backup_dir, running_check=lambda: False))
        self.assertEqual(self.path.read_bytes(), unchanged)

    def test_override_to_max_requires_valid_game_option(self) -> None:
        self.path.write_bytes(encoded_profile(valid_list=False))
        with self.assertRaises(ValueError):
            read_override_to_max(self.path)
        with self.assertRaises(ValueError):
            set_override_to_max(
                self.path, True, Path(self.folder.name) / "backups", running_check=lambda: False
            )

    def test_override_to_max_refuses_a_running_game_before_backup_or_write(self) -> None:
        original = encoded_profile(["KeepSong"])
        self.path.write_bytes(original)
        backup_dir = Path(self.folder.name) / "backups"
        with self.assertRaisesRegex(OSError, "Close Rocksmith"):
            set_override_to_max(self.path, True, backup_dir, running_check=lambda: True)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertFalse(backup_dir.exists())

    def test_read_difficulty_settings_reports_auto_and_automatic_leveling(self) -> None:
        self.path.write_bytes(encoded_profile())
        settings = read_difficulty_settings(self.path)
        self.assertTrue(settings.automatic_leveling_enabled)
        self.assertEqual(settings.sightreading_level, -1.0)
        self.assertIsNone(settings.sightreading_percent)
        self.assertFalse(settings.override_to_max)

    def test_fixed_75_percent_changes_only_global_difficulty_options(self) -> None:
        self.path.write_bytes(encoded_profile(["KeepSong"]))
        original = self.path.read_bytes()
        backup_dir = Path(self.folder.name) / "backups"

        backup = set_difficulty_settings(
            self.path, 75, True, backup_dir, running_check=lambda: False
        )

        self.assertIsNotNone(backup)
        self.assertEqual(backup.read_bytes(), original)
        document = _decode_profile(self.path.read_bytes())
        self.assertEqual(document["Options"]["DDSightreadLevel"], 7.5)
        self.assertFalse(document["Options"]["DDEnabled"])
        self.assertFalse(document["Options"]["DDOverrideMax"])
        self.assertEqual(document["Options"]["OtherOption"], "preserve")
        self.assertEqual(document["Songs"], {"Example": {"DynamicDifficulty": {"Lead": 4}}})
        settings = read_difficulty_settings(self.path)
        self.assertFalse(settings.automatic_leveling_enabled)
        self.assertEqual(settings.sightreading_percent, 75)
        self.assertIsNone(set_difficulty_settings(
            self.path, 75, True, backup_dir, running_check=lambda: False
        ))

    def test_75_percent_starting_level_can_keep_dynamic_difficulty_enabled(self) -> None:
        self.path.write_bytes(encoded_profile())
        backup_dir = Path(self.folder.name) / "backups"

        set_difficulty_settings(
            self.path, 75, False, backup_dir, running_check=lambda: False
        )

        settings = read_difficulty_settings(self.path)
        self.assertEqual(settings.sightreading_percent, 75)
        self.assertTrue(settings.automatic_leveling_enabled)
        self.assertFalse(settings.override_to_max)

    def test_difficulty_setting_validates_percent_and_running_game(self) -> None:
        self.path.write_bytes(encoded_profile())
        backup_dir = Path(self.folder.name) / "backups"
        for value in (True, -1, 101, 75.5, "75"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                set_difficulty_settings(
                    self.path, value, True, backup_dir, running_check=lambda: False
                )
        original = self.path.read_bytes()
        with self.assertRaisesRegex(OSError, "Close Rocksmith"):
            set_difficulty_settings(
                self.path, 75, True, backup_dir, running_check=lambda: True
            )
        self.assertEqual(self.path.read_bytes(), original)
        self.assertFalse(backup_dir.exists())

    def test_add_selected_song_keys_creates_backup_and_preserves_other_profile_data(self) -> None:
        self.path.write_bytes(encoded_profile(["ExistingSong"]))
        original = self.path.read_bytes()
        backup_dir = Path(self.folder.name) / "backups"

        result = set_song_favorites(
            self.path, ["NewSong", "SecondSong"], True, backup_dir,
            running_check=lambda: False,
        )

        self.assertEqual(("NewSong", "SecondSong"), result.changed_song_keys)
        self.assertTrue(result.favorited)
        self.assertEqual(original, result.backup_path.read_bytes())
        document = _decode_profile(self.path.read_bytes())
        self.assertEqual(
            ["ExistingSong", "NewSong", "SecondSong"],
            document["FavoritesListRoot"]["FavoritesList"],
        )
        self.assertEqual("preserve", document["Options"]["OtherOption"])
        self.assertEqual({"Lead": {"LevelUpThreshold": 75}}, document["DynamicDifficulty"])
        self.assertEqual({"Example": {"DynamicDifficulty": {"Lead": 4}}}, document["Songs"])
        self.assertEqual(
            frozenset({"existingsong", "newsong", "secondsong"}),
            load_favorites(self.path).favorite_song_keys,
        )

    def test_remove_song_keys_is_case_insensitive_and_preserves_other_favorites(self) -> None:
        self.path.write_bytes(encoded_profile(["KeepSong", "RemoveSong", "OtherSong"]))
        backup_dir = Path(self.folder.name) / "backups"

        result = set_song_favorites(
            self.path, ["removesong"], False, backup_dir,
            running_check=lambda: False,
        )

        self.assertEqual(("RemoveSong",), result.changed_song_keys)
        self.assertFalse(result.favorited)
        self.assertEqual(
            ["KeepSong", "OtherSong"],
            _decode_profile(self.path.read_bytes())["FavoritesListRoot"]["FavoritesList"],
        )
        self.assertEqual(frozenset({"keepsong", "othersong"}), load_favorites(self.path).favorite_song_keys)

    def test_favorite_update_is_idempotent_and_does_not_create_backup_without_change(self) -> None:
        self.path.write_bytes(encoded_profile(["SongOne"]))
        original = self.path.read_bytes()
        backup_dir = Path(self.folder.name) / "backups"

        result = set_song_favorites(
            self.path, ["sOnGoNe"], True, backup_dir,
            running_check=lambda: False,
        )

        self.assertEqual((), result.changed_song_keys)
        self.assertIsNone(result.backup_path)
        self.assertEqual(original, self.path.read_bytes())
        self.assertFalse(backup_dir.exists())

    def test_favorite_update_rejects_running_game_invalid_list_and_duplicate_keys(self) -> None:
        original = encoded_profile(["KeepSong"])
        self.path.write_bytes(original)
        backup_dir = Path(self.folder.name) / "backups"
        with self.assertRaisesRegex(OSError, "Close Rocksmith"):
            set_song_favorites(
                self.path, ["SongOne"], True, backup_dir,
                running_check=lambda: True,
            )
        with self.assertRaisesRegex(ValueError, "duplicates"):
            set_song_favorites(
                self.path, ["SongOne", "songone"], True, backup_dir,
                running_check=lambda: False,
            )
        self.path.write_bytes(encoded_profile(valid_list=False))
        with self.assertRaisesRegex(ValueError, "FavoritesListRoot"):
            set_song_favorites(
                self.path, ["SongOne"], True, backup_dir,
                running_check=lambda: False,
            )
        self.assertFalse(backup_dir.exists())

    def test_favorite_update_requires_profile_hash_from_confirmation(self) -> None:
        original = encoded_profile(["KeepSong"])
        self.path.write_bytes(original)
        backup_dir = Path(self.folder.name) / "backups"
        wrong_hash = hashlib.sha256(b"different profile").hexdigest()

        with self.assertRaisesRegex(OSError, "changed after confirmation"):
            set_song_favorites(
                self.path,
                ["SongOne"],
                True,
                backup_dir,
                running_check=lambda: False,
                expected_profile_sha256=wrong_hash,
            )

        self.assertEqual(original, self.path.read_bytes())
        self.assertFalse(backup_dir.exists())
        with self.assertRaisesRegex(ValueError, "64 hexadecimal"):
            set_song_favorites(
                self.path,
                ["SongOne"],
                True,
                backup_dir,
                running_check=lambda: False,
                expected_profile_sha256="bad",
            )

    def test_song_keys_from_metadata_accepts_complete_multi_song_archive_only(self) -> None:
        self.assertEqual(("SongOne", "SongTwo"), song_keys_from_metadata(archive("SongOne", "SongTwo")))
        self.assertEqual(("SongOne",), song_keys_from_metadata(archive("SongOne")))
        for metadata in (
            None,
            {**archive("SongOne"), "song_count": 2},
            {**archive("SongOne"), "warnings": ["Missing title"]},
            archive("SongOne", ""),
            archive("SongOne", "songone"),
        ):
            with self.subTest(metadata=metadata), self.assertRaises(ValueError):
                song_keys_from_metadata(metadata)


if __name__ == "__main__":
    unittest.main()
