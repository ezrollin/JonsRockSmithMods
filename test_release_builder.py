"""Public staging regressions use synthetic files and never launch applications."""
import json
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from release_builder import (
    DOCUMENT_FILES, EXE_BATCHES, EXE_NAME, HELPER_FILES, PORTAUDIO_NAME,
    RUNTIME_FILES, SOURCE_BATCHES, audit_stage, build_exe_stage, build_source_stage, _scan_bytes,
)


class ReleaseBuilderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        for name in RUNTIME_FILES + HELPER_FILES + DOCUMENT_FILES + SOURCE_BATCHES:
            path = self.project / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(('safe fixture ' + name).encode())
        (self.project / 'licenses').mkdir()
        (self.project / 'licenses' / 'LICENSE.txt').write_text('dependency copyright and license')

    def test_source_stage_copies_only_explicit_runtime_and_canonical_launchers(self):
        for name in ('private.log', 'tuner_settings.json', 'test_hidden.py', 'notes.md', '.env'):
            (self.project / name).write_text('excluded local state')
        native = self.project / 'native/artifacts/xinput1_3.dll'
        native.parent.mkdir(parents=True)
        native.write_bytes(b'local native candidate')
        stage = self.root / 'source'
        report = build_source_stage(self.project, stage)
        self.assertTrue(report['passed'])
        actual = {p.relative_to(stage).as_posix() for p in stage.rglob('*') if p.is_file()}
        expected = set(RUNTIME_FILES + HELPER_FILES + DOCUMENT_FILES + SOURCE_BATCHES)
        expected.update(('licenses/LICENSE.txt', 'PUBLIC_AUDIT.json'))
        self.assertEqual(expected, actual)
        for name in SOURCE_BATCHES:
            self.assertEqual((self.project / name).read_bytes(), (stage / name).read_bytes())
        self.assertTrue(json.loads((stage / 'PUBLIC_AUDIT.json').read_text())['passed'])

    def test_audit_rejection_does_not_promote_or_print_secret(self):
        secret = 'ghp_' + 'A' * 32
        (self.project / 'app.py').write_text(secret)
        stage = self.root / 'source'
        with self.assertRaisesRegex(ValueError, 'github_token') as failure:
            build_source_stage(self.project, stage)
        self.assertNotIn(secret, str(failure.exception))
        self.assertFalse(stage.exists())
        self.assertEqual([], list(self.root.glob('.public-release-*')))

    def test_existing_stage_is_preserved_and_missing_helper_stops_build(self):
        stage = self.root / 'source'
        stage.mkdir()
        sentinel = stage / 'keep.txt'
        sentinel.write_text('keep')
        with self.assertRaisesRegex(ValueError, 'new directory'):
            build_source_stage(self.project, stage)
        self.assertEqual('keep', sentinel.read_text())
        (self.project / HELPER_FILES[0]).unlink()
        with self.assertRaisesRegex(ValueError, 'Missing or unsafe'):
            build_source_stage(self.project, self.root / 'other')

    def test_audit_detects_identifiers_and_user_state_without_returning_values(self):
        stage = self.root / 'audit'
        stage.mkdir()
        private_id = '7656119' + '9' * 10
        username = 'PrivateBuildUser'
        (stage / 'data.txt').write_text(private_id + ' ' + username)
        (stage / 'tuner_settings.json').write_text('{}')
        with patch('release_builder.Path.home', return_value=Path('/home') / username):
            report = audit_stage(stage)
        self.assertFalse(report['passed'])
        encoded = json.dumps(report)
        self.assertNotIn(private_id, encoded)
        self.assertNotIn(username, encoded)
        self.assertEqual({'steam_account_id', 'private_username', 'forbidden_artifact'},
                         {row['category'] for row in report['findings']})

    def test_identifiers_in_forbidden_filenames_are_redacted_in_report(self):
        stage = self.root / 'audit'
        stage.mkdir()
        profile_name = ('a' * 32) + '_PRFLDB'
        (stage / profile_name).write_bytes(b'private profile fixture')
        report = audit_stage(stage)
        self.assertFalse(report['passed'])
        self.assertNotIn(profile_name, json.dumps(report))
        self.assertIn('[redacted-profile_id]', report['findings'][0]['path'])

    def test_existing_audit_report_is_not_a_privacy_scan_exemption(self):
        stage = self.root / 'audit'
        stage.mkdir()
        (stage / 'PUBLIC_AUDIT.json').write_text('ghp_' + 'A' * 32)
        self.assertFalse(audit_stage(stage)['passed'])

    def test_exact_vendor_parser_header_is_reviewed_but_key_material_is_blocked(self):
        path = EXE_NAME + ':cryptography.hazmat.primitives.serialization.ssh:constant'
        header = b'-----BEGIN ' + b'OPENSSH PRIVATE KEY-----'
        findings, reviewed = [], []
        _scan_bytes(header, path, findings, reviewed)
        self.assertEqual([], findings)
        self.assertEqual('private_key', reviewed[0]['category'])
        self.assertEqual(hashlib.sha256(header).hexdigest(), reviewed[0]['sha256'])
        findings, reviewed = [], []
        _scan_bytes(header + b'\n' + b'A' * 64, path, findings, reviewed)
        self.assertEqual([], reviewed)
        self.assertEqual('private_key', findings[0]['category'])

    def test_vendor_review_does_not_apply_to_application_source(self):
        header = b'-----BEGIN ' + b'OPENSSH PRIVATE KEY-----'
        findings, reviewed = [], []
        _scan_bytes(header, 'app.py', findings, reviewed)
        self.assertEqual([], reviewed)
        self.assertEqual('private_key', findings[0]['category'])
        findings, reviewed = [], []
        private_path = ('C:' + chr(92) + 'Users' + chr(92) + 'OtherPrivateUser' + chr(92) + 'notes').encode()
        _scan_bytes(private_path, '_internal/cryptography/hazmat/bindings/_rust.pyd', findings, reviewed)
        self.assertEqual([], reviewed)
        self.assertEqual('windows_user_path', findings[0]['category'])

    def test_network_review_is_bound_to_exact_vendor_constant(self):
        definition = b'10.' + b'0.0.0/8'
        vendor_path = EXE_NAME + ':ipaddress:constant'
        findings, reviewed = [], []
        _scan_bytes(definition, vendor_path, findings, reviewed)
        self.assertEqual([], findings)
        self.assertEqual(1, len(reviewed))
        for data, path in ((definition + b' changed', vendor_path),
                           (definition, EXE_NAME + ':app:constant')):
            findings, reviewed = [], []
            _scan_bytes(data, path, findings, reviewed)
            self.assertTrue(findings)
            self.assertEqual([], reviewed)

    def test_vendor_hash_or_count_change_requires_fresh_review(self):
        data = ('C:' + chr(92) + 'Users' + chr(92) + 'FixtureCI' + chr(92) + 'build').encode()
        path = '_internal/vendor.pyd'
        rule = (path, 'windows_user_path', hashlib.sha256(data).hexdigest(), 1, 'test provenance')
        with patch('release_builder.REVIEWED_VENDOR_ITEMS', (rule,)):
            findings, reviewed = [], []
            _scan_bytes(data, path, findings, reviewed)
            self.assertFalse(findings)
            self.assertEqual(1, len(reviewed))
            for changed in (data + b'x', data + b' ' + data):
                findings, reviewed = [], []
                _scan_bytes(changed, path, findings, reviewed)
                self.assertTrue(findings)
                self.assertFalse(reviewed)

    def fake_package(self):
        package = self.root / 'built'
        (package / '_internal/_sounddevice_data/portaudio-binaries').mkdir(parents=True)
        (package / EXE_NAME).write_bytes(b'synthetic executable')
        (package / '_internal/python311.dll').write_bytes(b'runtime')
        for name in (PORTAUDIO_NAME, 'libportaudio64bit-asio.dll', 'libportaudio32bit.dll',
                     'libportaudioarm64.dll', 'libportaudio.dylib', 'README.md'):
            (package / '_internal/_sounddevice_data/portaudio-binaries' / name).write_bytes(b'audio')
        (package / '_internal/native/artifacts').mkdir(parents=True)
        (package / '_internal/native/artifacts/xinput1_3.dll').write_bytes(b'optional native')
        (package / '_internal/debug.pdb').write_bytes(b'debug symbols')
        (package / 'unrelated.log').write_bytes(b'local log')
        return package

    def test_exe_stage_removes_native_symbols_and_unneeded_audio_platforms(self):
        package = self.fake_package()
        stage = self.root / 'exe'
        with patch('release_builder._scan_frozen_python', return_value=37) as audit:
            report = build_exe_stage(package, stage, self.project)
        self.assertTrue(report['passed'])
        self.assertEqual(37, report['code_objects_checked'])
        audit.assert_called_once()
        files = {p.name for p in stage.rglob('*') if p.is_file()}
        self.assertIn(PORTAUDIO_NAME, files)
        for forbidden in ('xinput1_3.dll', 'debug.pdb', 'libportaudio64bit-asio.dll',
                          'libportaudio32bit.dll', 'libportaudioarm64.dll', 'libportaudio.dylib', 'unrelated.log'):
            self.assertNotIn(forbidden, files)
        for name in EXE_BATCHES:
            self.assertEqual((self.project / name).read_bytes(), (stage / name).read_bytes())
        self.assertNotIn('Setup Python.bat', files)

    def test_public_exe_requires_non_asio_runtime_and_successful_frozen_audit(self):
        package = self.fake_package()
        with patch('release_builder._scan_frozen_python', side_effect=ValueError('bad frozen archive')):
            with self.assertRaisesRegex(ValueError, 'bad frozen archive'):
                build_exe_stage(package, self.root / 'bad', self.project)
        self.assertFalse((self.root / 'bad').exists())
        (package / '_internal/_sounddevice_data/portaudio-binaries' / PORTAUDIO_NAME).unlink()
        with self.assertRaisesRegex(ValueError, 'non-ASIO x64'):
            build_exe_stage(package, self.root / 'missing', self.project)

    def test_source_and_exe_stage_cannot_overlap_scanned_input_subtrees(self):
        with self.assertRaisesRegex(ValueError, 'license inputs'):
            build_source_stage(self.project, self.project / 'licenses/stage')
        package = self.fake_package()
        with self.assertRaisesRegex(ValueError, 'runtime inputs'):
            build_exe_stage(package, package / '_internal/stage', self.project)


if __name__ == '__main__':
    unittest.main()
