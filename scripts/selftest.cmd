@echo off
REM Nightly self-test: runs the e2e suites against the INSTALLED, RUNNING app and writes
REM %APPDATA%\JARVIS\selftest.json (shown in Diagnostics). Task JARVIS_SELFTEST, 03:30 daily.
setlocal
set PYTHONIOENCODING=utf-8
if not exist "%APPDATA%\JARVIS\logs" mkdir "%APPDATA%\JARVIS\logs"
"%~dp0..\sidecar\.venv\Scripts\python.exe" "%~dp0selftest.py" "%APPDATA%\JARVIS\selftest.json" > "%APPDATA%\JARVIS\logs\selftest.log" 2>&1
