"""Headless checks for actionable, bounded Tk callback exception reporting."""
import sys
import unittest
from unittest import mock

from app import App


class CallbackErrorTests(unittest.TestCase):
    def app(self):
        app = App.__new__(App)
        app.status_var = mock.Mock()
        return app

    def test_full_original_traceback_is_logged_and_displayed(self):
        app = self.app()
        try:
            raise TypeError("Toplevel object is not callable")
        except TypeError:
            failure = sys.exc_info()
        with mock.patch("app.logging.error") as logger, mock.patch("app.messagebox.showerror") as dialog:
            app.report_callback_exception(*failure)
        self.assertIs(failure[2], logger.call_args.kwargs["exc_info"][2])
        detail = dialog.call_args.args[1]
        self.assertIn("Traceback", detail)
        self.assertIn("TypeError: Toplevel object is not callable", detail)
        self.assertIn("Full log:", detail)
        self.assertIn("UI action failed", app.status_var.set.call_args.args[0])

    def test_repeated_event_failures_do_not_flood_dialogs_or_logs(self):
        app = self.app()
        error = RuntimeError("same failed event")
        with mock.patch("app.logging.error") as logger, mock.patch("app.messagebox.showerror") as dialog:
            for _ in range(1000):
                app.report_callback_exception(RuntimeError, error, None)
        self.assertEqual(1, dialog.call_count)
        self.assertEqual(10, logger.call_count)
        self.assertEqual(1000, app._callback_failure_count)

    def test_distinct_errors_keep_bounded_signatures_and_dialog_count(self):
        app = self.app()
        with mock.patch("app.logging.error") as logger, mock.patch("app.messagebox.showerror") as dialog:
            for index in range(100):
                app.report_callback_exception(ValueError, ValueError(f"failure {index}"), None)
        self.assertEqual(64, len(app._callback_failure_signatures))
        self.assertEqual(3, dialog.call_count)
        self.assertEqual(64, logger.call_count)

    def test_reporting_ui_failure_does_not_trigger_recursive_error(self):
        app = self.app()
        app.status_var.set.side_effect = RuntimeError("status unavailable")
        with mock.patch("app.logging.error"), mock.patch("app.logging.exception") as logger, \
                mock.patch("app.messagebox.showerror", side_effect=RuntimeError("dialog unavailable")):
            app.report_callback_exception(ValueError, ValueError("original"), None)
        self.assertEqual(2, logger.call_count)

    def test_repeated_status_failures_are_also_bounded(self):
        app = self.app()
        app.status_var.set.side_effect = RuntimeError("status unavailable")
        with mock.patch("app.logging.error"), mock.patch("app.logging.exception") as logger, \
                mock.patch("app.messagebox.showerror"):
            for _ in range(1000):
                app.report_callback_exception(ValueError, ValueError("original"), None)
        self.assertEqual(10, logger.call_count)


if __name__ == "__main__":
    unittest.main()
