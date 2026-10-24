# JonsRockSmithMods 0.22.0

Windows companion for Rocksmith 2014 Remastered: reversible song removal, whole-pack splitting, difficulty/speed settings, and explicit guitar intonation tuning.

## Download and install

Download either complete package from [Releases](https://github.com/ezrollin/JonsRockSmithMods/releases/latest):

- **Windows EXE ZIP:** no Python installation needed. Keep `JonsRockSmithMods.exe` and `_internal` together. Open `Run EXE.bat`.
- **Python ZIP:** install **Python 3.11, 64-bit**, including the Windows Python launcher. Open `Setup Python.bat` once, then `Run Python.bat`. Setup creates a private `.venv` and installs pinned requirements. It downloads Python dependencies; the app does not download songs.

Extract the `JonsRockSmithMods` folder here:

```text
C:\Program Files (x86)\Steam\steamapps\common\Rocksmith2014\JonsRockSmithMods
```

If Steam uses a different library, place it inside your actual Rocksmith2014 folder. `Launch JonsRockSmithMods.bat` explicitly lets you choose EXE or Python. It does not silently substitute an old EXE when you choose Python. Read [INSTALL.md](INSTALL.md) before changing files.

## Verify YOUR file links first

Open **Setup & startup** and use **Browse** to verify this computer's Rocksmith folder, Do Not Want backup folder, and Steam profile ending in `*_PRFLDB`. Paths differ between Windows users, Steam accounts, drives and libraries. Do not copy another person's user/profile paths or edit source code to change these links. Archive/title/split/difficulty features also need a separately installed compatible Rocksmith Toolkit; details are in INSTALL.md.

**Setup & startup displays the running version, EXE/Python mode, and exact running path.** The EXE's Windows file properties also show version 0.22.0. Use these to distinguish an old copy.

## Features and limits

- Check or select song archives in the companion while the game runs; queue reversible removal. After Rocksmith closes, confirmed whole archives move to Do Not Want and can be restored. Keep/reopen the companion to process work.
- To remove individual tracks from a pack, split/replace the complete pack with verified single-song files first, then queue only the songs you want moved.
- **Native in-game marking is optional and separate.** With a locally built compatible Jons RSMods extension, the game displays an F10 hint in menus. Let the desired song preview start in Learn a Song, then press F10; saved/error feedback appears. Review the saved mark after game exit. It identifies the preview, not an unverified currently playing arrangement. A multi-song archive affects all contained tracks.
- The optional modified RSMods DLL is **not included in public downloads**: upstream has no stated redistribution license. Developer build/provenance instructions are in native/README.md. Manual companion selection/removal works without that extension. Upstream RSMods installation is separate.
- Split a complete, self-contained pack into verified single-song archives. Only after verification does the original pack move to Do Not Want. Missing recordings cannot be reconstructed by splitting.
- Read-only health checks show charts, embedded audio, missing installed dependencies and detailed reports. File size and structural checks do not prove game playback.
- Sight Reading levels 1-10: writes require Rocksmith closed, exact chart mappings and a verified profile backup. Bulk results report success/partial success/failure and skipped songs. Global starting level and automatic difficulty still affect all songs; skipped saved phrase values stay unchanged.
- Per-song speed defaults to 85% when newly configured; native enforcement requires the compatible optional extension. It does not rewrite recording assets.
- Guitar input starts automatically for a recognized or explicitly selected cable. Choose string and OPEN/FRET 12 yourself. `<` lowers and `>` raises pitch. Fret 12 compares with twice captured open frequency. Personal cents offsets default to zero. Shared driver access and actual guitar accuracy require user validation.
- Tuner reference chart updates for every listed tuning style and shows open/12th-fret Hz for all six strings. E Standard shows low E2 at 82.41/164.81 Hz and A2 at 110.00/220.00 Hz. Color scale shows flat, near, in-tune, and sharp ranges.
- Queue warnings state that whole checked PSARCs move to Do Not Want after game exit; nothing is permanently deleted. Undo queued moves before processing or restore the identity-checked last completed queue batch afterward, including after restarting the app.

## Source, privacy and licensing

The complete Python release ZIP includes the six helper EXEs. GitHub's automatic source-code archive is the developer source; build helpers using metadata/build.ps1 and split_helper/build.ps1 if using that archive instead.

Public packages are allowlisted. They exclude songs, recordings, Steam profiles/IDs, personal configuration, metadata caches, logs, audit reports, credentials, native DLLs, PDBs, old releases and build/test scratch folders. Required runtime libraries and license notices are intentional dependencies. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and [LICENSE](LICENSE).

The app never sends your library/profile files to GitHub. Runtime logs/preferences/backups use the current Windows user's Documents/JonsRockSmithMods folder. Source developers can run `py -3.11 -m unittest discover -q`; tests use temporary fixtures unless an explicitly documented native integration variable is set.
