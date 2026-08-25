# Run the sidecar FROM SOURCE for fast iteration.
#
# Why this exists: testing a Python change by running scripts\release.ps1 costs ~15 min
# (PyInstaller bundle ~4, Tauri/Rust build ~7, install ~2, suites ~6) and quick.ps1 still
# costs ~5. Nothing about a Python edit needs any of that. This restarts the sidecar from
# source in ~40 s (almost all of it llama-server loading), so the edit->test loop is
# seconds instead of minutes. Build and install ONCE, at the end, when it already works.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\dev.ps1          # restart
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\dev.ps1 -Stop    # stop
#
# The port and token are fixed so test harnesses can hardcode them:
#   python tests\research_e2e.py 8790 devtoken123
#
# The installed app is closed first, deliberately. Two llama-servers do not fit on a
# 780M: the second one sits at 503 until it times out, every test then runs against
# nothing, and the run reports five bare shrugs that have nothing to do with the code.
# Failing loudly beats a whole test run that quietly measured a dead port.
param([switch]$Stop)

$ErrorActionPreference = "SilentlyContinue"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$port = 8790
$log  = "$env:TEMP\jarvis_dev_sidecar.log"

# Kill whatever is holding the dev port. taskkill, not pkill: these are Windows processes
# and a stale one silently wins the bind, so the next run tests the OLD code and lies.
$owners = (Get-NetTCPConnection -LocalPort $port -State Listen).OwningProcess
foreach ($p in $owners) { taskkill /PID $p /F 2>&1 | Out-Null }
Get-CimInstance Win32_Process -Filter "name='python.exe'" |
    Where-Object { $_.CommandLine -like "*main.py --port $port*" } |
    ForEach-Object { taskkill /PID $_.ProcessId /F 2>&1 | Out-Null }
Start-Sleep 2
if ($Stop) { "dev sidecar stopped"; exit 0 }

# Close the installed app (and its llama-server) so the dev one owns the GPU.
$installed = Get-Process jarvis -ErrorAction SilentlyContinue
if ($installed) {
    "closing the installed JARVIS so the dev sidecar can load the model"
    $installed | ForEach-Object { taskkill /PID $_.Id /T /F 2>&1 | Out-Null }
    Get-CimInstance Win32_Process -Filter "name='jarvis-sidecar.exe'" |
        ForEach-Object { taskkill /PID $_.ProcessId /T /F 2>&1 | Out-Null }
    Start-Sleep 4
}

Set-Location "$root\sidecar"
$env:JARVIS_DEBUG = "1"
$env:PYTHONIOENCODING = "utf-8"
Start-Process -FilePath ".\.venv\Scripts\python.exe" `
              -ArgumentList "main.py","--port","$port","--token","devtoken123" `
              -RedirectStandardOutput $log -RedirectStandardError "$log.err" `
              -WindowStyle Hidden

$deadline = (Get-Date).AddSeconds(240)
while ((Get-Date) -lt $deadline) {
    Start-Sleep 3
    try {
        $h = Invoke-RestMethod "http://127.0.0.1:$port/health" `
             -Headers @{'X-Jarvis-Token'='devtoken123'} -TimeoutSec 5
        if ($h.state -eq 'idle') { "dev sidecar ready on :$port (log: $log)"; exit 0 }
    } catch {}
}
"dev sidecar did not reach idle - see $log"
Get-Content $log -Tail 20
exit 1
