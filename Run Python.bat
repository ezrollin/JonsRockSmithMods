@echo off
setlocal
cd /d "%~dp0"
if not exist "%~dp0app.py" goto no_source
if exist "%~dp0.venv\Scripts\python.exe" goto venv
where py >nul 2>nul
if errorlevel 1 goto no_python
py -3.11 -c "import sys, struct; assert sys.version_info[:2] == (3,11) and struct.calcsize('P') == 8" >nul 2>nul
if errorlevel 1 goto no_python
py -3.11 app.py
if errorlevel 1 goto failed
exit /b 0
:venv
"%~dp0.venv\Scripts\python.exe" -c "import sys, struct; assert sys.version_info[:2] == (3,11) and struct.calcsize('P') == 8" >nul 2>nul
if errorlevel 1 goto no_python
"%~dp0.venv\Scripts\python.exe" app.py
if errorlevel 1 goto failed
exit /b 0
:no_source
echo ERROR: This download does not contain app.py. Download the Python source package.
pause
exit /b 1
:no_python
echo ERROR: Python 3.11 64-bit is required. Install it and run Setup Python.bat, or use the EXE download.
pause
exit /b 1
:failed
echo ERROR: Python application failed. Run Setup Python.bat and inspect Documents\JonsRockSmithMods\JonsRockSmithMods.log.
pause
exit /b 1
