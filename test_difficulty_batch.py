"""Synthetic archive preflight and encrypted profile-copy partial application tests."""
import hashlib
import tempfile
import unittest
from pathlib import Path
from difficulty_batch import BulkDifficultyResult, collect_global_difficulty
from favorites.profile import _decode_profile, set_all_song_difficulty
from favorites.test_song_difficulty import _encode_document, _profile_document
from song_store import SongFile


class DifficultyBatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.rows = []
        for name in ('good.psarc', 'exgirlfriend_p.psarc', 'other.psarc'):
            path = self.root / name
            path.write_bytes(b'synthetic archive')
            self.rows.append(SongFile(name, path, path.stat().st_size, 'Active'))

    def document(self, path, key='ArrangementA', maxima=None, warnings=None):
        return {'schema_version': 1, 'archive_path': str(path.resolve()), 'manifest_count': 1,
                'arrangements': [{'arrangement_id': key, 'arrangement_name': 'Lead',
                                  'phrase_max_difficulties': maxima or {'intro': 4, 'verse': 8}}],
                'warnings': warnings or []}

    def test_bad_archive_does_not_hide_valid_maps_or_failure(self):
        def extract(path, **kwargs):
            return self.document(path, warnings=['Repeated phrase a has conflicting maxima 18 and 16']
                                 if path.name == 'exgirlfriend_p.psarc' else [])
        events = []
        batch = collect_global_difficulty(self.rows[:2], self.root, extract,
                                          lambda *event: events.append(event),
                                          title_lookup=lambda path: "Artist — Song Title " + path.name)
        self.assertEqual('PARTIAL', batch.status)
        self.assertEqual(1, batch.successful_archive_count)
        self.assertEqual(1, batch.failed_archive_count)
        self.assertEqual({'ArrangementA': {'intro': 4, 'verse': 8}}, batch.arrangements)
        self.assertIn('exgirlfriend_p.psarc', batch.details())
        self.assertIn('18 and 16', batch.details())
        self.assertIn('Artist — Song Title', batch.details())
        self.assertEqual(2, len(events))

    def test_conflict_removes_both_archives_even_unrelated_arrangements(self):
        batch = collect_global_difficulty(self.rows, self.root,
            lambda path, **kwargs: self.document(path,
                key='Other' if path.name == 'other.psarc' else 'ArrangementA',
                maxima={'intro': 9} if path.name == 'exgirlfriend_p.psarc' else None))
        self.assertEqual({'Other': {'intro': 4, 'verse': 8}}, batch.arrangements)
        self.assertEqual(2, batch.failed_archive_count)
        self.assertIn('no maximum was guessed', batch.details())

    def test_title_cache_failure_does_not_abort_collection(self):
        def lookup(path):
            raise OSError('title cache unavailable')
        batch = collect_global_difficulty(self.rows[:1], self.root,
            lambda path, **kwargs: self.document(path), title_lookup=lookup)
        self.assertEqual('SUCCESS', batch.status)
        self.assertEqual('', batch.archives[0].title)

    def test_equal_duplicate_chart_is_deduplicated(self):
        batch = collect_global_difficulty(self.rows[:2], self.root,
                                          lambda path, **kwargs: self.document(path))
        self.assertEqual('SUCCESS', batch.status)
        self.assertEqual(2, batch.successful_archive_count)
        self.assertEqual(1, len(batch.arrangements))

    def test_all_failed_returns_full_report_and_no_maps(self):
        def extract(path, **kwargs):
            raise OSError('cannot read SNG')
        batch = collect_global_difficulty(self.rows, self.root, extract)
        self.assertEqual('FAILED', batch.status)
        self.assertFalse(batch.arrangements)
        self.assertEqual(3, batch.failed_archive_count)
        self.assertIn('cannot read SNG', batch.details())

    def test_synthetic_profile_copy_changes_only_valid_saved_phrases(self):
        document = _profile_document()
        skipped = document['Songs']['RemovedSongArrangement']['DynamicDifficulty']['Phrases'].copy()
        profile = self.root / 'copy_PRFLDB'
        original = _encode_document(document)
        profile.write_bytes(original)
        batch = collect_global_difficulty(self.rows[:2], self.root,
            lambda path, **kwargs: self.document(path,
                key='RemovedSongArrangement' if path.name == 'exgirlfriend_p.psarc' else 'ArrangementA',
                warnings=['conflicting phrase maxima'] if path.name == 'exgirlfriend_p.psarc' else []))
        update = set_all_song_difficulty(profile, batch.arrangements, list(batch.arrangements), 10, False,
            expected_profile_sha256=hashlib.sha256(original).hexdigest(),
            backup_dir=self.root/'backups', running_check=lambda: False)
        after = _decode_profile(profile.read_bytes())
        self.assertEqual(skipped, after['Songs']['RemovedSongArrangement']['DynamicDifficulty']['Phrases'])
        self.assertEqual(4, after['Songs']['ArrangementA']['DynamicDifficulty']['Phrases']['intro']['Diff'])
        self.assertEqual(10., after['Options']['DDSightreadLevel'])
        self.assertEqual(original, update.backup_path.read_bytes())
        report = BulkDifficultyResult(update, batch)
        self.assertIn('PARTIAL SUCCESS', report.summary())
        self.assertIn('Saved global level 10/10', report.summary())
        self.assertEqual(0, report.unchanged_arrangement_count)
        self.assertIn('Skipped saved phrase records were preserved', report.details())


if __name__ == '__main__':
    unittest.main()
