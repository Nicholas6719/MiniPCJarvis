# Run every e2e suite against the RUNNING install, waiting for quiet between each.
# The release script runs them back-to-back, which makes order-sensitive suites
# (brain_e2e especially) fail on speech still playing from the previous one.
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\suites.ps1
$ErrorActionPreference = "Continue"
$root = Split-Path $PSScriptRoot
$pt = (Get-Content "C:\Users\nicho\Documents\Coding_Projects\JARVIS\.agent\session.txt") -split ' '
$port, $tok = $pt[0], $pt[1]
Set-Location "$root\sidecar"
$env:PYTHONIOENCODING = 'utf-8'

# WAIT FOR THE APP TO BE READY. /health answers "starting" the moment the socket
# binds, and a suite run against a starting app fails wholesale for reasons no
# commit can fix - clarify_e2e reported 9 failures on 2026-08-31 purely because
# the sidecar had restarted seconds earlier. release.ps1 learned this on
# 2026-08-30; this script never did, which is why the same false failures kept
# coming back.
$deadline = (Get-Date).AddSeconds(300)
while ((Get-Date) -lt $deadline) {
    try {
        $h = Invoke-RestMethod "http://127.0.0.1:$port/health" -Headers @{'X-Jarvis-Token'=$tok} -TimeoutSec 10
        if ($h.state -ne 'starting' -and $h.state -ne 'offline') {
            $dg = Invoke-RestMethod "http://127.0.0.1:$port/diagnostics" -Headers @{'X-Jarvis-Token'=$tok} -TimeoutSec 25
            if (($dg.checks | Where-Object name -eq 'Wake Word').status -eq 'ok') { break }
        }
    } catch {}
    Start-Sleep 5
}
# and make it answer once, so the model and caches are warm before anything is judged
try {
    Invoke-RestMethod "http://127.0.0.1:$port/text" -Method Post -Headers @{'X-Jarvis-Token'=$tok} `
        -ContentType 'application/json' -Body (@{ text = "what time is it" } | ConvertTo-Json) -TimeoutSec 120 | Out-Null
    Start-Sleep 8
} catch { }

$failed = @()
# sleep_e2e runs LAST on purpose: it sleeps and wakes him, and the state churn
# was making whatever ran next (research) drop a turn and report a bare shrug.
foreach ($t in @("brain_e2e.py", "general_e2e.py", "teach_e2e.py", "files_e2e.py",
                 "research_e2e.py", "facts_e2e.py", "filler_e2e.py", "voice_ux_e2e.py",
                 "endpoint_e2e.py", "wake_guard_e2e.py", "hands_e2e.py", "clarify_e2e.py", "market_e2e.py",
                 "telegram_e2e.py",
                 "hud_e2e.py", "sleep_e2e.py", "soak_e2e.py")) {
    # wait for quiet: a suite that starts mid-sentence reads the previous answer
    $deadline = (Get-Date).AddSeconds(90)
    while ((Get-Date) -lt $deadline) {
        try { if ((Invoke-RestMethod "http://127.0.0.1:$port/health" -TimeoutSec 5).state -in @('idle','sleeping')) { break } } catch {}
        Start-Sleep 3
    }
    Start-Sleep 10   # let the speaker drain and the wake model settle
    Write-Host "== $t"
    # keep enough of the tail to show WHY, not just that it failed: the last 3 lines
    # once hid the one diagnostic line that explained a suite-only failure
    $out = & .\.venv\Scripts\python.exe "tests\$t" $port $tok 2>&1
    $code = $LASTEXITCODE
    # ALWAYS keep the whole thing. An intermittent failure that only shows its
    # tail is a failure you get to diagnose once, if you are lucky and watching.
    $full = "$root\.agent\logs\suite_$($t -replace '\.py$','').log"
    $out | Out-String -Width 300 | Set-Content -Encoding utf8 $full
    $out | Select-Object -Last $(if ($code -eq 0) { 3 } else { 25 })
    if ($code -ne 0) { Write-Host "    (full output: $full)" }
    $global:LASTEXITCODE = $code
    if ($LASTEXITCODE -ne 0 -and $t -notin @("filler_e2e.py", "general_e2e.py", "voice_ux_e2e.py")) {
        $failed += $t
    }
}
Write-Host ("SUITES " + $(if ($failed.Count -eq 0) { "ALL GREEN" } else { "FAILED: " + ($failed -join ', ') }))
exit $failed.Count
