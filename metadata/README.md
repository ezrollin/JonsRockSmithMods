# PSARC metadata reader

Build with `powershell -File build.ps1`. The script builds x86 and x64 helpers against the selected `RocksmithToolkitLib.dll`; Python selects the matching PE architecture. Its Python adapters are `metadata.py` (`extract_metadata(path)`) for title metadata and `extract_difficulty_metadata(path)` for on-demand SNG phrase maxima. Difficulty metadata bypasses the title cache; callers must reject any non-empty `warnings` before writing profile difficulty.

In title mode, the helper opens the selected `.psarc` read-only, reads its table of contents lazily, and inflates only `manifests/*.json` entries into memory. It returns one archive object with distinct songs, each carrying artist, title, song key, DLC key, arrangement IDs, and source manifest paths. A package may contain several songs. Unknown or malformed metadata stays unknown; archive filenames are never treated as proof of artist/title or play history.

Example from the app folder:

```powershell
& .\metadata\build.ps1
$toolkit = Join-Path $env:USERPROFILE 'Desktop\RocksmithToolkit'
& .\metadata\bin\MetadataExtractor-x86.exe $toolkit 'C:\path\song_p.psarc'
```

The helper does not read or change play counts. Those live in Steam profile data and need separate decoding and explicit arrangement-ID matching. Metadata reads do not modify Rocksmith files.

Use MetadataExtractor-x64.exe for a PE32+ Toolkit library. The Python adapter selects the matching helper automatically. Difficulty mode also reads the arrangement SNG phrase section.
