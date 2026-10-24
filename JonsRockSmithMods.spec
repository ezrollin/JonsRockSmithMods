# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_all

# Public builds never include the optional modified RSMods native DLL.
cryptography_datas, cryptography_binaries, cryptography_hiddenimports = collect_all('cryptography')
audio_datas, audio_binaries, audio_hiddenimports = collect_all('_sounddevice_data')

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=cryptography_binaries + audio_binaries,
    datas=[
        ('metadata\\bin\\MetadataExtractor.exe', 'metadata\\bin'),
        ('metadata\\bin\\MetadataExtractor-x86.exe', 'metadata\\bin'),
        ('metadata\\bin\\MetadataExtractor-x64.exe', 'metadata\\bin'),
        ('split_helper\\bin\\SplitPackHelper.exe', 'split_helper\\bin'),
        ('split_helper\\bin\\SplitPackHelper-x86.exe', 'split_helper\\bin'),
        ('split_helper\\bin\\SplitPackHelper-x64.exe', 'split_helper\\bin'),
        ('LICENSE', '.'),
        ('THIRD_PARTY_NOTICES.md', '.'),
        ('licenses', 'licenses'),
    ] + cryptography_datas + audio_datas,
    hiddenimports=cryptography_hiddenimports + audio_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='JonsRockSmithMods',
    version='version_info.txt',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='JonsRockSmithMods',
)
