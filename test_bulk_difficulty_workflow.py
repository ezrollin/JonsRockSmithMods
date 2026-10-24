"""Headless integration of global preflight, one guarded commit and outcome validation."""
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from app import App, _validate_worker_result
from difficulty_batch import DifficultyBatch, DifficultyArchiveResult, BulkDifficultyResult
from favorites.profile import SongDifficultyUpdateResult
from song_store import StoreError


class BulkDifficultyWorkflowTests(unittest.TestCase):
    def make_app(self):
        path=Path('TEST_PRFLDB').resolve()
        row=SimpleNamespace(location='Active',path=Path('song.psarc'))
        value=lambda x: SimpleNamespace(get=lambda:x)
        app=SimpleNamespace(difficulty_level_var=value('4'),
            allow_automatic_difficulty_var=value(False), game_var=value('.'),
            _difficulty_snapshot=lambda:(path,('signature',1,2,'hash')),
            _store=lambda:SimpleNamespace(inventory=lambda:[row]),rows=[row],
            metadata=SimpleNamespace(get=lambda path:None),events=SimpleNamespace(put=Mock()),_run=Mock())
        return app,path

    def test_valid_batch_commits_once_and_returns_explicit_partial_outcome(self):
        app,path=self.make_app()
        batch=DifficultyBatch({'good':{'intro':4}},(
            DifficultyArchiveResult(Path('good.psarc'),'good.psarc',('good',)),
            DifficultyArchiveResult(Path('bad.psarc'),'bad.psarc',(),'conflicting maxima','Artist - Title')))
        update=SongDifficultyUpdateResult(path,4,False,('good',),1,Path('backup'), 'newhash')
        with patch('app.library_signature',return_value='same'),patch('app.messagebox.askyesno',return_value=True),\
            patch('app.find_toolkit_path',return_value=Path('.')),patch('app.collect_global_difficulty',return_value=batch),\
            patch('app.profile_file_signature',return_value=('signature',1,2,'hash')),\
            patch('app.set_all_song_difficulty',return_value=update) as save:
            App._save_profile_difficulty(app)
            result=app._run.call_args.args[1]()
        save.assert_called_once()
        self.assertEqual(batch.arrangements,save.call_args.args[1])
        self.assertEqual(('good',),save.call_args.args[2])
        self.assertIn('PARTIAL SUCCESS',result.summary())
        self.assertIn('Artist - Title',result.details())
        _validate_worker_result('Saving difficulty setting',result)

    def test_no_verified_maps_never_calls_profile_writer(self):
        app,path=self.make_app()
        batch=DifficultyBatch({},(DifficultyArchiveResult(Path('bad.psarc'),'bad.psarc',(),'broken'),))
        with patch('app.library_signature',return_value='same'),patch('app.messagebox.askyesno',return_value=True),\
            patch('app.find_toolkit_path',return_value=Path('.')),patch('app.collect_global_difficulty',return_value=batch),\
            patch('app.set_all_song_difficulty') as save:
            App._save_profile_difficulty(app)
            with self.assertRaisesRegex(StoreError,'no profile changes'):
                app._run.call_args.args[1]()
        save.assert_not_called()

    def test_global_requires_coverage_wrapper_instead_of_plain_profile_result(self):
        result=SongDifficultyUpdateResult(Path('p'),4,False,(),0,None,'hash')
        with self.assertRaises(StoreError):
            _validate_worker_result('Saving difficulty setting',result)
        _validate_worker_result('Saving selected song difficulty',result)


if __name__=='__main__':unittest.main()
