"""Build public distributions from explicit inputs; never copy local user state."""
from __future__ import annotations

import json
import hashlib
import marshal
import os
import re
import shutil
import tempfile
import types
import zipfile
from pathlib import Path


RUNTIME_FILES = (
    'app.py', 'archive_details.py', 'audio_preview.py', 'difficulty_batch.py',
    'game_song_requests.py', 'game_tuning_settings.py', 'ini_settings.py', 'intonation_analysis.py',
    'intonation_checker.py', 'library_health.py', 'library_health_view.py',
    'library_refresh.py', 'metadata_cache.py', 'native_install.py', 'pending_moves.py',
    'song_store.py', 'song_table.py', 'speed_config.py', 'split_pack.py', 'track_selection.py',
    'startup_config.py', 'toolkit_runtime.py', 'tuner_settings.py', 'ui_layout.py',
    'waveform_input.py', 'favorites/__init__.py', 'favorites/profile.py',
    'metadata/metadata.py', 'metadata/difficulty.py', 'requirements.txt',
)
HELPER_FILES = tuple(f'{directory}/bin/{name}{suffix}.exe'
                     for directory, name in (('metadata', 'MetadataExtractor'),
                                              ('split_helper', 'SplitPackHelper'))
                     for suffix in ('', '-x86', '-x64'))
DOCUMENT_FILES = ('README.md', 'INSTALL.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md')
EXE_NAME = 'JonsRockSmithMods.exe'
PORTAUDIO_NAME = 'libportaudio64bit.dll'

SOURCE_BATCHES = ('Setup Python.bat', 'Run Python.bat', 'Run EXE.bat', 'Launch JonsRockSmithMods.bat')
EXE_BATCHES = ('Run EXE.bat', 'Launch JonsRockSmithMods.bat')

# Values are counted and categorized; matched secrets/identifiers are never returned.
PRIVATE_PATTERNS = {
    'windows_user_path': re.compile(rb'(?i)[a-z]:[\\/]+users[\\/]+(?!public\b|default\b|username\b|user\b|<)[a-z0-9_. -]+'),
    'steam_account_id': re.compile(rb'(?<!\d)7656119\d{10}(?!\d)'),
    'profile_id': re.compile(rb'(?i)(?<![0-9a-f])[0-9a-f]{32}_PRFLDB'),
    'private_ipv4': re.compile(rb'\b(?:192\.168|10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b'),
    'github_token': re.compile(rb'\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b'),
    'private_key': re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'aws_access_key': re.compile(rb'\bAKIA[A-Z0-9]{16}\b'),
}

# Reviewed on the inspected dependency versions. Each rule binds an exact file
# or exact compiled constant, location, category AND match count. Changed bytes
# require a fresh review; no vendor/module has a general privacy exemption.
REVIEWED_VENDOR_ITEMS = (
    ('_internal/bcrypt/_bcrypt.pyd', 'windows_user_path',
     '898d1fe05e287d66bd379f26eb49d27a260f14666c7dc8b1719dae8c489abff0', 36,
     'bcrypt 5.0.0 wheel RECORD verified; upstream CI Cargo registry build paths'),
    ('_internal/cryptography/hazmat/bindings/_rust.pyd', 'windows_user_path',
     '92f58bb4f01a75a0ef4dd9425dc2a96ddfe25fc93fd9dc11ea8f0d05b7a4f085', 144,
     'cryptography 46.0.3 wheel RECORD verified; upstream CI Cargo registry build paths'),
    ('_internal/cryptography/hazmat/primitives/serialization/ssh.py', 'private_key',
     '84f579a1b1739f3d108457d714f39e43ccba32cbab034c5531088b9cb11212cf', 1,
     'cryptography 46.0.3 wheel RECORD verified; SSH parser header literal, no key material'),
    ('JonsRockSmithMods.exe:cryptography.hazmat.primitives.serialization.ssh:constant', 'private_key',
     '03d104c669e3c7b6be7f989db8b12c8b910d3be8c1e2a73c9369d3cc0ba803b5', 1,
     'cryptography 46.0.3 exact standalone SSH parser header constant; no key material'),
    ('JonsRockSmithMods.exe:ipaddress:constant', 'private_ipv4',
     '93997fe8a8121085052fd8c9a6515591714f1b745733ac379f691a9074d518a5', 1,
     'CPython 3.11.9 ipaddress standard private-network definition'),
    ('JonsRockSmithMods.exe:ipaddress:constant', 'private_ipv4',
     '59fb47b55edaafdb39c6a47128a8156ab33ea55dd29ebb53b4e3bd0c3900ba34', 1,
     'CPython 3.11.9 ipaddress standard private-network definition'),
    ('JonsRockSmithMods.exe:ipaddress:constant', 'private_ipv4',
     'da3508145559327a834270888340bada8c5cb343aa2ed4c996276be352066030', 1,
     'CPython 3.11.9 ipaddress standard private-network definition'),
    ('JonsRockSmithMods.exe:numpy.__config__:constant', 'windows_user_path',
     'ab1f4406d6a1acd58a2d428dbd82993a0805a6f992f1b7bdd8615fa2a2582226', 1,
     'NumPy 1.26.4 wheel build configuration; upstream CI build path'),
    ('JonsRockSmithMods.exe:urllib.request:constant', 'private_ipv4',
     'a3bce7cd7a7fa119dd0cd1186019d89c47be7d3beb4e2eccf26fd83d706ebd8c', 1,
     'CPython 3.11.9 _proxy_bypass_winreg_override documentation example'),
)


