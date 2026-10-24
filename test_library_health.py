"""Health scan schema, failure visibility, cancellation and read-only identity tests."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from library_health import _validate_health_document, inspect_archive_health, scan_library, _apply_audio_inventory, _installation_audio_inventory, inventory_audio_banks


class HealthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name, 'song.psarc')
        self.path.write_bytes(b'unchanged')

    def document(self, issues=None):
        issues = issues or []
        return dict(schema_version=1, archive_path=str(self.path.resolve()), manifest_count=1,
                    arrangements=[dict(title='Song', artist='Artist', song_key='key', arrangement_name='Lead', note_count=10)],
                    issues=issues, status='issue_found' if any(i['severity']=='error' for i in issues) else 'needs_review' if issues else 'no_issue_found')

    def test_valid_and_dependency_review_documents(self):
        _validate_health_document(self.document(), self.path.resolve())
        doc = self.document([dict(severity='review', code='external_audio_bank', path='bank', detail='Dependency may be valid')])
        _validate_health_document(doc, self.path.resolve())
        self.assertEqual(doc['status'], 'needs_review')

    def test_rejects_contradictory_status(self):
        doc = self.document([dict(severity='error', code='empty_chart', path='chart', detail='No notes')])
        doc['status'] = 'no_issue_found'
        with self.assertRaisesRegex(ValueError, 'contradicts'):
            _validate_health_document(doc, self.path.resolve())

    def test_rejects_wrong_archive_and_bools_as_counts(self):
        doc = self.document()
        doc['archive_path'] += '.other'
        with self.assertRaisesRegex(ValueError, 'does not match'):
            _validate_health_document(doc, self.path.resolve())
        doc = self.document()
        doc['arrangements'][0]['note_count'] = True
        with self.assertRaisesRegex(ValueError, 'arrangement'):
            _validate_health_document(doc, self.path.resolve())

    def test_read_only_success_and_helper_mode(self):
        before = self.path.read_bytes()
        with patch('library_health.resolve_toolkit_helper', return_value=Path('helper.exe')), patch('library_health.subprocess.run', return_value=SimpleNamespace(returncode=0, stdout=json.dumps(self.document()), stderr='')) as run:
            result = inspect_archive_health(self.path, self.temp.name)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertIn('--health', run.call_args.args[0])
        self.assertIn('file_identity', result)
        self.assertIn('not decoded', result['limitations'])

    def test_changed_archive_rejected(self):
        def changed(*args, **kwargs):
            self.path.write_bytes(b'replaced during scan')
            return SimpleNamespace(returncode=0, stdout=json.dumps(self.document()), stderr='')
        with patch('library_health.resolve_toolkit_helper', return_value=Path('helper.exe')), patch('library_health.subprocess.run', side_effect=changed):
            with self.assertRaisesRegex(RuntimeError, 'changed during'):
                inspect_archive_health(self.path, self.temp.name)

    def test_helper_failure_is_review_row_with_full_traceback(self):
        with patch('library_health.inspect_archive_health', side_effect=RuntimeError('bad archive')):
            reports = scan_library([self.path], self.temp.name)
        self.assertEqual(reports[0].status, 'needs_review')
        self.assertIn('Traceback', reports[0].issues[0]['detail'])
        self.assertEqual(reports[0].as_dict()['archive_path'], str(self.path.resolve()))

    def test_cancel_deduplicate_progress(self):
        event = threading.Event()
        second = self.path.with_name('second.psarc')
        progress = []
        def callback(done, total, path):
            progress.append((done, total, path))
            if done:
                event.set()
        with patch('library_health.inspect_archive_health', return_value=self.document()) as inspect:
            reports = scan_library([self.path, self.path, second], self.temp.name, callback, event)
        self.assertEqual(len(reports), 1)
        self.assertEqual(inspect.call_count, 1)
        self.assertEqual(progress[0][0:2], (0, 2))
        self.assertEqual(progress[1][0:2], (1, 2))

    def test_already_cancelled_does_not_start(self):
        event = threading.Event()
        event.set()
        with patch('library_health.inspect_archive_health') as inspect:
            self.assertEqual(scan_library([self.path], self.temp.name, cancel_event=event), [])
            inspect.assert_not_called()


class AudioInventoryTests(unittest.TestCase):
    setUp = HealthTests.setUp
    document = HealthTests.document
    def missing_document(self):
        document = self.document([dict(severity="review", code="external_audio_bank", path="audio/windows/song_test.bnk", detail="External bank")])
        document["arrangements"][0]["song_bank"] = "song_test.bnk"
        return document

    def test_complete_inventory_promotes_missing_dependency_with_song_count(self):
        document = self.missing_document()
        _apply_audio_inventory(document, dict(complete=True, archive_count=2, banks=set(), failures=[]))
        self.assertEqual(document["issues"][0]["code"], "missing_installed_audio_bank")
        self.assertEqual(document["issues"][0]["severity"], "review")
        self.assertEqual(document["affected_song_keys"], ["key"])
        self.assertEqual(document["missing_installed_audio_bank_count"], 1)
        self.assertEqual(document["track_count"], 1)

    def test_any_inventory_failure_withholds_absence_proof(self):
        document = self.missing_document()
        _apply_audio_inventory(document, dict(complete=False, archive_count=2, banks=set(), failures=[{"detail":"unreadable archive"}]))
        self.assertEqual(document["issues"][0]["code"], "external_audio_bank")
        self.assertEqual(document["missing_installed_audio_bank_count"], 0)

    def test_matching_bank_is_review_without_missing_verdict(self):
        document = self.missing_document()
        _apply_audio_inventory(document, dict(complete=True, archive_count=2, banks={"audio/windows/song_test.bnk"}, failures=[]))
        self.assertEqual(document["issues"][0]["code"], "external_audio_bank")
        self.assertIn("Matching bank", document["issues"][0]["detail"])
        self.assertEqual(document["missing_installed_audio_bank_count"], 0)

    def test_discovery_excludes_backup_and_project_and_includes_root(self):
        root=Path(self.temp.name)
        for name in ["dlc/active.psarc", "dlc/backup/hidden.psarc", "dlc/DoNotWant/hidden.psarc", "audio.psarc", "JonsRockSmithMods/fixture.psarc"]:
            path=root/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fixture")
        with patch("library_health.inventory_audio_banks", return_value={}) as inventory:
            _installation_audio_inventory([root/"dlc/active.psarc"], root/"Toolkit")
        paths=set(inventory.call_args.args[0])
        self.assertEqual(paths, {root/"dlc/active.psarc", root/"audio.psarc", self.path})
        self.assertIn(root/"audio.psarc", paths)
        self.assertIn(root/"dlc/active.psarc", paths)
        self.assertFalse(any("hidden" in p.name or "fixture.psarc" == p.name for p in paths))

    def test_inventory_once_per_library_scan(self):
        other=self.path.with_name("other.psarc")
        with patch("library_health._installation_audio_inventory", return_value=dict(complete=True,archive_count=2,banks=set(),failures=[])) as inventory, patch("library_health.inspect_archive_health", side_effect=lambda *a: self.document()):
            reports=scan_library([self.path,other],self.temp.name)
        self.assertEqual(len(reports),2)
        self.assertEqual(inventory.call_count,1)


if __name__ == '__main__':
    unittest.main()
