"""Headless checks for durable user intent and non-interrupting review."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from app import App


class SavedMarkWorkflowTests(unittest.TestCase):
    def make_app(self):
        request = SimpleNamespace(request_id="saved-1", song_key="SongOne")
        app = SimpleNamespace(
            _operation_busy=False, active_game_song_request=None,
            deferred_game_song_requests=set(), rows=[object()],
            metadata=SimpleNamespace(get=Mock()),
            pending_moves_status_var=SimpleNamespace(set=Mock()),
            game_song_request_error=None, after=Mock(), _run=Mock(),
            _check_in_game_song_requests=Mock(),
        )
        app._finish_in_game_song_request = lambda message: App._finish_in_game_song_request(app, message)
        return app, request

    def test_mark_does_not_interrupt_game_or_resolve_archive(self):
        app, request = self.make_app()
        batch = SimpleNamespace(requests=(request,), expired_count=0, invalid_count=0)
        with patch("app.load_pending_game_song_requests", return_value=batch), patch("app.game_is_running", return_value=True):
            App._check_in_game_song_requests(app)
        app._run.assert_not_called()
        self.assertIsNone(app.active_game_song_request)
        self.assertIn("saved", app.pending_moves_status_var.set.call_args.args[0])
        app.after.assert_called_once()

    def test_game_exit_starts_review_and_manual_check_does_not_duplicate_timer(self):
        app, request = self.make_app()
        batch = SimpleNamespace(requests=(request,), expired_count=0, invalid_count=0)
        with patch("app.load_pending_game_song_requests", return_value=batch), patch("app.game_is_running", return_value=False):
            App._check_in_game_song_requests(app, schedule=False)
        self.assertIs(request, app.active_game_song_request)
        app._run.assert_called_once()
        app.after.assert_not_called()

    def test_failed_or_postponed_review_retains_mark_without_prompt_loop(self):
        app, request = self.make_app()
        app.active_game_song_request = request
        with patch("app.discard_game_song_request") as discard:
            App._finish_in_game_song_request(app, "Review postponed")
        discard.assert_not_called()
        self.assertIn(request.request_id, app.deferred_game_song_requests)
        self.assertIsNone(app.active_game_song_request)
        batch = SimpleNamespace(requests=(request,), expired_count=0, invalid_count=0)
        with patch("app.load_pending_game_song_requests", return_value=batch), patch("app.game_is_running", return_value=False):
            App._check_in_game_song_requests(app, schedule=False)
        app._run.assert_not_called()

    def test_saved_single_song_mark_queues_without_second_confirmation(self):
        app, request = self.make_app()
        request.captured_at_unix_ms = 1800000000000
        app.active_game_song_request = request
        app._store = Mock(return_value=SimpleNamespace(songs_root="songs"))
        app._confirm_archive_moves = Mock()
        with patch("app.discard_game_song_request") as discard:
            with patch("app.game_is_running", return_value=False):
                App._queue_in_game_song_archive(app, object())
        discard.assert_not_called()
        self.assertIs(request, app.active_game_song_request)
        self.assertEqual("Queueing in-game selected archive", app._run.call_args.args[0])
        app._confirm_archive_moves.assert_not_called()

    def test_saved_single_song_mark_waits_if_game_reopened(self):
        app, request = self.make_app()
        app.active_game_song_request = request
        app._store = Mock()
        with patch("app.game_is_running", return_value=True):
            App._queue_in_game_song_archive(app, object())
        app._store.assert_not_called()
        app._run.assert_not_called()
        self.assertIsNone(app.active_game_song_request)
        self.assertIn("started again", app.pending_moves_status_var.set.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
