@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0JonsRockSmithMods.exe" if exist "%~dp0_internal\python311.dll" goto flat
if exist "%~dp0release\JonsRockSmithMods\JonsRockSmithMods.exe" if exist "%~dp0release\JonsRockSmithMods\_internal\python311.dll" goto legacy
echo ERROR: Precompiled program is missing or incomplete. Extract the full Windows EXE ZIP, including _internal.
pause
exit /b 1
:flat
start "JonsRockSmithMods" /D "%~dp0" "%~dp0JonsRockSmithMods.exe"
if errorlevel 1 goto failed
exit /b 0
:legacy
start "JonsRockSmithMods" /D "%~dp0release\JonsRockSmithMods" "%~dp0release\JonsRockSmithMods\JonsRockSmithMods.exe"
if errorlevel 1 goto failed
exit /b 0
:failed
echo ERROR: Windows could not launch JonsRockSmithMods.exe. Check the full package and its file properties version.
pause
exit /b 1
