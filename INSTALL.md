# Install JonsRockSmithMods

## 1. Choose a download

Use the complete Windows EXE ZIP if Python is not installed. It contains the EXE and required `_internal` runtime; neither can be separated. Use the complete Python ZIP for editable Python files plus helper EXEs.

Python users need **Python 3.11 x64 (64-bit)** with Tkinter and the Windows `py` launcher, then `Setup Python.bat`. The tested baseline is CPython 3.11.9 x64; requirements are pinned. Other Python versions are not the supported workflow. Obtain Python from https://www.python.org/downloads/windows/ . Setup downloads requirements into `.venv`; no global or other user's packages are changed. Never copy another computer's `.venv`.

## 2. Put the folder in Rocksmith

Extract so the final folder is:

```text
C:\Program Files (x86)\Steam\steamapps\common\Rocksmith2014\JonsRockSmithMods
```

The executable or app.py must be directly inside that JonsRockSmithMods folder, not inside a second identically named nested folder. For a different Steam drive/library, use your actual Rocksmith2014 directory. Windows may request permission to extract or create `.venv` under Program Files. If setup is denied, use the EXE download or approve the required folder-write permission.

## 3. Start the chosen program

- EXE: `Run EXE.bat` or `JonsRockSmithMods.exe`.
- Python: `Setup Python.bat` once, then `Run Python.bat`.
- `Launch JonsRockSmithMods.bat` offers both choices explicitly.

On Setup & startup, confirm running version **0.21.0**, mode and path. File Properties > Details also shows EXE version 0.21.0.0. Close an old companion before switching versions. Rocksmith can remain running for read-only review, queued removals and shared tuner input; profile/archive/native installation writes have their own game-closed guards.

## 4. Check file links before use

Browse and verify:

1. **Rocksmith 2014 folder:** contains Rocksmith2014.exe; songs install under its `dlc` folder.
2. **Do Not Want folder:** your reversible backup destination, outside active `dlc`. Do not point this at someone else's Windows user folder or nest it inside the active song library.
3. **Rocksmith profile:** your Steam account's exact `*_PRFLDB`, normally under `Steam/userdata/<your-account>/221680/remote`. Do not select another user's profile. Account IDs differ.
4. **Toolkit files:** install compatible Rocksmith Toolkit separately. The app searches Rocksmith2014/RocksmithToolkit, your current Desktop/RocksmithToolkit, or Program Files/Rocksmith 2014 DLC Builder. The chosen folder must contain RocksmithToolkitLib.dll and its dependencies. No Toolkit or game content is bundled. See the official project https://github.com/rscustom/rocksmith-custom-song-toolkit .

Run Full refresh. If paths are wrong, change them in the GUI and refresh again. No hardcoded personal Windows username needs editing. A missing profile/Toolkit is reported rather than guessed. Keep verified backups before changing difficulty or song packs.

## 5. In-game removal

Manual workflow needs no native add-on: select/check archives in the companion, Queue checked until game closes, review confirmation, then close Rocksmith and leave/reopen the companion. Removal is reversible.

In-game F10 requires the optional compatible Jons native extension, installed while Rocksmith is closed and loaded after restart. Its new overlay shows the configured key in menus and saved/error feedback. In Learn a Song, enable previews, wait for the desired preview and press F10. Return to the song list to mark a song; performance identity is not verified. A whole pack moves as a unit. After game exit review the exact archive and type QUEUE. These are marks for review, not immediate deletion.

The modified upstream native DLL is intentionally omitted from public downloads because no upstream redistribution license was found. Developer source/patch build instructions are in native/README.md in the repository. Do not download unknown replacement DLLs or overwrite an incompatible version. No public download silently installs a native module.

## 6. Common errors

- Missing `_internal`: extract the whole EXE ZIP.
- Missing Python 3.11 x64: install the supported interpreter or choose the EXE package.
- Source dependency error: rerun Setup Python.bat and inspect its visible error.
- Missing helpers: use the complete Python release ZIP, or build developer helpers.
- Missing audio banks: restore owned DLC/source backups; splitting cannot synthesize recordings.
- Guitar input unavailable: select the correct cable/backend; check Windows desktop microphone permission. An exclusive owner can block sharing even when the tuner requests shared access.
- Native feature unavailable: ordinary companion functions still work; optional module is separate.

Logs are created under your current user's Documents/JonsRockSmithMods. Do not upload those logs, profiles, songs or backup folders with the program. Before sharing an error log, review its paths/account identifiers.
