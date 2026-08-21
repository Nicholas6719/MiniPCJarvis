# JARVIS Troubleshooting

## JARVIS won't respond to voice
1. Open **Diagnostics** — check Microphone (should say "capturing") and Wake Word.
2. Check **Settings → Audio → Microphone** is the right device; try "System default".
3. Wake mode: Settings → Voice Activation. In "Push to talk" mode, "Hey Jarvis" is off.
4. Wake sensitivity too high = missed activations; too low = false triggers.

## No speech output
1. Diagnostics → Voice Synthesis. REPAIR reloads the engine.
2. Settings → Audio → Speaker: pick the active output device.
3. Kokoro voices (bm_*/bf_*) need `%APPDATA%\JARVIS\voices\kokoro\` model files;
   if missing, JARVIS falls back to Piper (en_GB voices) automatically.

## "Language model failed to start" / stuck at INITIALIZING
- The model needs ~12 GB free RAM. Close heavyweight apps; JARVIS retries with
  backoff automatically and recovers within about a minute of RAM freeing up.
- Model file expected at `C:\AI\models\gpt-oss-20b-MXFP4.gguf`, engine at
  `C:\AI\llama.cpp\llama-server.exe` (configurable in `%APPDATA%\JARVIS\config.json`).

## Web search says it isn't configured
Settings → Web Search → paste a Brave Search API key (free tier:
https://brave.com/search/api). Stored in Windows Credential Manager only.

## Browser tools fail to launch a browser
JARVIS drives your system Microsoft Edge. If Edge was removed, install Google
Chrome — it's used as the second choice.

## Everything is broken
1. Diagnostics → RUN CHECKS → REPAIR buttons.
2. Tray icon → Exit, then relaunch JARVIS (the app supervises and restarts its
   own subsystems, and the shell restarts a dead backend automatically).
3. Logs: `%APPDATA%\JARVIS\logs\` (sidecar.log, llama-server.log, vision-server.log).
4. Nuclear: uninstall via Windows Apps & Features, delete `%APPDATA%\JARVIS`
   (this erases memories/settings), reinstall.

## Where things live
| What | Where |
|---|---|
| App | `%LOCALAPPDATA%\JARVIS` |
| Settings/memory/logs/voices | `%APPDATA%\JARVIS` |
| Models | `C:\AI\models` |
| Secrets | Windows Credential Manager (service "JARVIS") |
