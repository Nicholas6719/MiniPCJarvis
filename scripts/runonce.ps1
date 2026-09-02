# Run a .cmd in the REAL user session, once, and leave nothing behind.
#
# WHY THIS EXISTS. Every real-session check here goes through schtasks, because
# the agent shell's %APPDATA% is a virtualized shadow. The habit that grew around
# that was `schtasks /Create /SC ONCE /ST 23:59` — 23:59 being used to mean
# "never". It does not mean never. It means tonight.
#
# On 2026-09-02 a JARVIS_PCLIVE task written that way was left registered and
# would have run a generate-and-slice against his live app at 23:59 while he
# slept. A JARVIS_SOUND task from 2026-08-29 had already fired at exactly that
# time. This is the same shape as the orphaned JARVIS_SUITES_FULL task that sent
# him Telegram messages at midnight — the bug he asked, in as many words, never
# to see again.
#
# So: create, run, wait for the script's own DONE marker, delete. The delete is
# in `finally`, so it happens even if the wait times out or this is interrupted.
#
# And because a rule written down is not a guard — this one HAD been written down
# before the third escape — `sidecar/tests/check_stray_tasks.py` runs as the first
# gate of every sidecar build and refuses to build while any of these is still
# registered.
param(
  [Parameter(Mandatory = $true)][string]$Script,   # full path to the .cmd
  [string]$Log = "",                               # log to watch for "DONE"
  [int]$TimeoutSec = 900,
  [string]$TaskName = ""
)

if (-not $TaskName) { $TaskName = "JARVIS_RUNONCE_" + [IO.Path]::GetFileNameWithoutExtension($Script) }
if ($Log -and (Test-Path $Log)) { Remove-Item $Log -Force -ErrorAction SilentlyContinue }

try {
  schtasks /Create /TN $TaskName /TR "`"$Script`"" /SC ONCE /ST 23:59 /F | Out-Null
  schtasks /Run /TN $TaskName | Out-Null
  if ($Log) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
      if ((Test-Path $Log) -and (Select-String -Path $Log -Pattern '^DONE' -Quiet)) { break }
      Start-Sleep -Seconds 2
    }
    if (Test-Path $Log) { Get-Content $Log }
    else { Write-Output "no log at $Log" }
  }
} finally {
  schtasks /Delete /TN $TaskName /F | Out-Null
}
