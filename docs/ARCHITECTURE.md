# JARVIS Architecture

```
┌─────────────────────────────────────────────────────────┐
│  JARVIS.exe  (Tauri 2 — Rust core)                      │
│  window · tray · global hotkey · autostart              │
│  Credential Manager (keyring/DPAPI)                     │
│  sidecar supervision (spawn / health / tree-kill)       │
└──────────────┬──────────────────────────┬───────────────┘
       Tauri IPC (invoke)          loopback HTTP/WS + token
               │                          │
┌──────────────▼───────────┐   ┌──────────▼───────────────────────────┐
│  React HUD (WebView2)    │   │  jarvis-sidecar (Python, PyInstaller)│
│  core orb · conversation │◄──┤  FastAPI + WS event bus              │
│  activity log · confirm  │ WS│  ┌────────────────────────────────┐  │
│  zustand store           │   │  │ Orchestrator (turn engine)     │  │
└──────────────────────────┘   │  │  state machine (13 states)     │  │
                               │  │  mic → VAD → STT → LLM(tools)  │  │
                               │  │  → sentence split → TTS → out  │  │
                               │  │  barge-in / stop-words         │  │
                               │  └───┬──────────┬──────────┬─────┘  │
                               │      │          │          │        │
                               │  ┌───▼───┐ ┌────▼────┐ ┌───▼─────┐  │
                               │  │ LLM   │ │ Tools   │ │ Memory  │  │
                               │  │ layer │ │ registry│ │ SQLite+ │  │
                               │  └───┬───┘ │ risk    │ │ vectors │  │
                               │      │     │ gating  │ └─────────┘  │
                               └──────┼─────┴─────────┴──────────────┘
                                      │ manages child (job object)
                               ┌──────▼──────────────────────┐
                               │ llama-server (llama.cpp)     │
                               │ Vulkan iGPU · OpenAI API     │
                               └─────────────────────────────┘
```

## Key contracts

**State machine** (`sidecar/state_machine.py`, mirrored in `src/state/store.ts`):
`OFFLINE STARTING IDLE LISTENING PROCESSING THINKING SEARCHING EXECUTING WAITING
SPEAKING INTERRUPTED ERROR SLEEPING` — transitions validated in `_ALLOWED`; every
change broadcast as a `state` event; the UI orb renders from it directly.

**Event bus** (`sidecar/events.py` → WS `/ws`): every user-visible fact is an
event — `transcript`, `assistant_delta`, `tool_call` (pending/success/error/denied),
`confirmation_required`, `speaking`, `interrupted`, `turn_done`, `boot`, `error`.
The UI never fabricates status; it renders events.

**Tool contract** (`sidecar/tools/registry.py`): name, JSON-schema parameters,
risk tier (SAFE/LOW/MEDIUM/HIGH), timeout, async handler. MEDIUM+ requires a
`confirmation_required` round-trip; deny/timeout refuses execution and tells the
model so. New tools = one `Tool(...)` registration; nothing else changes.

**Provider interfaces**: `llm/provider.py` (LocalLLM streams OpenAI-compatible
chunks; a CloudLLM can slot in), `audio/tts.py` (PiperTTS today, Fish Audio
later), `audio/stt.py` (faster-whisper today). The orchestrator only sees the
interface.

**Turn pipeline** (`sidecar/orchestrator.py`): capture → transcribe → memory
recall (semantic, top-4) → system prompt (static prefix first for llama-server
prefix-cache hits; time/memory appended last) → stream LLM → sentences flush to
TTS as they complete → tool calls execute mid-stream (max 6 rounds) → history
trimmed to 20 messages. Barge-in: a dedicated VAD watcher aborts playback within
~200 ms of sustained user speech and flips straight to LISTENING.

## Resilience

- Rust supervisor: spawns sidecar with random port+token, health-checks, kills
  the whole tree on exit (`taskkill /T`).
- Sidecar: llama-server child assigned to a Windows Job Object with
  kill-on-close — no orphaned 11 GB processes even on hard kill.
- LLM startup failure (e.g. OOM) → ERROR state + retry loop with backoff; the
  HTTP/WS surface stays up throughout so the UI can show what's wrong.

## Data locations

- `%LOCALAPPDATA%\JARVIS\` — config.json, jarvis.db (memories + transcript),
  logs/, voices/
- Secrets: Windows Credential Manager, service `JARVIS` — nowhere else.
- Models: `C:\AI\models\` (GGUF), engine `C:\AI\llama.cpp` (dev machine); the
  installer will provision these under app data for clean installs.
