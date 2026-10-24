from __future__ import annotations

import hashlib
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from split_pack import (
    SplitPackError,
    SplitPackInstallError,
    SplitOutput,
    SplitResult,
    create_split_output_directory,
    install_split_outputs,
    selected_song_payload,
    split_and_install_pack,
    validate_single_song_readback,
    validate_split_metadata,
    split_pack_unavailable_reason,
    resolve_rocksmith_dlc_directory,
    update_visible_song_selection,
)


def song(index: int = 1) -> dict:
    return {
        "artist": f"Artist {index}",
        "title": f"Song {index}",
        "song_key": f"SongKey{index}",
        "dlc_key": "TestPack",
        "arrangement_ids": [f"arr-{index}"],
        "manifest_files": [f"manifests/test/song{index}_lead.json"],
    }


def metadata() -> dict:
    return {"warnings": [], "songs": [song(1), song(2)]}


def game_directory(parent: Path) -> Path:
    game = parent / "SteamLibrary" / "Rocksmith2014"
    (game / "dlc").mkdir(parents=True)
    (game / "Rocksmith2014.exe").write_bytes(b"fixture game executable")
    return game


def split_result(stage: Path, filename: str, content: bytes) -> SplitResult:
    stage.mkdir(parents=True, exist_ok=True)
    output_path = stage / filename
    output_path.write_bytes(content)
    output = SplitOutput(
        artist="Artist",
        title="Song",
        song_key="SongKey",
        path=output_path,
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest().upper(),
    )
    return SplitResult(stage, (output,), "SOURCEHASH")