class PublicAuditError(ValueError):
    def __init__(self, report):
        self.report = report
        categories = sorted({row['category'] for row in report['findings']})
        super().__init__('Public release audit rejected categories: ' + ', '.join(categories))


def _copy_file(source: Path, destination: Path, root: Path) -> None:
    if source.is_symlink() or not source.is_file():
        raise ValueError(f'Missing or unsafe release input: {source.name}')
    try:
        source.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f'Release input escapes its root: {source.name}') from exc
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def _copy_documents(project: Path, destination: Path) -> None:
    for name in DOCUMENT_FILES:
        _copy_file(project / name, destination / name, project)
    licenses = project / 'licenses'
    if licenses.is_symlink() or not licenses.is_dir():
        raise ValueError('The verified licenses directory is missing or unsafe')
    files = sorted(licenses.rglob('*'))
    if not any(path.is_file() for path in files):
        raise ValueError('The verified licenses directory is empty')
    for source in files:
        if source.is_symlink():
            raise ValueError('A license input is a symbolic link')
        if source.is_file():
            _copy_file(source, destination / source.relative_to(project), project)


def _private_patterns():
    patterns = dict(PRIVATE_PATTERNS)
    username = Path.home().name
    if username.casefold() not in ('user', 'username', 'public', 'default'):
        patterns['private_username'] = re.compile(re.escape(username.encode('utf-8')), re.I)
        patterns['private_username_utf16'] = re.compile(re.escape(username.encode('utf-16le')), re.I)
    return patterns


def _safe_report_path(path: str) -> str:
    encoded = path.encode('utf-8', errors='replace')
    for category, pattern in _private_patterns().items():
        encoded = pattern.sub(('[redacted-' + category + ']').encode(), encoded)
    return encoded.decode('utf-8', errors='replace')


def _scan_bytes(data: bytes, path: str, findings: list, reviewed: list | None = None) -> None:
    patterns = _private_patterns()
    for category, pattern in patterns.items():
        count = len(pattern.findall(data))
        if count:
            item = {'path': _safe_report_path(path), 'category': category, 'count': count}
            digest = hashlib.sha256(data).hexdigest()
            rule = next((rule for rule in REVIEWED_VENDOR_ITEMS
                         if rule[:4] == (path, category, digest, count)), None)
            if rule is not None and reviewed is not None:
                reviewed.append({**item, 'sha256': digest, 'reason': rule[4]})
            else:
                findings.append(item)


def _scan_code(code, path: str, findings: list, reviewed: list | None = None) -> int:
    if not isinstance(code, types.CodeType):
        return 0
    count = 1
    _scan_bytes(code.co_filename.encode('utf-8', errors='replace'), path + ':code_filename', findings, reviewed)
    for value in code.co_consts:
        if isinstance(value, types.CodeType):
            count += _scan_code(value, path, findings, reviewed)
        elif isinstance(value, str):
            _scan_bytes(value.encode('utf-8', errors='replace'), path + ':constant', findings, reviewed)
        elif isinstance(value, bytes):
            _scan_bytes(value, path + ':constant', findings, reviewed)
    return count


def _scan_frozen_python(executable: Path, findings: list, reviewed: list | None = None) -> int:
    try:
        from PyInstaller.archive.readers import CArchiveReader
    except ImportError as exc:
        raise ValueError('Install PyInstaller to audit frozen Python code before publishing') from exc
    archive = CArchiveReader(str(executable))
    count = 0
    for name, entry in archive.toc.items():
        if entry[-1] == 's':
            count += _scan_code(marshal.loads(archive.extract(name)), executable.name + ':' + name, findings, reviewed)
        elif entry[-1] == 'z':
            embedded = archive.open_embedded_archive(name)
            for module in embedded.toc:
                content = embedded.extract(module)
                if isinstance(content, bytes):
                    _scan_bytes(content, executable.name + ':' + module, findings, reviewed)
                else:
                    count += _scan_code(content, executable.name + ':' + module, findings, reviewed)
    base_library = executable.parent / '_internal' / 'base_library.zip'
    if base_library.is_file():
        with zipfile.ZipFile(base_library) as archive:
            for name in archive.namelist():
                if name.endswith('.pyc'):
                    count += _scan_code(marshal.loads(archive.read(name)[16:]), 'base_library.zip:' + name, findings, reviewed)
    return count


