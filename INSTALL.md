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

On Setup & startup, confirm running version **0.25.0**, mode and path. File Properties > Details also shows EXE version 0.25.0.0. Close an old companion before switching versions. Rocksmith can remain running for read-only review, queued removals and shared tuner input; profile/archive/native installation writes have their own game-closed guards.

## 4. Check file links before use

Browse and verify:

1. **Rocksmith 2014 folder:** contains Rocksmith2014.exe; songs install under its `dlc` folder.
2. **Do Not Want folder:** defaults to `Rocksmith2014\DoNotWantSongs`, outside active `dlc`, and follows the selected Rocksmith folder while still at its default. Browse to choose another reversible backup destination. Do not point this at someone else's Windows user folder or nest it inside the active song library.
3. **Rocksmith profile:** your Steam account's exact `*_PRFLDB`, normally under `Steam/userdata/<your-account>/221680/remote`. Do not select another user's profile. Account IDs differ.
4. **Toolkit files:** install compatible Rocksmith Toolkit separately. The app searches Rocksmith2014/RocksmithToolkit, your current Desktop/RocksmithToolkit, or Program Files/Rocksmith 2014 DLC Builder. The chosen folder must contain RocksmithToolkitLib.dll and its dependencies. No Toolkit or game content is bundled. See the official project https://github.com/rscustom/rocksmith-custom-song-toolkit .

Run Full refresh. If paths are wrong, change them in the GUI and refresh again. No hardcoded personal Windows username needs editing. A missing profile/Toolkit is reported rather than guessed. Keep verified backups before changing difficulty or song packs.

## 5. In-game removal

Manual workflow needs no native add-on: select/check archives in the companion, read the warning and typed confirmation, then choose Queue checked until game closes. Whole archives move reversibly from active DLC into Do Not Want after Rocksmith exits; they are not permanently deleted. Use **Undo queued removals** before processing, or **Undo last completed removal** afterward (the last batch remains available after app restart). Undo verifies paths and file identities and requires Rocksmith closed. Existing Restore selected archives still restores any selected Do Not Want files.

To remove a track from a multi-song pack, use **Split up whole song pack** and replace the complete pack with verified single-song files. Then check only individual song archives you want moved to Do Not Want and queue them. The complete pack must be split so unselected tracks are not lost.

In-game F10 requires the optional compatible Jons native extension, installed while Rocksmith is closed and loaded after restart. Its hint appears while a fresh song preview is selected; press F10 to mark that preview. Keep the companion open or reopen it after Rocksmith exits. It automatically moves a verified single-song archive to Do Not Want and saves an undo record. It refuses a multi-song pack so one track mark cannot move unrelated tracks; split the pack into individual archives first, then retry the saved F10 mark. No archive moves while Rocksmith is running. F10 does not identify an arrangement already playing.

## First Song Tuning Preferences

In **Tune guitar**, choose whether you want the tuner once each time Rocksmith starts and which pitch reference it should use. **Use JonsRockSmithMods standard tuning (A=440)** corrects chart-authored cents offsets while preserving the song's semitone tuning, such as Drop D. **Use Rocksmith's original per-song pitch reference** keeps the original authored target. A=440 changes note detection only; it does not pitch-shift backing audio. These settings are saved in `JonsRockSmithMods.ini`, with a verified timestamped backup. Close Rocksmith before saving, then restart it. Rocksmith shows no tuner on later songs during the same game session; restart the game for the next first-song tuner.

A440 changes the note target only. It does not pitch-shift backing audio. A chart with an intentionally detuned recording can therefore sound out of tune when its detection target is normalized. The A440 hook runs only on the two supported Rocksmith executable checksums in the native source; an unrecognized build leaves the authored reference active and logs the reason. The public download excludes the native DLL, so the settings require a compatible locally built Jons extension.

The modified upstream native DLL is intentionally omitted from public downloads because no upstream redistribution license was found. Developer source/patch build instructions are in native/README.md in the repository. Do not download unknown replacement DLLs or overwrite an incompatible version. No public download silently installs a native module.

In the tuner, the nominal reference table updates whenever you change tuning. It lists all six strings' open notes/frequencies and 12th-fret frequencies (twice the nominal open pitch). The selected table row follows the string selector. Choose OPEN or FRET 12 directly; fret 12 needs no open-string reading first and always uses the nominal chart target. The live meter's cents value comes from the same measured and target Hz values shown beside it. Closing the tuner waits for its audio-capture thread to release the cable before the window closes.

## 6. Common errors

- Missing `_internal`: extract the whole EXE ZIP.
- Missing Python 3.11 x64: install the supported interpreter or choose the EXE package.
- Source dependency error: rerun Setup Python.bat and inspect its visible error.
- Missing helpers: use the complete Python release ZIP, or build developer helpers.
- Missing audio banks: restore owned DLC/source backups; splitting cannot synthesize recordings.
- Guitar input unavailable: select the correct cable/backend; check Windows desktop microphone permission. An exclusive owner can block sharing even when the tuner requests shared access.
- Native feature unavailable: ordinary companion functions still work; optional module is separate.

Logs are created under your current user's Documents/JonsRockSmithMods. Do not upload those logs, profiles, songs or backup folders with the program. Before sharing an error log, review its paths/account identifiers.
