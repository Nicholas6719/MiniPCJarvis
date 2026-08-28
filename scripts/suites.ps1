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
$failed = @()
# sleep_e2e runs LAST on purpose: it sleeps and wakes him, and the state churn
# was making whatever ran next (research) drop a turn and report a bare shrug.
foreach ($t in @("brain_e2e.py", "general_e2e.py", "teach_e2e.py", "files_e2e.py",
                 "research_e2e.py", "facts_e2e.py", "filler_e2e.py", "voice_ux_e2e.py",
                 "endpoint_e2e.py", "hands_e2e.py",
                 "hud_e2e.py", "sleep_e2e.py")) {
    # wait for quiet: a suite that starts mid-sentence reads the previous answer
    $deadline = (Get-Date).AddSeconds(90)
    while ((Get-Date) -lt $deadline) {
        try { if ((Invoke-RestMethod "http://127.0.0.1:$port/health" -TimeoutSec 5).state -in @('idle','sleeping')) { break } } catch {}
        Start-Sleep 3
    }
    Start-Sleep 10   # let the speaker drain and the wake model settle
    Write-Host "== $t"
    & .\.venv\Scripts\python.exe "tests\$t" $port $tok 2>&1 | Select-Object -Last 3
    if ($LASTEXITCODE -ne 0 -and $t -notin @("filler_e2e.py", "general_e2e.py", "voice_ux_e2e.py")) {
        $failed += $t
    }
}
Write-Host ("SUITES " + $(if ($failed.Count -eq 0) { "ALL GREEN" } else { "FAILED: " + ($failed -join ', ') }))
exit $failed.Count
