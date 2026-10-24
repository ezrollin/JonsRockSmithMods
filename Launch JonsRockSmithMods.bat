@echo off
setlocal
cd /d "%~dp0"
echo JonsRockSmithMods - choose how to run
echo E = precompiled EXE, no Python installation required
echo P = Python source, requires Python 3.11 64-bit and Setup Python.bat
choice /C EP /N /M "Run [E]XE or [P]ython? "
if errorlevel 2 goto python
call "%~dp0Run EXE.bat"
exit /b %errorlevel%
:python
call "%~dp0Run Python.bat"
exit /b %errorlevel%
