@echo off
REM Sidecar-only update: stop JARVIS and everything it spawned, mirror the fresh PyInstaller
REM output over %LOCALAPPDATA%\JARVIS\sidecar, relaunch. Runs in the REAL user session.
REM Refuses to launch on a partial copy (robocopy exit >= 8) - a mixed build is worse than none.
set LOG=C:\Users\nicho\Documents\jarvis_hotswap.log
echo [%DATE% %TIME%] hotswap start > "%LOG%"
taskkill /F /IM jarvis.exe >> "%LOG%" 2>&1
taskkill /F /IM jarvis-sidecar.exe >> "%LOG%" 2>&1
REM children that keep DLLs in the sidecar folder open: JARVIS's hidden Brave profiles and
REM the llama-servers JARVIS itself started (never Houston's - matched by our log path)
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { ($_.Name -eq 'brave.exe' -and $_.CommandLine -match 'JARVIS\\(browser-profile|session-browser)') -or ($_.Name -eq 'llama-server.exe' -and $_.CommandLine -match 'JARVIS\\logs') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; Write-Output ('stopped child ' + $_.Name + ' ' + $_.ProcessId) }" >> "%LOG%" 2>&1
timeout /t 3 /nobreak > nul
robocopy "C:\Users\nicho\Documents\Coding_Projects\JARVIS\sidecar\dist\jarvis-sidecar" "C:\Users\nicho\AppData\Local\JARVIS\sidecar" /MIR /R:5 /W:2 /NFL /NDL /NJH /NP >> "%LOG%" 2>&1
set RC=%ERRORLEVEL%
echo [%DATE% %TIME%] robocopy exit %RC% >> "%LOG%"
if %RC% GEQ 8 (
  echo HOTSWAP FAILED - not launching a mixed build >> "%LOG%"
  echo DONE >> "%LOG%"
  exit /b 1
)
set JARVIS_DEBUG=1
start "" "C:\Users\nicho\AppData\Local\JARVIS\jarvis.exe"
echo [%DATE% %TIME%] launched >> "%LOG%"
echo DONE >> "%LOG%"
