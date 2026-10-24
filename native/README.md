# Optional local native extension

This patch targets upstream RSMods commit `1e4a67d43dfd0bae2b613edc4cd67b922332dc61` from https://github.com/Lovrom8/RSMods . It preserves the existing proxy architecture and compatibility guards. The upstream pinned tree and repository metadata did not identify a project-wide redistribution license on 2026-10-24. Public downloads omit the compiled modified DLL; this project's GPL does not grant rights to all upstream code. Upstream game and Toolkit assets are never bundled. Public EXE builds exclude this DLL at build time, not only during ZIP staging.

## Visible in-game song marks

The local extension adds a default-on hint while a fresh song preview is active and ten-second saved/error feedback. In Learn a Song, wait for the desired preview, then press F10. Its exact SongKey is saved as a durable request. The extension checks the preview-eligible menu state, a fresh preview identity, and unchanged menu before publication. It never changes archives inside the game.

After Rocksmith closes, the companion automatically queues a verified single-song archive for a reversible move to Do Not Want. Keep/reopen the companion to process it; Undo last completed removal restores it. Missing/ambiguous metadata never guesses an archive. Multi-song archives are refused; split the pack first. F10 is not a permanent delete.

## Note detection and first tuner

The companion stores `JonsPitchReference = a440` or `rocksmith` under `[Mod Settings]`, and `JonsSkipFirstPreSongTuner = on/off` under `[Toggle Switches]`. Defaults are A440 and one skip per game launch. A440 changes the cents reference used for note detection while retaining chart semitone notes (including Drop D); it does not pitch-shift backing audio. The optional skip sends the game's Delete/skip key only at the first song-specific pre-song tuner in the process. A fresh game process resets the one-time guard.

The A440 hook reuses RSMods' `TrueTuning` hook and runs only when the executable checksum matches one of the two known RSMods versions. Unknown checksums leave chart-authored pitch enabled and log `A440_REFERENCE_UNAVAILABLE`; no legacy offset is guessed. A successful source build does not prove live note recognition or the game's skip response. Verify the UI setting and one song manually after restarting Rocksmith.

Settings in JonsRockSmithMods.ini beside Rocksmith2014.exe:

```ini
[Keybinds]
SongRemovalRequestKey = VK_F10

[Toggle Switches]
ShowSongRemovalHint = on
JonsFixedSongSpeedEnabled = off

[Mod Settings]
JonsFixedSongSpeedPercent = 85

[Song Speed Overrides]
ExampleSongKey = 85
```

ShowSongRemovalHint=off hides routine hints; action feedback remains. Per-song speed requires independently verified game identity and the compatible extension. Startup auto-profile uses upstream RSMods ForceProfileLoad/ProfileToLoad; Steam login remains external. No unverified live difficulty setter is supplied.

## Developer provenance and local builds

The source patch and pinned build script document local development. A local build requires Git, Visual Studio 2022 Community C++ v143 and a Windows SDK, the exact upstream revision and its dependencies. build-native.ps1 creates an isolated ignored checkout, disables PostBuildEventUseInBuild, and verifies the output. Do not run upstream copy-to-game build events. Install only a verified compatible local build with Rocksmith closed, using backups and compatibility guards; restart the game afterward. Keep any private native builds/backups outside public releases. A successful build does not establish live overlay acceptance or upstream redistribution permission.
