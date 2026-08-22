# Changelog

## 0.2.0 — 2026-08-22 (Brain layer, speed, "never leave JARVIS")

### Brain (JARVIS's own intelligence, no LLM needed)
- Embedding kNN intent router over canonicalized phrasings (bge-small, 62 ms);
  22 reflex skills (time, date, volume, mute, apps, sites, screenshots, search,
  images, screen, reminders, remember/recall, stats, windows, media, clipboard)
  answer in ~0.3 s with templated speech; 34/34 held-out accuracy
- Search / images / open-site / recall run the tool first, then one LLM round
- General knowledge questions detected -> LLM answers directly (tools omitted)
- Self-learning from clean single-tool LLM turns with sanity filters; seeds win
- `/brain`, `/brain/teach`, `/brain/classify`; BRAIN panel in Diagnostics;
  reflex rows in Activity

### Speed
- Static system prompt + per-turn context in the user message -> KV prompt cache
  hits (prompt eval ~1 s instead of ~10 s)
- FIX: search-intent regex matched the context note ("current time") so every
  turn forced a tool call; now only the user's words count

### Everything inside JARVIS
- `open_url` / browser tools run in a hidden Brave profile; live page screenshots
  in a new BROWSER view with URL bar + back; search results open in-app
- FIX: Brave path typo made Playwright fall back to a visible Edge window
- `open_application` resolves alias -> Start Menu -> PATH -> Store apps and never
  spawns a shell (a bad name used to pop a cmd window)
- Hidden browsers excluded from "what windows are open"
- Auto-switch back to CONVERSATION when a new turn starts

### Build
- `scripts/build_sidecar.cmd` gates PyInstaller on compileall + imports

## 0.1.0 — 2026-08-21 (initial build, phases 0–12 in one session)

### Core
- Tauri 2 Windows app: NSIS installer, Start Menu, tray, global hotkey
  (Ctrl+Shift+J), autostart toggle, supervised Python AI sidecar
- Full local speech-to-speech: Silero VAD → faster-whisper STT → gpt-oss-20b
  (llama.cpp Vulkan, 27 t/s) → Kokoro TTS (bm_george), with barge-in
  interruption (~0.15 s) and "Hey Jarvis" wake word (openWakeWord)
- 35+ tools with risk tiers (SAFE/LOW/MEDIUM/HIGH) and confirmation gating:
  apps, windows, volume/media, clipboard, screenshots, files, power, web
  search/research (Brave), page fetching, interactive browser agent
  (Playwright/Edge), vision (Gemma3-4B), reminders/routines, memory
- Persistent semantic memory (SQLite + ONNX embeddings) with pin/edit/forget UI
- Proactive intelligence (disk/RAM/break monitors) with quiet hours, cooldowns,
  hourly caps
- AI-OS HUD: 6 views (Conversation/Research/Memory/Tasks/Diagnostics/Settings),
  reactive core orb, live activity log, status bar, dynamic view switching,
  boot sequence + first-run wizard
- MCP plugin system: external tool servers via config, MEDIUM-risk default
- Hardening: audit log, per-session LLM API key, dynamic ports, LLM watchdog
  (67 s auto-recovery), Rust-side sidecar supervisor, turn metrics

### Model decisions
- gpt-oss-20b MXFP4 chosen over Qwen3.6-35B-A3B after on-device bake-off
  (Qwen OOMs the 780M Vulkan heap; 9 t/s CPU-only vs 27 t/s)
- Kokoro-82M chosen over Piper for voice quality (3.4–3.8× realtime on CPU)
