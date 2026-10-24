"""Toolkit discovery and bounded audio-preview regression tests."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
import wave
from types import SimpleNamespace
from unittest.mock import patch

import audio_preview
from audio_preview import PreviewError, cleanup_preview_directory, prepare_preview
from metadata.metadata import default_toolkit_directory, extract_metadata, find_toolkit_path
from metadata_cache import MetadataCache


class MetadataCacheTests(unittest.TestCase):
    def test_replaced_archive_with_preserved_size_and_timestamp_invalidates_titles(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            archive = root / "song.psarc"
            archive.write_bytes(b"song")
            cache = MetadataCache(root / "metadata.json")
            with patch("metadata_cache.extract_metadata", return_value={"songs": []}):
                cache.scan(archive)
            self.assertIsNotNone(cache.get(archive))
            original_stat = archive.stat()
            replacement = root / "replacement.psarc"
            replacement.write_bytes(b"evil")
            os.utime(replacement, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
            replacement.replace(archive)
            self.assertIsNone(cache.get(archive))

    def test_legacy_cache_without_file_identity_is_rescanned(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            archive = root / "song.psarc"
            archive.write_bytes(b"song")
            stat = archive.stat()
            cache_path = root / "metadata.json"
            cache_path.write_text(
                json.dumps({
                    str(archive.resolve()).casefold(): {
                        "size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "metadata": {"songs": []}
                    }
                }),
                encoding="utf-8",
            )
            cache = MetadataCache(cache_path)
            self.assertIsNone(cache.get(archive))

    def test_archive_change_during_title_scan_is_not_cached(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            archive = root / "song.psarc"
            archive.write_bytes(b"song")
            cache_path = root / "metadata.json"
            cache = MetadataCache(cache_path)

            def mutate_archive(_archive, **_kwargs):
                archive.write_bytes(b"changed")
                return {"songs": []}

            with patch("metadata_cache.extract_metadata", side_effect=mutate_archive):
                with self.assertRaisesRegex(RuntimeError, "changed during title scan"):
                    cache.scan(archive)
            self.assertFalse(cache_path.exists())

    def test_relocated_titles_can_be_saved_as_one_batch(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source.psarc"
            target = root / "review" / "source.psarc"
            source.write_bytes(b"song")
            cache_path = root / "metadata.json"
            cache = MetadataCache(cache_path)
            with patch("metadata_cache.extract_metadata", return_value={"songs": []}):
                cache.scan(source)
            cache.relocate(source, target, save=False)
            self.assertIsNone(cache.get(source))
            target_key = str(target.resolve(strict=False)).casefold()
            source_key = str(source.resolve(strict=False)).casefold()
            persisted = json.loads(cache_path.read_text(encoding="utf-8"))
            self.assertIn(source_key, persisted)
            self.assertNotIn(target_key, persisted)
            cache.save()
            self.assertIn(target_key, cache.entries)


class ToolkitDiscoveryTests(unittest.TestCase):
    def test_default_toolkit_follows_current_windows_user_home(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            user_home = Path(folder) / "Users" / "NewUser"
            toolkit = user_home / "Desktop" / "RocksmithToolkit"
            toolkit.mkdir(parents=True)
            (toolkit / "RocksmithToolkitLib.dll").write_bytes(b"test")

            with patch("metadata.metadata.Path.home", return_value=user_home):
                self.assertEqual(toolkit, default_toolkit_directory())
                self.assertEqual(toolkit.resolve(), find_toolkit_path(user_home / "Game"))

    def test_prefers_toolkit_next_to_game_when_old_default_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            game = Path(folder) / "Rocksmith2014"
            toolkit = game / "RocksmithToolkit"
            toolkit.mkdir(parents=True)
            (toolkit / "RocksmithToolkitLib.dll").write_bytes(b"test")
            self.assertEqual(toolkit.resolve(), find_toolkit_path(game, Path(folder) / "missing"))

    def test_missing_toolkit_error_lists_searched_paths(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(FileNotFoundError, "RocksmithToolkitLib.dll") as error:
                find_toolkit_path(Path(folder) / "game", Path(folder) / "missing")
            self.assertIn("missing", str(error.exception))

    def test_metadata_helper_is_launched_without_a_console_window(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            toolkit = root / "toolkit"
            toolkit.mkdir()
            header = bytearray(256)
            header[0:2] = b"MZ"
            header[0x3C:0x40] = (128).to_bytes(4, "little")
            header[128:132] = b"PE\0\0"
            header[132:134] = (0x14C).to_bytes(2, "little")
            header[152:154] = (0x10B).to_bytes(2, "little")
            (toolkit / "RocksmithToolkitLib.dll").write_bytes(header)
            (root / "MetadataExtractor.exe").write_bytes(b"helper fixture")
            archive = root / "song.psarc"
            archive.write_bytes(b"fixture")
            with patch(
                "metadata.metadata.subprocess.run",
                return_value=SimpleNamespace(returncode=0, stdout='{"songs": []}', stderr=""),
            ) as run:
                extract_metadata(archive, toolkit=toolkit, helper=root / "MetadataExtractor.exe")
            self.assertEqual(
                getattr(__import__("subprocess"), "CREATE_NO_WINDOW", 0x08000000),
                run.call_args.kwargs["creationflags"],
            )


class AudioPreviewTests(unittest.TestCase):
    def test_non_psarc_is_rejected_before_toolkit_execution(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "not-a-song.txt"
            source.write_text("test", encoding="utf-8")
            with self.assertRaisesRegex(PreviewError, "psarc"):
                prepare_preview(source, folder)

    def test_cleanup_ignores_directories_outside_its_owned_temp_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            owned = Path(folder) / "other"
            owned.mkdir()
            marker = owned / "keep.txt"
            marker.write_text("keep", encoding="utf-8")
            cleanup_preview_directory(owned)
            self.assertTrue(marker.is_file())


class PreviewExtractionSafetyTests(unittest.TestCase):
    def _fixtures(self, folder: str):
        root = Path(folder)
        archive = root / "pack.psarc"
        archive_bytes = b"unchanged Rocksmith archive fixture"
        archive.write_bytes(archive_bytes)
        toolkit = root / "toolkit"
        tools = toolkit / "tools"
        tools.mkdir(parents=True)
        for name in (
            "packer.exe", "ww2ogg.exe", "revorb.exe", "packed_codebooks_aoTuV_603.bin",
            "oggCut.exe", "oggdec.exe",
        ):
            target = toolkit / name if name == "packer.exe" else tools / name
            target.write_bytes(b"fixture")
        return archive, archive_bytes, toolkit

    def _fake_run(self, extracted_names, *, packer_returncode=0, packer_stderr=""):
        def run(args, **_kwargs):
            tool = Path(args[0]).name.casefold()
            if tool == "packer.exe":
                output = Path(next(value[3:] for value in args if value.startswith("-o=")))
                output.mkdir(parents=True, exist_ok=True)
                for relative in extracted_names:
                    target = output / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(b"decoded fixture")
                return SimpleNamespace(returncode=packer_returncode, stdout="unpack output", stderr=packer_stderr)
            if tool == "oggcut.exe":
                Path(args[-1]).write_bytes(b"clipped ogg")
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            if tool == "oggdec.exe":
                output = Path(args[args.index("-o") + 1])
                with wave.open(str(output), "wb") as stream:
                    stream.setnchannels(1)
                    stream.setsampwidth(2)
                    stream.setframerate(8000)
                    stream.writeframes(b"\x00\x00" * 8000)
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            raise AssertionError(f"Unexpected tool: {args[0]}")
        return run

    def test_no_ogg_error_names_archive_stage_and_extracted_file_types(self):
        with tempfile.TemporaryDirectory() as folder:
            archive, original_bytes, toolkit = self._fixtures(folder)
            created = []
            real_mkdtemp = audio_preview.tempfile.mkdtemp
            with patch("audio_preview.tempfile.mkdtemp") as mkdtemp:
                mkdtemp.side_effect = lambda *args, **kwargs: self._remember_temp(real_mkdtemp, created, *args, **kwargs)
                with patch("audio_preview.subprocess.run", side_effect=self._fake_run(["song.sng", "song.xblock"])):
                    with self.assertRaises(PreviewError) as raised:
                        prepare_preview(archive, toolkit, track_count=144)
            self.assertIn("no decoded .ogg audio", str(raised.exception))
            self.assertIn("does not prove the PSARC is corrupt", str(raised.exception))
            self.assertIn("Stage: Audio enumeration", str(raised.exception))
            self.assertIn(str(archive.resolve()), str(raised.exception))
            self.assertIn("file_types=.sng=1, .xblock=1", raised.exception.diagnostics)
            self.assertEqual(original_bytes, archive.read_bytes())
            self.assertEqual(1, len(created))
            self.assertFalse(created[0].exists())

    @staticmethod
    def _remember_temp(original, created, *args, **kwargs):
        path = Path(original(*args, **kwargs))
        created.append(path)
        return str(path)

    def test_multiple_ogg_candidates_are_never_guessed(self):
        with tempfile.TemporaryDirectory() as folder:
            archive, original_bytes, toolkit = self._fixtures(folder)
            created = []
            real_mkdtemp = audio_preview.tempfile.mkdtemp
            with patch("audio_preview.tempfile.mkdtemp") as mkdtemp:
                mkdtemp.side_effect = lambda *args, **kwargs: self._remember_temp(real_mkdtemp, created, *args, **kwargs)
                with patch("audio_preview.subprocess.run", side_effect=self._fake_run(["a.ogg", "b.ogg"])):
                    with self.assertRaisesRegex(PreviewError, "cannot safely map") as raised:
                        prepare_preview(archive, toolkit, track_count=1)
            self.assertIn("No arbitrary track was played", str(raised.exception))
            self.assertEqual(original_bytes, archive.read_bytes())
            self.assertFalse(created[0].exists())

    def test_one_audio_file_cannot_stand_in_for_a_multi_song_archive(self):
        with tempfile.TemporaryDirectory() as folder:
            archive, original_bytes, toolkit = self._fixtures(folder)
            with patch("audio_preview.subprocess.run", side_effect=self._fake_run(["single.ogg"])):
                with self.assertRaisesRegex(PreviewError, "lists 144 song records"):
                    prepare_preview(archive, toolkit, track_count=144)
            self.assertEqual(original_bytes, archive.read_bytes())

    def test_unique_single_track_audio_still_previews_and_source_stays_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            archive, original_bytes, toolkit = self._fixtures(folder)
            with patch("audio_preview.subprocess.run", side_effect=self._fake_run(["single.ogg"])):
                asset = prepare_preview(archive, toolkit, track_count=1)
            self.assertTrue(asset.wav_path.is_file())
            self.assertAlmostEqual(1.0, asset.seconds)
            self.assertEqual(original_bytes, archive.read_bytes())
            cleanup_preview_directory(asset.working_directory)
            self.assertFalse(asset.working_directory.exists())

    def test_unpack_failure_includes_stage_and_bounded_tool_diagnostics(self):
        with tempfile.TemporaryDirectory() as folder:
            archive, original_bytes, toolkit = self._fixtures(folder)
            noisy = "x" * 5000
            created = []
            real_mkdtemp = audio_preview.tempfile.mkdtemp
            with patch("audio_preview.tempfile.mkdtemp") as mkdtemp:
                mkdtemp.side_effect = lambda *args, **kwargs: self._remember_temp(real_mkdtemp, created, *args, **kwargs)
                with patch(
                    "audio_preview.subprocess.run",
                    side_effect=self._fake_run([], packer_returncode=7, packer_stderr=noisy),
                ):
                    with self.assertRaisesRegex(PreviewError, "unpacking this archive") as raised:
                        prepare_preview(archive, toolkit, track_count=1)
            self.assertIn("Stage: Unpack", str(raised.exception))
            self.assertLessEqual(len(raised.exception.diagnostics), 2200)
            self.assertEqual(original_bytes, archive.read_bytes())
            self.assertFalse(created[0].exists())


if __name__ == "__main__":
    unittest.main()
