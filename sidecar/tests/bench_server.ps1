# Launch a throwaway llama-server for model bake-offs on port 8099 (no API key).
#   powershell -File tests\bench_server.ps1 -Model C:\AI\models\x.gguf [-Mtp path] [-Mmproj path] [-Extra "..."]
param([string]$Model, [string]$Mtp = "", [string]$Mmproj = "", [string]$Extra = "", [int]$Port = 8099, [int]$Ctx = 16384)
Get-Process llama-server -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -match "--port $Port" } | Stop-Process -Force -ErrorAction SilentlyContinue
$args = @("-m", $Model, "-c", "$Ctx", "--host", "127.0.0.1", "--port", "$Port", "-ngl", "999", "-t", "8", "-fa", "on", "--jinja", "--cache-reuse", "256",
          "--log-file", "C:\Users\nicho\Documents\bench_llama.log")
if ($Mtp) { $args += @("--model-draft", $Mtp, "--spec-type", "draft-mtp", "--spec-draft-n-max", "4") }
if ($Mmproj) { $args += @("--mmproj", $Mmproj) }
if ($Extra) { $args += $Extra.Split(" ") }
$p = Start-Process -FilePath "C:\AI\llama.cpp\llama-server.exe" -ArgumentList $args -PassThru -WindowStyle Hidden
"pid $($p.Id)"
$deadline = (Get-Date).AddSeconds(240)
while ((Get-Date) -lt $deadline) {
    try { $h = Invoke-RestMethod "http://127.0.0.1:$Port/health" -TimeoutSec 3; if ($h.status -eq "ok") { "READY"; exit 0 } } catch {}
    if ($p.HasExited) { "EXITED code $($p.ExitCode)"; Get-Content C:\Users\nicho\Documents\bench_llama.log -Tail 15; exit 1 }
    Start-Sleep 3
}
"TIMEOUT"; exit 1
