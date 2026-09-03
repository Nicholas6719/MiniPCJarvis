# One-command release: gated sidecar build -> tauri build -> real-session install
# (scheduled task, see docs/HANDOFF.md "sandbox trap") -> wait for the app -> run every
# e2e suite against the installed app. Exits non-zero if anything fails.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\release.ps1 [-SkipBuild] [-SkipTests] [-Silent]
#
# -Silent is for building while he is working and does not want to be
# interrupted: "I don't want to hear anything, I don't want any Telegram
# messages and I don't want to see anything". It mutes the speaker for the whole
# run through /debug/silence — turns still run end to end, they just make no
# sound — and leaves JARVIS_TELEGRAM_E2E unset, which makes telegram_e2e skip
# itself rather than message his phone. Everything else is a normal release, so
# a silent run is still a real gate and not a weaker one.
param([switch]$SkipBuild, [switch]$SkipTests, [switch]$Silent)
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
$installLog = "C:\Users\nicho\Documents\Coding_Projects\JARVIS\.agent\logs\install.log"
if (Test-Path $installLog) { Clear-Content $installLog }
schtasks /Create /TN JARVIS_INSTALL /TR "C:\Users\nicho\Documents\Coding_Projects\JARVIS\.agent\scripts\jarvis_install.cmd" /SC ONCE /ST 23:59 /F | Out-Null
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

# /health says "idle" the moment FastAPI binds its socket, which is a long way
# short of being able to answer. On 2026-08-30 the suites started 34s after that
# and three of them failed - brain_e2e at 2/8 - purely because the app was not
# warm yet; every one passed on the same build minutes later. A release that
# cries wolf is worse than no release check at all, so wait for the subsystems
# and then make it actually answer something before judging it.
if ($port -and $Silent) {
    # BEFORE the warm-up turn, which is the first thing that would speak. An
    # hour covers the warm-up and every suite; the flag is a deadline on the
    # speaker rather than a mode, so it cannot be left switched on by accident.
    try {
        Invoke-RestMethod "http://127.0.0.1:$port/debug/silence" -Method Post `
            -Headers @{'X-Jarvis-Token'=$tok} -ContentType 'application/json' `
            -Body (@{ seconds = 3600 } | ConvertTo-Json) -TimeoutSec 15 | Out-Null
        & $log "silent run: speaker muted for an hour, telegram left unpaired to the suite"
    } catch { & $log "COULD NOT MUTE - stopping rather than making noise he asked me not to make: $($_.Exception.Message)"; exit 1 }
}

if ($port) {
    & $log "waiting for the subsystems"
    $deadline = (Get-Date).AddSeconds(240)
    while ((Get-Date) -lt $deadline) {
        try {
            $dg = Invoke-RestMethod "http://127.0.0.1:$port/diagnostics" -Headers @{'X-Jarvis-Token'=$tok} -TimeoutSec 20
            if (($dg.checks | Where-Object name -eq 'Wake Word').status -eq 'ok') { break }
        } catch {}
        Start-Sleep 5
    }
    & $log "warming up (one real turn)"
    try {
        Invoke-RestMethod "http://127.0.0.1:$port/text" -Method Post -Headers @{'X-Jarvis-Token'=$tok} `
            -ContentType 'application/json' -Body (@{ text = "what time is it" } | ConvertTo-Json) -TimeoutSec 120 | Out-Null
    } catch { & $log "warm-up turn did not complete: $($_.Exception.Message)" }
    Start-Sleep 5
}
if (-not $port) { & $log "APP DID NOT COME UP"; exit 1 }
[IO.File]::WriteAllText('C:\Users\nicho\Documents\Coding_Projects\JARVIS\.agent\session.txt', "$port $tok")
# the wake model loads lazily after boot: the voice suite is a false failure before it's up
$deadline = (Get-Date).AddSeconds(120)
while ((Get-Date) -lt $deadline) { try { $dg = Invoke-RestMethod "http://127.0.0.1:$port/diagnostics" -Headers @{'X-Jarvis-Token'=$tok} -TimeoutSec 20; if (($dg.checks | Where-Object name -eq 'Wake Word').status -eq 'ok') { break } } catch {}; Start-Sleep 5 }
Start-Sleep 15
& $log "app up on :$port"

if ($SkipTests) { exit 0 }
Set-Location "$root\sidecar"
$env:PYTHONIOENCODING = 'utf-8'
# ONE list of suites, not two. This kept its own shorter list and had quietly
# drifted seven suites behind scripts\suites.ps1 - missing soak_e2e, which is
# what caught the audio crash on 2026-08-31, and market_e2e, which caught the
# Finnhub retry regressions. A release gate weaker than the ad-hoc check is worse
# than no release gate, because it is the one you trust.
#
# Telegram runs HERE and not in the ad-hoc suite: it sends real messages to his
# phone, which is right for a release and wrong every ten minutes.
[IO.File]::WriteAllText("$root\.agent\session.txt", "$port $tok")
if (-not $Silent) { $env:JARVIS_TELEGRAM_E2E = "1" }
try {
    & "$root\scripts\suites.ps1"
    $failed = $LASTEXITCODE
} finally {
    Remove-Item Env:\JARVIS_TELEGRAM_E2E -ErrorAction SilentlyContinue
}
& $log ("RELEASE " + $(if ($failed -eq 0) { "OK" } else { "FAILED ($failed suites)" }))
exit $failed
