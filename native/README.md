# Optional local native extension

This patch targets upstream RSMods commit `1e4a67d43dfd0bae2b613edc4cd67b922332dc61` from https://github.com/Lovrom8/RSMods . It preserves the existing proxy architecture and compatibility guards. The upstream pinned tree and repository metadata did not identify a project-wide redistribution license on 2026-10-24. Public downloads omit the compiled modified DLL; this project's GPL does not grant rights to all upstream code. Upstream game and Toolkit assets are never bundled. Public EXE builds exclude this DLL at build time, not only during ZIP staging.

## Visible in-game song marks

The local extension adds a default-on menu hint and ten-second saved/error feedback, independent of the accuracy overlay. In Learn a Song, enable previews, wait for the desired preview, then press F10. Its exact SongKey is saved as a durable request. The extension checks a fresh preview identity and unchanged menu before publication. During performance it instructs the user to return to the song list; a current-performance SongKey has not been verified. It never changes archives inside the game.

After Rocksmith closes, review the exact archive and every pack track in the companion; typed QUEUE confirmation persists a reversible whole-archive move. Keep/reopen the companion to process it. Missing/ambiguous metadata never guesses an archive. Multi-song removal requires whole-pack scope or prior complete splitting.

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
