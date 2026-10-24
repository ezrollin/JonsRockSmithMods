@echo off
setlocal
cd /d "%~dp0"
if not exist "%~dp0requirements.txt" goto no_source
where py >nul 2>nul
if errorlevel 1 goto no_python
py -3.11 -c "import sys, struct; assert sys.version_info[:2] == (3,11) and struct.calcsize('P') == 8" >nul 2>nul
if errorlevel 1 goto no_python
if exist "%~dp0.venv\Scripts\python.exe" goto install
py -3.11 -m venv "%~dp0.venv"
if errorlevel 1 goto failed
:install
"%~dp0.venv\Scripts\python.exe" -c "import sys, struct; assert sys.version_info[:2] == (3,11) and struct.calcsize('P') == 8" >nul 2>nul
if errorlevel 1 goto no_python
"%~dp0.venv\Scripts\python.exe" -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 goto failed
echo Setup complete. Open Run Python.bat. Verify your file links on Setup ^& startup.
pause
exit /b 0
:no_source
echo ERROR: Download and extract the complete Python source package first.
pause
exit /b 1
:no_python
echo ERROR: Install Python 3.11 64-bit with the Windows Python launcher, then try again.
echo The precompiled EXE package does not require Python installation.
pause
exit /b 1
:failed
echo ERROR: Setup did not complete. Check the error above, internet access, and write permission to this folder.
echo Program Files may require approval to create .venv. Use the EXE download if needed.
pause
exit /b 1
