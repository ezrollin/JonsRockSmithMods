# Whole song pack splitter helper

Build against the selected Rocksmith Toolkit folder with:

```powershell
& .\split_helper\build.ps1 -ToolkitDirectory 'C:\path\to\RocksmithToolkit\GUI\Lib'
```

The build produces x86 and x64 executables. The app selects the helper that
matches `RocksmithToolkitLib.dll`'s PE architecture. The generic
`SplitPackHelper.exe` remains as a compatibility copy of the x64 build.
