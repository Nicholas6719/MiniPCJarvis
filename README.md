# J.A.R.V.I.S.

A speech-first personal AI operating layer for Windows. Local-first: the language
model, speech recognition, speech synthesis, and memory all run on this machine.

## What it is

- A real Windows desktop app (Tauri 2): taskbar, Start Menu, system tray,
  global hotkey (`Ctrl+Shift+J`), NSIS installer.
- Speech-to-speech primary interface: VAD → Parakeet TDT STT → local LLM with
  tool calling (llama.cpp Vulkan) → streaming Kokoro TTS, with barge-in interruption.
- A learned intent router ("the brain") answers known requests in ~0.3 s without
  touching the language model at all, and learns new phrasings from real use.
- Keyless web search and research, driven through a hidden Brave of its own —
  no API key and no account.
- A real tool system with per-tool risk classification and confirmation gating.
- Persistent semantic memory (SQLite + ONNX embeddings).
- HUD-style UI: reactive core orb, conversation view, live activity log showing
  actual tool calls — nothing simulated.

## Layout

```
src-tauri/   Rust core: window, tray, hotkey, sidecar supervision, Credential Manager
src/         React/TS HUD frontend
sidecar/     Python AI backend: orchestrator, LLM, STT/TTS/VAD, tools, memory
docs/        Architecture, security, build plan
```

## Development

```
# Terminal 1 — nothing needed; the Rust core spawns the sidecar itself.
npm install
cd sidecar && python -m venv .venv && .venv\Scripts\pip install -r requirements.txt
cd ..
npm run tauri dev
```

Requires the llama.cpp Vulkan build at `C:\AI\llama.cpp` and a model GGUF at
`C:\AI\models\` (see `sidecar/config.py` defaults).

## Production build

```
npm run tauri build   # → src-tauri/target/release/bundle/nsis/JARVIS-Setup.exe
```

See `docs/JARVIS_BUILD_PLAN.md` for the roadmap and decision log.
