@echo off
REM Sidecar-only update: replace %LOCALAPPDATA%\JARVIS\sidecar with the fresh PyInstaller
REM output and relaunch the app. Runs in the REAL user session (scheduled task).
set LOG=C:\Users\nicho\Documents\jarvis_hotswap.log
echo [%DATE% %TIME%] hotswap start > "%LOG%"
taskkill /F /IM jarvis.exe >> "%LOG%" 2>&1
taskkill /F /IM jarvis-sidecar.exe >> "%LOG%" 2>&1
timeout /t 2 /nobreak > nul
robocopy "C:\Users\nicho\Documents\Coding_Projects\JARVIS\sidecar\dist\jarvis-sidecar" "C:\Users\nicho\AppData\Local\JARVIS\sidecar" /MIR /R:3 /W:2 /NFL /NDL /NJH /NP >> "%LOG%" 2>&1
echo [%DATE% %TIME%] robocopy exit %ERRORLEVEL% >> "%LOG%"
set JARVIS_DEBUG=1
start "" "C:\Users\nicho\AppData\Local\JARVIS\jarvis.exe"
echo [%DATE% %TIME%] launched >> "%LOG%"
echo DONE >> "%LOG%"
