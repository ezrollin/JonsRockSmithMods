from __future__ import annotations

import json
import queue
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from library_health_view import export_report, health_report, result_details, scan_worker, selected_active_rows
from song_store import SongFile


class LibraryHealthViewTests(unittest.TestCase):
    def test_real_active_snapshot_can_queue_and_duplicate_results_are_deduplicated(self):
        row = SongFile("one.psarc", Path("one.psarc"), 8, "Active", (8, 2, 3, 4))
        result = {"archive_path": str(row.path)}
        selected = selected_active_rows([result, result], [row])
        self.assertEqual([row], selected)
        self.assertIs(row, selected[0])

    def test_reports_cannot_queue_unknown_or_backup_archives(self):
        row = SongFile("one.psarc", Path("one.psarc"), 8, "Do Not Want", (8, 2, 3, 4))
        for result in ({"archive_path": "one.psarc"}, {"archive_path": "unknown.psarc"}, {}):
            with self.subTest(result=result), self.assertRaisesRegex(ValueError, "active scanned library"):
                selected_active_rows([result], [row])

    def test_report_records_partial_scan_and_original_identities(self):
        row = SongFile("one.psarc", Path("one.psarc"), 8, "active", (8, 2, 3, 4))
        report = health_report([{"status": "needs_review"}], [row, row], "start", False)
        self.assertFalse(report["scan_completed"])
        self.assertEqual(2, report["requested_archive_count"])
        self.assertEqual(1, report["checked_archive_count"])
        self.assertEqual([8, 2, 3, 4], report["source_snapshots"][0]["identity"])
        self.assertIn("cannot prove playback", report["limitation"])

    def test_details_preserve_all_diagnostic_fields(self):
        result = {"status": "needs_review", "issues": [{"code": "MISSING_DEPENDENCY", "path": "x.bnk"}],
                  "future_field": {"notes": "full detail"}}
        self.assertEqual(result, json.loads(result_details(result)))

    def test_export_is_readable_and_replaces_existing_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "report.json"
            path.write_text("old")
            export_report(path, {"results": [{"summary": "Björk"}]})
            self.assertEqual("Björk", json.loads(path.read_text(encoding="utf-8"))["results"][0]["summary"])
            self.assertEqual([path], list(path.parent.iterdir()))

    def test_export_failure_preserves_old_report_and_cleans_temporary(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "report.json"
            path.write_text("old")
            with patch("library_health_view.os.replace", side_effect=OSError("locked")):
                with self.assertRaisesRegex(OSError, "locked"):
                    export_report(path, {"new": True})
            self.assertEqual("old", path.read_text())
            self.assertEqual([path], list(path.parent.iterdir()))

    def test_worker_posts_data_without_tk_and_passes_cancellation(self):
        messages = queue.Queue()
        cancellation = threading.Event()
        rows = [SimpleNamespace(path=Path("one.psarc"))]
        def scan(actual_rows, toolkit, progress, event):
            self.assertIs(rows, actual_rows)
            self.assertEqual(Path("toolkit"), toolkit)
            self.assertIs(cancellation, event)
            progress(1, 1, rows[0].path)
            return [SimpleNamespace(as_dict=lambda: {"archive_path": "one.psarc", "status": "needs_review"})]
        with patch("library_health_view.scan_library", side_effect=scan):
            scan_worker(rows, Path("toolkit"), messages, cancellation, Path("log"))
        self.assertEqual(("progress", (1, 1, "one.psarc")), messages.get_nowait())
        self.assertEqual("done", messages.get_nowait()[0])

    def test_worker_exposes_full_traceback_on_error(self):
        messages = queue.Queue()
        with patch("library_health_view.scan_library", side_effect=RuntimeError("helper missing")), \
                patch("library_health_view.logging.exception") as logger:
            scan_worker([], None, messages, threading.Event(), Path("log"))
        event, detail = messages.get_nowait()
        self.assertEqual("error", event)
        self.assertIn("Traceback", detail)
        self.assertIn("RuntimeError: helper missing", detail)
        logger.assert_called_once()


if __name__ == "__main__":
    unittest.main()
