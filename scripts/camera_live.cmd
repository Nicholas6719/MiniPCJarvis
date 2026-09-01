@echo off
REM Live camera/routing check against the RUNNING app. Must run in HIS session:
REM %APPDATA% is virtualized in an agent shell, and session.token lives there.
setlocal
set HERE=%~dp0
set PYTHONIOENCODING=utf-8
set OUT=%HERE%..\.agent\logs\camera_live.log
echo [%DATE% %TIME%] camera live check > "%OUT%"
"%HERE%..\sidecar\.venv\Scripts\python.exe" "%HERE%..\sidecar\tests\camera_live.py" >> "%OUT%" 2>&1
echo DONE >> "%OUT%"
type "%OUT%"
