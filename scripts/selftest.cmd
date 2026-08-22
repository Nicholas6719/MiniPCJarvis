@echo off
REM Nightly self-test: run the e2e suites against the INSTALLED, RUNNING app and write a
REM report the Diagnostics view shows. Registered as scheduled task JARVIS_SELFTEST
REM (03:30 daily) by scripts\install_selftest.cmd. Safe to run by hand.
setlocal
set ROOT=%~dp0..
set OUT=%APPDATA%\JARVIS\selftest.json
set LOG=%APPDATA%\JARVIS\logs\selftest.log
set PYTHONIOENCODING=utf-8
for /f "usebackq tokens=*" %%i in (`powershell -NoProfile -Command "$p=Get-CimInstance Win32_Process -Filter \"name='jarvis-sidecar.exe'\"; if($p){[regex]::Match($p.CommandLine,'--port (\d+)').Groups[1].Value + ' ' + [regex]::Match($p.CommandLine,'--token ([0-9a-f]+)').Groups[1].Value}"`) do set PT=%%i
if "%PT%"=="" (
  echo {"ts": %DATE:~-4%, "ok": false, "error": "JARVIS is not running"} > "%OUT%"
  exit /b 1
)
for /f "tokens=1,2" %%a in ("%PT%") do (set PORT=%%a& set TOK=%%b)
echo [%DATE% %TIME%] selftest start port %PORT% > "%LOG%"
"%ROOT%\sidecar\.venv\Scripts\python.exe" "%ROOT%\scripts\selftest.py" %PORT% %TOK% "%OUT%" >> "%LOG%" 2>&1
echo [%DATE% %TIME%] selftest done >> "%LOG%"