def audit_stage(stage: Path, *, frozen_python: bool = False) -> dict:
    stage = Path(stage)
    findings = []
    reviewed = []
    files = []
    for path in sorted(stage.rglob('*')):
        relative = path.relative_to(stage).as_posix()
        safe_relative = _safe_report_path(relative)
        if path.is_symlink():
            findings.append({'path': safe_relative, 'category': 'symbolic_link', 'count': 1})
            continue
        if not path.is_file():
            continue
        files.append(path)
        if (path.suffix.casefold() in ('.pdb', '.psarc', '.log')
                or '_prfldb' in path.name.casefold()
                or path.name.casefold() in ('xinput1_3.dll', 'metadata_cache.json',
                                             'tuner_settings.json', 'pending_song_moves.json',
                                             'jonsrocksmithmods.ini', 'rsmods.ini')):
            findings.append({'path': safe_relative, 'category': 'forbidden_artifact', 'count': 1})
        _scan_bytes(path.read_bytes(), relative, findings, reviewed)
    code_objects = _scan_frozen_python(stage / EXE_NAME, findings, reviewed) if frozen_python else 0
    return {'schema_version': 1, 'passed': not findings, 'file_count': len(files),
            'total_bytes': sum(path.stat().st_size for path in files),
            'code_objects_checked': code_objects, 'findings': findings,
            'reviewed_vendor_findings': reviewed,
            'limitations': 'Pattern checks detect known forms; they do not prove absence of every secret or replace file allowlists.'}


def _build(stage: Path, populate, *, frozen_python=False) -> dict:
    stage = Path(stage).absolute()
    if stage.exists() or stage.is_symlink():
        raise ValueError('Release stage must be a new directory')
    stage.parent.mkdir(parents=True, exist_ok=True)
    # Only this newly created, owned scratch directory is automatically cleaned.
    with tempfile.TemporaryDirectory(prefix='.public-release-', dir=stage.parent) as temporary:
        scratch = Path(temporary).resolve()
        if scratch.parent != stage.parent.resolve():
            raise ValueError('Unexpected staging scratch location')
        payload = scratch / 'payload'
        payload.mkdir()
        populate(payload)
        report = audit_stage(payload, frozen_python=frozen_python)
        if not report['passed']:
            raise PublicAuditError(report)
        (payload / 'PUBLIC_AUDIT.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        os.rename(payload, stage)
        return report


def build_source_stage(project: Path, stage: Path) -> dict:
    project = Path(project).resolve()
    if Path(stage).resolve().is_relative_to(project / 'licenses'):
        raise ValueError('Public staging must not be inside source license inputs')
    def populate(destination):
        for name in RUNTIME_FILES + HELPER_FILES:
            _copy_file(project / name, destination / name, project)
        _copy_documents(project, destination)
        for name in SOURCE_BATCHES:
            _copy_file(project / name, destination / name, project)
    return _build(stage, populate)


def build_exe_stage(pybuilt: Path, stage: Path, project: Path) -> dict:
    pybuilt, project = Path(pybuilt).resolve(), Path(project).resolve()
    internal = pybuilt / '_internal'
    if Path(stage).resolve().is_relative_to(internal):
        raise ValueError('Public staging must not be inside packaged runtime inputs')
    if not internal.is_dir() or internal.is_symlink():
        raise ValueError('Packaged _internal runtime is missing or unsafe')
    if not (internal / 'python311.dll').is_file():
        raise ValueError('Packaged Python 3.11 runtime is missing')
    portaudio = internal / '_sounddevice_data' / 'portaudio-binaries' / PORTAUDIO_NAME
    if not portaudio.is_file():
        raise ValueError('The non-ASIO x64 PortAudio runtime is missing')
    def populate(destination):
        _copy_file(pybuilt / EXE_NAME, destination / EXE_NAME, pybuilt)
        for source in sorted(internal.rglob('*')):
            relative = source.relative_to(pybuilt)
            if source.is_symlink():
                raise ValueError('Packaged runtime contains a symbolic link')
            if not source.is_file():
                continue
            if source.suffix.casefold() == '.pdb' or source.name.casefold() == 'xinput1_3.dll':
                continue
            if source.name.casefold().startswith('libportaudio') and source.name != PORTAUDIO_NAME:
                continue
            if 'portaudio-binaries' in relative.parts:
                if source.name != PORTAUDIO_NAME and source.name != 'README.md':
                    continue
            _copy_file(source, destination / relative, pybuilt)
        _copy_documents(project, destination)
        for name in EXE_BATCHES:
            _copy_file(project / name, destination / name, project)
    return _build(stage, populate, frozen_python=True)
