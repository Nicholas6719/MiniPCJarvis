# Changelog

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