class SplitPackTests(unittest.TestCase):
    def test_split_metadata_requires_clean_records_and_safe_manifest_paths(self):
        self.assertEqual(len(validate_split_metadata(metadata())), 2)

        warned = metadata()
        warned["warnings"] = ["manifest was unreadable"]
        with self.assertRaisesRegex(SplitPackError, "warnings"):
            validate_split_metadata(warned)

        escaped = metadata()
        escaped["songs"][0]["manifest_files"] = ["manifests/../../outside.json"]
        with self.assertRaisesRegex(SplitPackError, "Unsafe"):
            validate_split_metadata(escaped)

    def test_selection_payload_contains_only_checked_songs(self):
        payload = selected_song_payload(metadata()["songs"], [1, 1])
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["title"], "Song 2")
        self.assertEqual(payload[0]["arrangement_ids"], ["arr-2"])
        with self.assertRaisesRegex(SplitPackError, "at least one"):
            selected_song_payload(metadata()["songs"], [])

    def test_select_all_payload_contains_every_track_once_in_source_order(self):
        songs = metadata()["songs"]
        payload = selected_song_payload(songs, range(len(songs)))
        self.assertEqual([song["title"] for song in payload], ["Song 1", "Song 2"])
        self.assertEqual(len(payload), len(songs))

    def test_split_action_is_unavailable_for_single_song_or_unverified_metadata(self):
        self.assertIsNone(split_pack_unavailable_reason(metadata()))
        single = {"warnings": [], "songs": [song(1)]}
        self.assertIn("at least two", split_pack_unavailable_reason(single))
        warned = metadata()
        warned["warnings"] = ["title read incomplete"]
        self.assertIn("warnings", split_pack_unavailable_reason(warned))

    def test_filtered_selection_updates_visible_rows_and_preserves_hidden_rows(self):
        updated = update_visible_song_selection(
            existing_indices={0, 1, 3},
            visible_indices={1, 2},
            selected_visible_indices={2},
        )
        self.assertEqual(updated, {0, 2, 3})
        with self.assertRaisesRegex(SplitPackError, "changed"):
            update_visible_song_selection({0}, {1}, {2})

    def test_duplicate_song_identity_cannot_be_selected_twice(self):
        repeated = song(1)
        with self.assertRaisesRegex(SplitPackError, "Duplicate song identity"):
            selected_song_payload([repeated, repeated.copy()], [0, 1])

    def test_output_directory_is_unique_and_never_inside_game_or_source_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_dir = root / "game" / "dlc"
            source_dir.mkdir(parents=True)
            source = source_dir / "pack.psarc"
            source.write_bytes(b"source archive fixture")
            output_parent = root / "review"
            fixed_time = datetime(2026, 10, 11, 9, 30, 0)

            first = create_split_output_directory(source, output_parent, root / "game", fixed_time)
            second = create_split_output_directory(source, output_parent, root / "game", fixed_time)
            self.assertNotEqual(first, second)
            self.assertEqual(first.parent, output_parent)
            self.assertTrue(first.is_dir() and second.is_dir())

            with self.assertRaisesRegex(SplitPackError, "outside the Rocksmith game folder"):
                create_split_output_directory(source, source_dir / "review", root / "game", fixed_time)
            with self.assertRaisesRegex(SplitPackError, "outside the source archive folder"):
                create_split_output_directory(source, source_dir / "review", None, fixed_time)

    def test_resolves_dlc_from_configured_game_directory_without_windows_username(self):
        with tempfile.TemporaryDirectory() as temporary:
            game = game_directory(Path(temporary))
            self.assertEqual(resolve_rocksmith_dlc_directory(game), game / "dlc")

    def test_dlc_resolution_requires_existing_rocksmith_install_and_dlc_folder(self):
        with tempfile.TemporaryDirectory() as temporary:
            invalid_game = Path(temporary) / "not-rocksmith"
            invalid_game.mkdir()
            with self.assertRaisesRegex(SplitPackError, "not a Rocksmith 2014 install"):
                resolve_rocksmith_dlc_directory(invalid_game)

            game = Path(temporary) / "missing-dlc"
            game.mkdir()
            (game / "Rocksmith2014.exe").write_bytes(b"fixture")
            with self.assertRaisesRegex(SplitPackError, "DLC folder does not exist"):
                resolve_rocksmith_dlc_directory(game)

    def test_installs_verified_songs_directly_into_dlc_and_reports_progress(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = game_directory(root)
            staged = split_result(root / "stage", "Artist - Song - SongKey.psarc", b"verified psarc")
            progress = []

            installed = install_split_outputs(staged, game, progress.append)

            target = game / "dlc" / "Artist - Song - SongKey.psarc"
            self.assertEqual(installed.output_directory, game / "dlc")
            self.assertEqual(installed.outputs[0].path, target)
            self.assertEqual(target.read_bytes(), b"verified psarc")
            self.assertTrue(any(text.startswith("Staging 1/1") for text in progress))
            self.assertTrue(any(text.startswith("Installing verified song 1/1") for text in progress))
            self.assertEqual(list((game / "dlc").glob("*.tmp")), [])

    def test_destination_collision_prevents_every_install_and_preserves_existing_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = game_directory(root)
            dlc = game / "dlc"
            first_name = "Artist - Song - SongKey.psarc"
            (dlc / first_name).write_bytes(b"pre-existing")
            stage = root / "stage"
            first = split_result(stage, first_name, b"new first")
            second_path = stage / "second.psarc"
            second_path.write_bytes(b"new second")
            second = SplitOutput(
                artist="Artist 2",
                title="Song 2",
                song_key="SongKey2",
                path=second_path,
                size_bytes=len(b"new second"),
                sha256=hashlib.sha256(b"new second").hexdigest().upper(),
            )
            staged = SplitResult(stage, first.outputs + (second,), "SOURCEHASH")

            with self.assertRaisesRegex(SplitPackInstallError, "No split songs were installed") as caught:
                install_split_outputs(staged, game)

            self.assertEqual(caught.exception.installed_paths, ())
            self.assertEqual((dlc / first_name).read_bytes(), b"pre-existing")
            self.assertFalse((dlc / "second.psarc").exists())

    def test_partial_install_error_names_completed_files_and_cleans_temporary_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = game_directory(root)
            stage = root / "stage"
            first = split_result(stage, "first.psarc", b"verified first")
            second_path = stage / "second.psarc"
            second_path.write_bytes(b"verified second")
            second = SplitOutput(
                artist="Artist 2",
                title="Song 2",
                song_key="SongKey2",
                path=second_path,
                size_bytes=len(b"verified second"),
                sha256=hashlib.sha256(b"verified second").hexdigest().upper(),
            )
            staged = SplitResult(stage, first.outputs + (second,), "SOURCEHASH")
            original_rename = Path.rename

            def fail_second_rename(path, target):
                if Path(target).name == "second.psarc":
                    raise OSError("simulated install failure")
                return original_rename(path, target)

            with patch.object(Path, "rename", new=fail_second_rename):
                with self.assertRaisesRegex(
                    SplitPackInstallError, "Partial install.*Installed files: first.psarc"
                ) as caught:
                    install_split_outputs(staged, game)

            self.assertEqual(caught.exception.installed_paths, (game / "dlc" / "first.psarc",))
            self.assertEqual((game / "dlc" / "first.psarc").read_bytes(), b"verified first")
            self.assertFalse((game / "dlc" / "second.psarc").exists())
            self.assertEqual(list((game / "dlc").glob(".JonsRockSmithMods_split_*.tmp")), [])

    def test_split_and_install_cleans_temporary_stage_after_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = game_directory(root)
            captured_stage_paths = []

            def fake_split(_source, _metadata, _indices, *, output_parent, **_kwargs):
                stage = Path(output_parent) / "verified-output"
                staged = split_result(stage, "Artist - Song - SongKey.psarc", b"verified psarc")
                captured_stage_paths.append(stage)
                return staged

            with patch("split_pack.split_pack_songs", side_effect=fake_split):
                result = split_and_install_pack(
                    "source.psarc",
                    metadata(),
                    [0],
                    toolkit_directory="unused",
                    game_directory=game,
                )

            self.assertEqual(result.output_directory, game / "dlc")
            self.assertTrue(result.outputs[0].path.is_file())
            self.assertEqual(len(captured_stage_paths), 1)
            self.assertFalse(captured_stage_paths[0].exists())

    def test_readback_requires_exactly_selected_single_song(self):
        with tempfile.TemporaryDirectory() as temporary:
            expected = song(1)
            correct = {"warnings": [], "songs": [expected]}
            self.assertEqual(
                validate_single_song_readback(correct, expected, Path(temporary) / "song.psarc")[0]["title"],
                "Song 1",
            )
            with self.assertRaisesRegex(SplitPackError, "exactly one"):
                validate_single_song_readback(
                    {"warnings": [], "songs": [expected, song(2)]}, expected, Path(temporary) / "pack.psarc"
                )
            with self.assertRaisesRegex(SplitPackError, "mismatch"):
                validate_single_song_readback(
                    {"warnings": [], "songs": [song(2)]}, expected, Path(temporary) / "wrong.psarc"
                )


if __name__ == "__main__":
    unittest.main()
