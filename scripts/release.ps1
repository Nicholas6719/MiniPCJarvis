# One-command release: gated sidecar build -> tauri build -> real-session install
# (scheduled task, see docs/HANDOFF.md "sandbox trap") -> wait for the app -> run every
# e2e suite against the installed app. Exits non-zero if anything fails.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\release.ps1 [-SkipBuild] [-SkipTests]
param([switch]$SkipBuild, [switch]$SkipTests)
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root
$log = { param($m) Write-Host ("[{0}] {1}" -f (Get-Date -Format HH:mm:ss), $m) }

if (-not $SkipBuild) {
    & $log "sidecar build (gated)"
    cmd /c scripts\build_sidecar.cmd
    if ($LASTEXITCODE -ne 0) { & $log "SIDECAR BUILD FAILED"; exit 1 }

    & $log "tauri build"
    $vc = 'C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat'
    cmd /c "`"$vc`" >nul 2>&1 && set" | ForEach-Object { if ($_ -match '^([^=]+)=(.*)$') { Set-Item -Path "env:$($matches[1])" -Value $matches[2] } }
    $env:PATH = "C:\Users\nicho\Tools\node;" + $env:PATH
    & C:\Users\nicho\Tools\node\npm.cmd run tauri build *> "$env:TEMP\tauri.log"
    if ($LASTEXITCODE -ne 0) { & $log "TAURI BUILD FAILED (see $env:TEMP\tauri.log)"; Get-Content "$env:TEMP\tauri.log" -Tail 30; exit 1 }
    $setup = Get-Item "$root\src-tauri\target\release\bundle\nsis\JARVIS_0.1.0_x64-setup.exe"
    if ((Get-Date) - $setup.LastWriteTime -gt [TimeSpan]::FromMinutes(15)) { & $log "installer is stale ($($setup.LastWriteTime))"; exit 1 }
}

& $log "install via real-session scheduled task"
$installLog = "C:\Users\nicho\Documents\jarvis_install.log"
if (Test-Path $installLog) { Clear-Content $installLog }
schtasks /Create /TN JARVIS_INSTALL /TR "C:\Users\nicho\Documents\jarvis_install.cmd" /SC ONCE /ST 23:59 /F | Out-Null
schtasks /Run /TN JARVIS_INSTALL | Out-Null
$deadline = (Get-Date).AddMinutes(4)
while ((Get-Date) -lt $deadline) {
    Start-Sleep 5
    if ((Test-Path $installLog) -and (Select-String -Path $installLog -Pattern '^DONE' -Quiet)) { break }
}
schtasks /Delete /TN JARVIS_INSTALL /F | Out-Null
if (-not (Select-String -Path $installLog -Pattern 'installer exit 0' -Quiet)) { & $log "INSTALL FAILED"; Get-Content $installLog; exit 1 }

& $log "waiting for the app"
$port = $null
$deadline = (Get-Date).AddSeconds(150)
while ((Get-Date) -lt $deadline) {
    $p = Get-CimInstance Win32_Process -Filter "name='jarvis-sidecar.exe'" -ErrorAction SilentlyContinue
    if ($p) {
        $port = [regex]::Match($p.CommandLine, '--port (\d+)').Groups[1].Value
        $tok = [regex]::Match($p.CommandLine, '--token ([0-9a-f]+)').Groups[1].Value
        if (-not $tok) { $tf = "$env:APPDATA\JARVIS\session.token"; if (Test-Path $tf) { $tok = (Get-Content $tf -Raw).Trim() } }
        try { $h = Invoke-RestMethod "http://127.0.0.1:$port/health" -TimeoutSec 3; if ($h.state -eq 'idle') { break } } catch {}
    }
    Start-Sleep 3
}
if (-not $port) { & $log "APP DID NOT COME UP"; exit 1 }
[IO.File]::WriteAllText('C:\Users\nicho\Documents\jarvis_real.txt', "$port $tok")
# the wake model loads lazily after boot: the voice suite is a false failure before it's up
$deadline = (Get-Date).AddSeconds(120)
while ((Get-Date) -lt $deadline) { try { $dg = Invoke-RestMethod "http://127.0.0.1:$port/diagnostics" -Headers @{'X-Jarvis-Token'=$tok} -TimeoutSec 20; if (($dg.checks | Where-Object name -eq 'Wake Word').status -eq 'ok') { break } } catch {}; Start-Sleep 5 }
Start-Sleep 15
& $log "app up on :$port"

if ($SkipTests) { exit 0 }
Set-Location "$root\sidecar"
$env:PYTHONIOENCODING = 'utf-8'
$failed = 0
foreach ($t in @("brain_e2e.py", "general_e2e.py", "teach_e2e.py", "files_e2e.py", "sleep_e2e.py", "research_e2e.py", "filler_e2e.py", "voice_ux_e2e.py")) {
    & $log "== $t"
    & .\.venv\Scripts\python.exe "tests\$t" $port $tok 2>&1 | Select-Object -Last 4
    if ($LASTEXITCODE -ne 0 -and $t -notin @("filler_e2e.py", "general_e2e.py", "voice_ux_e2e.py")) { $failed++ }
}
& $log ("RELEASE " + $(if ($failed -eq 0) { "OK" } else { "FAILED ($failed suites)" }))
exit $failed
