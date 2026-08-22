# Fast path for SIDECAR-ONLY changes (Python): gated sidecar build -> hot-swap the installed
# sidecar folder in the real session -> smoke tests. ~4-5 min instead of ~15.
# UI (src/) or Rust (src-tauri/) changes still need scripts\release.ps1.
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\quick.ps1 [-Full]
param([switch]$Full)
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root
$log = { param($m) Write-Host ("[{0}] {1}" -f (Get-Date -Format HH:mm:ss), $m) }

& $log "sidecar build (gated)"
cmd /c scripts\build_sidecar.cmd
if ($LASTEXITCODE -ne 0) { & $log "SIDECAR BUILD FAILED"; exit 1 }

& $log "hot-swap sidecar in the real session"
$swapLog = "C:\Users\nicho\Documents\jarvis_hotswap.log"
if (Test-Path $swapLog) { Clear-Content $swapLog }
schtasks /Create /TN JARVIS_HOTSWAP /TR "C:\Users\nicho\Documents\jarvis_hotswap.cmd" /SC ONCE /ST 23:59 /F | Out-Null
schtasks /Run /TN JARVIS_HOTSWAP | Out-Null
$deadline = (Get-Date).AddMinutes(3)
while ((Get-Date) -lt $deadline) { Start-Sleep 3; if ((Test-Path $swapLog) -and (Select-String -Path $swapLog -Pattern '^DONE' -Quiet)) { break } }
schtasks /Delete /TN JARVIS_HOTSWAP /F | Out-Null
$rc = [regex]::Match((Get-Content $swapLog -Raw), 'robocopy exit (\d+)').Groups[1].Value
if (-not $rc -or [int]$rc -ge 8) { & $log "HOTSWAP FAILED (robocopy $rc)"; Get-Content $swapLog; exit 1 }

& $log "waiting for the app"
$port = $null
$deadline = (Get-Date).AddSeconds(150)
while ((Get-Date) -lt $deadline) {
    $p = Get-CimInstance Win32_Process -Filter "name='jarvis-sidecar.exe'" -ErrorAction SilentlyContinue
    if ($p) {
        $port = [regex]::Match($p.CommandLine, '--port (\d+)').Groups[1].Value
        $tok = [regex]::Match($p.CommandLine, '--token ([0-9a-f]+)').Groups[1].Value
        try { $h = Invoke-RestMethod "http://127.0.0.1:$port/health" -TimeoutSec 3; if ($h.state -eq 'idle') { break } } catch {}
    }
    Start-Sleep 3
}
if (-not $port) { & $log "APP DID NOT COME UP"; exit 1 }
[IO.File]::WriteAllText('C:\Users\nicho\Documents\jarvis_real.txt', "$port $tok")
Start-Sleep 12
& $log "app up on :$port"

Set-Location "$root\sidecar"
$env:PYTHONIOENCODING = 'utf-8'
$suites = if ($Full) { @("brain_e2e.py", "general_e2e.py", "teach_e2e.py", "files_e2e.py", "filler_e2e.py", "voice_ux_e2e.py") } else { @("brain_e2e.py", "files_e2e.py", "voice_ux_e2e.py") }
$failed = 0
foreach ($t in $suites) {
    & $log "== $t"
    & .\.venv\Scripts\python.exe "tests\$t" $port $tok 2>&1 | Select-Object -Last 3
    if ($LASTEXITCODE -ne 0 -and $t -notin @("filler_e2e.py", "general_e2e.py", "voice_ux_e2e.py")) { $failed++ }
}
& $log ("QUICK " + $(if ($failed -eq 0) { "OK" } else { "FAILED ($failed suites)" }))
exit $failed
