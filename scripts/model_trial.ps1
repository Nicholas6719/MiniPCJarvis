# Switch the RUNNING installed app to another configured model (live, no reinstall) and
# run every e2e suite against it.  powershell -File scripts\model_trial.ps1 -Model gemma-4-26b-a4b
param([string]$Model)
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$p = Get-CimInstance Win32_Process -Filter "name='jarvis-sidecar.exe'"
$port = [regex]::Match($p.CommandLine, '--port (\d+)').Groups[1].Value
# The token is passed to the sidecar on STDIN (--token-stdin), not on the command
# line, so reading it from the command line silently produced an empty string and
# every call in this script came back "bad token". Same fallback quick.ps1 uses.
$tok = [regex]::Match($p.CommandLine, '--token ([0-9a-f]+)').Groups[1].Value
if (-not $tok) {
    $tf = "$env:APPDATA\JARVIS\session.token"
    if (Test-Path $tf) { $tok = (Get-Content $tf -Raw).Trim() }
}
if (-not $tok) {
    $sf = "$root\.agent\session.txt"
    if (Test-Path $sf) { $tok = ((Get-Content $sf -Raw).Trim() -split '\s+')[1] }
}
if (-not $tok) { Write-Host "could not find the session token - is JARVIS running?"; exit 1 }
$H = @{ 'X-Jarvis-Token' = $tok }
Write-Host "[$(Get-Date -Format HH:mm:ss)] switching :$port to $Model"
$r = Invoke-RestMethod "http://127.0.0.1:$port/config" -Method Patch -Headers $H -ContentType 'application/json' -Body (@{ llm = @{ active_model = $Model } } | ConvertTo-Json) -TimeoutSec 400
Write-Host ("applied: " + ($r.applied -join ", "))
$deadline = (Get-Date).AddSeconds(240)
while ((Get-Date) -lt $deadline) { try { $h = Invoke-RestMethod "http://127.0.0.1:$port/health" -TimeoutSec 3; if ($h.state -eq 'idle') { break } } catch {}; Start-Sleep 3 }
$d = Invoke-RestMethod "http://127.0.0.1:$port/diagnostics" -Headers $H
Write-Host ("AI Engine: " + ($d.checks | Where-Object name -eq 'AI Engine').detail)
Set-Location "$root\sidecar"
$env:PYTHONIOENCODING = 'utf-8'
foreach ($t in @("brain_e2e.py", "general_e2e.py", "teach_e2e.py", "files_e2e.py", "filler_e2e.py", "voice_ux_e2e.py")) {
    Write-Host "== $t"
    & .\.venv\Scripts\python.exe "tests\$t" $port $tok 2>&1 | Select-Object -Last 8
}
$m = Invoke-RestMethod "http://127.0.0.1:$port/metrics" -Headers $H
Write-Host ("metrics: " + ($m.summary | ConvertTo-Json -Compress))
