"""Regression tests for async worker results passed to the Tk event poller."""

from __future__ import annotations

import tempfile
import unittest
import inspect
from unittest import mock
from pathlib import Path

from app import (
    App,
    DEFAULT_DIFFICULTY_PERCENT,
    PENDING_MOVE_QUEUE_PATH,
    _validate_worker_result,
    favorite_scan_allows_automatic_move,
)
from favorites.profile import (
    FavoriteResult,
    clear_all_song_favorites,
    load_favorites,
    set_song_favorites,
)
from favorites.test_profile import encoded_profile
from library_refresh import LibraryRefreshResult, library_signature, profile_file_signature
from speed_config import DEFAULT_SPEED_PERCENT
from metadata_cache import MetadataCache
from song_store import MANIFEST_NAME, SongFile, StoreError


class WorkerResultTests(unittest.TestCase):
    def test_app_unconfigured_defaults_are_level_5_and_85_percent_speed(self) -> None:
        self.assertEqual(DEFAULT_DIFFICULTY_PERCENT, 50)
        self.assertEqual(DEFAULT_SPEED_PERCENT, 85)

    def test_difficulty_tab_has_no_inactive_individual_song_editor(self) -> None:
        source = inspect.getsource(App._build)
        self.assertNotIn('text="Individual songs"', source)
        self.assertNotIn('text="Edit selected songs"', source)
        self.assertIn("Edit difficulty / speed for selected pack songs", source)
        self.assertIn("Undo last completed removal", source)
        self.assertIn("WARNING: Queueing moves each checked PSARC", source)

    def test_missing_profile_gets_clear_browse_guidance_without_profile_write(self) -> None:
        class Value:
            def __init__(self, value):
                self.value = value

            def get(self):
                return self.value

            def set(self, value):
                self.value = value

        app = App.__new__(App)
        app.profile_var = Value("")
        app.difficulty_percent_var = Value("75")
        app.difficulty_level_var = Value("7")
        app.allow_automatic_difficulty_var = Value(False)
        app.difficulty_status_var = Value("")
        app._load_profile_difficulty()
        self.assertEqual(app.difficulty_percent_var.get(), "50")
        self.assertIn("use Browse", app.difficulty_status_var.get())
        self.assertIn("Nothing is written", app.difficulty_status_var.get())

    def test_difficulty_save_while_game_runs_gives_close_and_restart_instructions(self) -> None:
        class Value:
            def __init__(self, value):
                self.value = value

            def get(self):
                return self.value

            def set(self, value):
                self.value = value

        with tempfile.TemporaryDirectory() as folder:
            profile = Path(folder) / "TEST_PRFLDB"
            profile.write_bytes(b"unchanged profile")
            app = App.__new__(App)
            app.profile_var = Value(str(profile))
            app.difficulty_percent_var = Value("75")
            app.difficulty_level_var = Value("7")
            app.allow_automatic_difficulty_var = Value(False)
            app.difficulty_status_var = Value("")
            app._run = mock.Mock()

            with mock.patch("app.game_is_running", return_value=True), \
                    mock.patch("app.messagebox.showinfo") as showinfo, \
                    mock.patch("app.messagebox.askyesno") as askyesno:
                app._save_profile_difficulty()

            self.assertIn("Close Rocksmith", app.difficulty_status_var.get())
            self.assertIn("restart Rocksmith", app.difficulty_status_var.get())
            showinfo.assert_called_once()
            askyesno.assert_not_called()
            app._run.assert_not_called()
            self.assertEqual(profile.read_bytes(), b"unchanged profile")

    def test_checked_archive_queue_action_does_not_require_game_to_be_closed(self) -> None:
        candidate = object()
        store = mock.Mock()
        store.game_root = Path("C:/Rocksmith")
        store.review_root = Path("C:/DoNotWant")
        store.songs_root = Path("C:/DoNotWant/songs")
        app = App.__new__(App)
        app._checked_active = mock.Mock(return_value=[candidate])
        app._store = mock.Mock(return_value=store)
        app._confirm_archive_moves = mock.Mock(return_value=True)
        app._run = mock.Mock()

        app._queue_checked_after_close()

        app._confirm_archive_moves.assert_called_once()
        self.assertTrue(app._confirm_archive_moves.call_args.kwargs["queue_after_close"])
        description, worker = app._run.call_args.args
        self.assertEqual("Queueing selected archives", description)
        with mock.patch("app.queue_song_moves", return_value=1) as queue_moves:
            self.assertEqual(1, worker())
        queue_moves.assert_called_once_with(PENDING_MOVE_QUEUE_PATH, store, [candidate])

    def test_immediate_move_is_refused_while_game_runs_with_queue_guidance(self) -> None:
        app = App.__new__(App)
        with mock.patch("app.game_is_running", return_value=True), \
                mock.patch("app.messagebox.showinfo") as showinfo:
            self.assertFalse(app._game_closed_for_immediate_move())
        self.assertIn("Queue checked", showinfo.call_args.args[1])

    def test_profile_discovery_uses_unique_profile_and_accepts_other_steam_accounts(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            userdata = Path(folder)
            profile = userdata / "987654321" / "221680" / "remote" / "OTHER_ACCOUNT_PRFLDB"
            profile.parent.mkdir(parents=True)
            profile.write_bytes(b"profile")
            self.assertEqual(App._discover_profile(userdata), str(profile))
            second = userdata / "123456789" / "221680" / "remote" / "SECOND_PRFLDB"
            second.parent.mkdir(parents=True)
            second.write_bytes(b"profile")
            self.assertEqual(App._discover_profile(userdata), "")

    def test_missing_inventory_result_becomes_clear_error_instead_of_len_crash(self) -> None:
        with self.assertRaisesRegex(StoreError, "returned NoneType; expected a list"):
            _validate_worker_result("Scanning songs", None)

    def test_inventory_result_must_contain_song_file_rows(self) -> None:
        with self.assertRaisesRegex(StoreError, "invalid row"):
            _validate_worker_result("Scanning songs", [None])

    def test_empty_and_populated_inventories_are_valid(self) -> None:
        _validate_worker_result("Scanning songs", [])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "example.psarc"
            row = SongFile("example.psarc", path, 1, "Active")
            _validate_worker_result("Scanning songs", [row])

    def test_non_inventory_worker_results_keep_existing_contract(self) -> None:
        _validate_worker_result("Reading titles", None)

    def test_favorite_update_requires_verified_result(self) -> None:
        with self.assertRaisesRegex(StoreError, "expected a verified profile update result"):
            _validate_worker_result("Updating song favorites", None)
        with self.assertRaisesRegex(StoreError, "expected a verified profile update result"):
            _validate_worker_result("Unfavoriting all songs", None)

    def test_full_refresh_requires_a_validated_result_object(self) -> None:
        with self.assertRaisesRegex(StoreError, "expected a validated refresh result"):
            _validate_worker_result("Full library refresh", None)

    def test_full_refresh_rejects_invalid_favorite_state_data(self) -> None:
        result = LibraryRefreshResult(
            rows=[], favorites=FavoriteResult(False, frozenset(), "unavailable", Path("profile_PRFLDB")),
            favorite_states=[], profile_signature=None, library_signature=(), toolkit_path=None,
            titles_read=0, metadata_failures=0, metadata_ready=0, metadata_diagnostic="",
        )
        with self.assertRaisesRegex(StoreError, "invalid favorite states"):
            _validate_worker_result("Full library refresh", result)

    def test_full_refresh_without_archive_snapshot_is_rejected(self) -> None:
        result = LibraryRefreshResult(
            rows=[], favorites=FavoriteResult(False, frozenset(), "unavailable", Path("profile_PRFLDB")),
            favorite_states={}, profile_signature=None, library_signature=None, toolkit_path=None,
            titles_read=0, metadata_failures=0, metadata_ready=0, metadata_diagnostic="",
        )
        with self.assertRaisesRegex(StoreError, "no verified archive snapshot"):
            _validate_worker_result("Full library refresh", result)


class AutomaticFavoriteMoveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = FavoriteResult(True, frozenset({"known-song"}), "loaded", Path("profile_PRFLDB"))
        self.active = SongFile("known.psarc", Path("C:/game/dlc/known.psarc"), 10, "Active")
        self.reviewed = SongFile("reviewed.psarc", Path("C:/review/songs/reviewed.psarc"), 10, "Do Not Want")

    def test_empty_favorite_list_never_unlocks_bulk_move(self) -> None:
        empty = FavoriteResult(True, frozenset(), "loaded zero", Path("profile_PRFLDB"))
        self.assertFalse(favorite_scan_allows_automatic_move(
            [self.active], empty, {"known.psarc": "Not favorite"}, "same", "same"
        ))

    def test_no_favorite_match_in_active_library_never_unlocks_bulk_move(self) -> None:
        self.assertFalse(favorite_scan_allows_automatic_move(
            [self.active, self.reviewed], self.profile,
            {"known.psarc": "Unknown", "reviewed.psarc": "Favorite"}, "same", "same",
        ))

    def test_fresh_profile_with_active_favorite_match_unlocks_move(self) -> None:
        self.assertTrue(favorite_scan_allows_automatic_move(
            [self.active], self.profile, {"known.psarc": "Favorite"}, "same", "same"
        ))

    def test_changed_profile_signature_keeps_bulk_move_locked(self) -> None:
        self.assertFalse(favorite_scan_allows_automatic_move(
            [self.active], self.profile, {"known.psarc": "Favorite"}, "old", "new"
        ))


class DoNotWantFolderButtonTests(unittest.TestCase):
    def test_app_store_and_destination_label_normalize_manifest_backed_songs_path(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            game = root / "game"
            (game / "dlc").mkdir(parents=True)
            (game / "Rocksmith2014.exe").write_bytes(b"test")
            review = root / "DoNotWant"
            selected_songs = review / "songs"
            selected_songs.mkdir(parents=True)
            (review / MANIFEST_NAME).write_text('{"version":1,"songs":{}}', encoding="utf-8")

            class Variable:
                def __init__(self, value):
                    self.value = value

                def get(self):
                    return self.value

                def set(self, value):
                    self.value = value

            app = App.__new__(App)
            app.game_var = Variable(str(game))
            app.review_var = Variable(str(selected_songs))

            store = app._store()
            destination = app._store_destination_label()

            self.assertEqual(review.resolve(), store.review_root)
            self.assertEqual(review.resolve(), Path(app.review_var.get()))
            self.assertEqual(str(selected_songs.resolve()), destination)

    def test_open_button_creates_and_opens_normalized_songs_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            songs_folder = Path(folder) / "DoNotWant" / "songs"
            app = App.__new__(App)
            app._store = lambda: type("Store", (), {"songs_root": songs_folder})()
            app.status_var = type("Variable", (), {"set": lambda self, value: setattr(self, "value", value)})()

            with mock.patch("app.os.startfile", create=True) as startfile:
                app._open_do_not_want_folder()

            self.assertTrue(songs_folder.is_dir())
            startfile.assert_called_once_with(str(songs_folder))
            self.assertIn(str(songs_folder), app.status_var.value)


class FavoriteButtonWorkflowTests(unittest.TestCase):
    class Variable:
        def __init__(self, value):
            self.value = value

        def get(self):
            return self.value

        def set(self, value):
            self.value = value

    def _make_app(self, folder: str):
        root = Path(folder)
        game = root / "game"
        dlc = game / "dlc"
        dlc.mkdir(parents=True)
        (game / "Rocksmith2014.exe").write_bytes(b"synthetic game marker")
        archive = dlc / "Artist_Song_p.psarc"
        archive_bytes = b"synthetic archive data"
        archive.write_bytes(archive_bytes)
        stat = archive.stat()
        row = SongFile(
            archive.name,
            archive,
            stat.st_size,
            "Active",
            (stat.st_size, stat.st_mtime_ns, stat.st_dev, stat.st_ino),
        )
        profile = root / "Selected_PRFLDB"
        profile.write_bytes(encoded_profile(["ExistingSong"]))
        metadata = MetadataCache(root / "metadata_cache.json")
        metadata.entries[str(archive.resolve()).casefold()] = {
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "device": stat.st_dev,
            "file_id": stat.st_ino,
            "metadata": {
                "song_count": 2,
                "manifest_count": 1,
                "warnings": [],
                "songs": [
                    {"song_key": "Artist_SongA", "artist": "Artist", "title": "Song A"},
                    {"song_key": "Artist_SongB", "artist": "Artist", "title": "Song B"},
                ],
            },
        }

        app = App.__new__(App)
        app._operation_busy = False
        app.game_var = self.Variable(str(game))
        app.review_var = self.Variable(str(root / "DoNotWant"))
        app.profile_var = self.Variable(str(profile))
        app.favorite_result = load_favorites(profile)
        app.favorite_profile_signature = profile_file_signature(profile)
        app.favorite_library_signature = library_signature([row])
        app.metadata = metadata
        app.favorite_write_status_var = self.Variable("")
        app.visible_rows = {}
        app._stop_preview = lambda: None
        queued = []
        app._run = lambda description, worker: queued.append((description, worker))
        return app, row, archive, archive_bytes, profile, queued, root

    def test_add_selected_multitrack_archive_confirms_and_updates_profile_only(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            app, row, archive, archive_bytes, profile, queued, root = self._make_app(folder)
            with mock.patch("app.messagebox.askyesno", return_value=True) as confirm, \
                    mock.patch(
                        "app.set_song_favorites",
                        side_effect=lambda *args, **kwargs: set_song_favorites(
                            *args,
                            backup_dir=root / "backups",
                            running_check=lambda: False,
                            **kwargs,
                        ),
                    ):
                app._update_selected_favorites(True, [row])
                self.assertEqual(1, len(queued))
                description, worker = queued[0]
                self.assertEqual("Updating song favorites", description)
                confirmation_text = confirm.call_args.args[1]
                self.assertIn("Song A", confirmation_text)
                self.assertIn("Selected_PRFLDB", confirmation_text)
                self.assertIn("does not move or delete", confirmation_text)
                result = worker()
                self.assertEqual(("Artist_SongA", "Artist_SongB"), result.changed_song_keys)
                self.assertEqual(b"synthetic archive data", archive.read_bytes())
                self.assertEqual(
                    frozenset({"existingsong", "artist_songa", "artist_songb"}),
                    load_favorites(profile).favorite_song_keys,
                )
                self.assertTrue(result.backup_path.is_file())

    def test_selected_archive_change_after_confirmation_blocks_profile_write(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            app, row, archive, _archive_bytes, profile, queued, root = self._make_app(folder)
            with mock.patch("app.messagebox.askyesno", return_value=True), \
                    mock.patch(
                        "app.set_song_favorites",
                        side_effect=lambda *args, **kwargs: set_song_favorites(
                            *args,
                            backup_dir=root / "backups",
                            running_check=lambda: False,
                            **kwargs,
                        ),
                    ):
                app._update_selected_favorites(True, [row])

            before = profile.read_bytes()
            archive.write_bytes(b"replaced archive")
            with self.assertRaisesRegex(StoreError, "archive identity changed"):
                queued[0][1]()
            self.assertEqual(before, profile.read_bytes())
            self.assertFalse((root / "backups").exists())

    def test_selected_profile_change_after_confirmation_blocks_write(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            app, row, _archive, _archive_bytes, profile, queued, root = self._make_app(folder)
            with mock.patch("app.messagebox.askyesno", return_value=True), \
                    mock.patch(
                        "app.set_song_favorites",
                        side_effect=lambda *args, **kwargs: set_song_favorites(
                            *args,
                            backup_dir=root / "backups",
                            running_check=lambda: False,
                            **kwargs,
                        ),
                    ):
                app._update_selected_favorites(True, [row])

            changed_profile = encoded_profile(["ExternallyAddedFavorite"])
            profile.write_bytes(changed_profile)
            with self.assertRaisesRegex(StoreError, "profile changed after confirmation"):
                queued[0][1]()
            self.assertEqual(changed_profile, profile.read_bytes())
            self.assertFalse((root / "backups").exists())

    def test_unfavorite_all_confirms_once_and_clears_entire_profile_only(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            app, _row, archive, archive_bytes, profile, queued, root = self._make_app(folder)
            with mock.patch("app.simpledialog.askstring", return_value="UNFAVORITE ALL") as confirm, \
                    mock.patch(
                        "app.clear_all_song_favorites",
                        side_effect=lambda path, **kwargs: clear_all_song_favorites(
                            path,
                            backup_dir=root / "backups",
                            running_check=lambda: False,
                            **kwargs,
                        ),
                    ):
                app._unfavorite_all()
                self.assertEqual(1, len(queued))
                description, worker = queued[0]
                self.assertEqual("Unfavoriting all songs", description)
                prompt = confirm.call_args.args[1]
                self.assertIn("1 favorite entries", prompt)
                self.assertIn("UNFAVORITE ALL", prompt)
                self.assertIn("no song files are moved or deleted", prompt)
                result = worker()

            self.assertEqual(("ExistingSong",), result.changed_song_keys)
            self.assertEqual(frozenset(), load_favorites(profile).favorite_song_keys)
            self.assertTrue(result.backup_path.is_file())
            self.assertEqual(archive_bytes, archive.read_bytes())

    def test_unfavorite_all_requires_exact_typed_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            app, _row, _archive, _archive_bytes, _profile, queued, _root = self._make_app(folder)
            with mock.patch("app.simpledialog.askstring", return_value="yes"):
                app._unfavorite_all()
            self.assertEqual([], queued)
            self.assertIn("cancelled", app.favorite_write_status_var.value)

    def test_unfavorite_all_rechecks_profile_after_typed_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            app, _row, _archive, _archive_bytes, profile, queued, root = self._make_app(folder)
            with mock.patch("app.simpledialog.askstring", return_value="UNFAVORITE ALL"), \
                    mock.patch(
                        "app.clear_all_song_favorites",
                        side_effect=lambda path, **kwargs: clear_all_song_favorites(
                            path,
                            backup_dir=root / "backups",
                            running_check=lambda: False,
                            **kwargs,
                        ),
                    ):
                app._unfavorite_all()
                profile.write_bytes(encoded_profile(["ChangedAfterConfirmation"]))
                with self.assertRaisesRegex(StoreError, "profile changed after confirmation"):
                    queued[0][1]()
            self.assertFalse((root / "backups").exists())


if __name__ == "__main__":
    unittest.main()
