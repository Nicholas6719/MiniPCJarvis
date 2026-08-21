# JARVIS Build Plan

Living document — decisions, roadmap, status. Updated as phases complete.

## Machine Profile

| | |
|---|---|
| Host | GMKtec NucBox K8 Plus (mini PC) |
| CPU | AMD Ryzen 7 8845HS — 8C/16T, Zen 4, ~3.8 GHz |
| NPU | XDNA (~16 TOPS) — unused for now (immature tooling) |
| GPU | Radeon 780M iGPU (RDNA3, gfx1103) — **no discrete GPU, no CUDA** |
| RAM | 32 GB DDR5-5600 dual channel (~17.8 GB visible to Vulkan UMA) |
| Disk | C: 1.9 TB NVMe (1.5+ TB free), D: 3.7 TB |
| OS | Windows 11 Pro build 26200 |
| Tooling | Python 3.12, Node 24, Rust 1.98, Git, WSL2 (unused), llama.cpp b10488 Vulkan build at `C:\AI\llama.cpp` |

**Measured LLM perf (llama.cpp Vulkan, `-ngl 999 -t 8 -fa on`):**
gpt-oss-20b MXFP4: **27.3 tok/s decode, 494 tok/s prefill** — ~85% of the DDR5-5600
bandwidth ceiling for a 3.6B-active model. SMT (16 threads) is *slower*; use 8.

## Locked Decisions

| Decision | Choice | Why |
|---|---|---|
| App shell | Tauri 2 (Rust) + React/TS/Vite | Rust toolchain already present; WebView2 preinstalled; tiny RAM footprint vs Electron (RAM is contended by the LLM); first-party tray/hotkey/autostart/NSIS bundler |
| AI backend | Python sidecar (FastAPI), spawned+supervised by Rust core | Richest local-AI ecosystem; user never opens a terminal |
| Inference | llama.cpp `llama-server` (existing tuned Vulkan build), managed as a child of the sidecar | Proven 27 t/s on this machine; OpenAI-compatible API; no Ollama daemon to babysit |
| LLM strategy | **Fully local first** (user choice) | Privacy, zero API cost; cloud can be added behind `LLMProvider` later |
| Primary model | **gpt-oss-20b MXFP4** (bake-off winner, 2026-08-21) | 27.3 t/s Vulkan, 6/6 tool-calling, ~1.7 s first token. Qwen3.6-35B-A3B (UD-Q3_K_XL) OOMs the Vulkan heap on this iGPU even with `--cpu-moe`, host-memory placement, and llama.cpp b10549 — CPU-only it manages just 9.2 t/s: 3× too slow for voice. Kept on disk (+mmproj) as a vision-phase candidate where latency matters less |
| STT | faster-whisper `small.en` int8 CPU | Best CPU speed/accuracy tradeoff; `base.en` fallback if contended |
| VAD | Silero (ONNX, bundled with faster-whisper) | Streaming, low CPU, drives barge-in |
| TTS | **Piper local** (user choice), voice `en_GB-alan-medium` | Real-time on CPU, private, free; Fish Audio cloud optional later behind `TTSProvider` |
| Embeddings | fastembed / bge-small-en-v1.5 (ONNX) | Torch-free (packaging), fast on CPU |
| Search | Brave Search API (user choice) | Key stored in Windows Credential Manager only |
| Secrets | Windows Credential Manager via Rust `keyring`; pushed to sidecar over token-authed loopback; memory-only there | Never in files/logs |
| Packaging | PyInstaller sidecar + `tauri-bundler` NSIS → `JARVIS-Setup.exe` | Real installer, Start Menu, tray, clean uninstall |
| Wake word | Phase 2 (openWakeWord/Porcupine research) — Phase 1 is push-to-talk `Ctrl+Shift+J` | Keep Phase 1 shippable |

Rejected: Electron (RAM tax), WinUI3/.NET (no SDK installed, weaker AI interop),
Ollama (extra daemon), llama-cpp-python (Windows Vulkan build pain, llama-server
already tuned), sentence-transformers (torch dependency, packaging weight),
dense 27-31B models (Gemma 4 31B, Qwen3.6-27B — smarter but ~4 t/s decode here:
unusable for voice).

## Phase Roadmap

| Phase | Scope | Status |
|---|---|---|
| 0 | Discovery: machine inspection, model research, architecture | **DONE** |
| 1 | Foundation: voice loop (VAD→STT→LLM+tools→TTS), barge-in, state machine, HUD shell (orb/conversation/activity), 7 real tools w/ risk gating, memory store+recall, secrets, tray/hotkey, NSIS installer | **DONE 2026-08-21** — installer verified: silent install → launch → full boot to idle, no terminal. Human-at-machine checks still open: mic voice turn, barge-in feel, tray/hotkey UX |
| 2 | Wake word ("Hey Jarvis" via openWakeWord ONNX, ~2% of one core), always-listening mode, Settings UI (wake/audio/voice/model/search-key/autostart) with live-apply config API, Memory browser (list/search/forget), 4 en_GB voices | **DONE 2026-08-21** — wake detector scored 0.999 on TTS-spoken positives, 0.000 on negatives; installed build verified booting with wake loop active. Human checks open: real-mic wake reliability + sensitivity tuning |
| 3 | Memory governance depth (edit/pin/categories UI, retention), first-run wizard | queued |
| 4 | Windows control depth: window mgmt (list/focus/min/max/close), volume/mute, media keys, clipboard, screenshots, open URL, lock, power actions (HIGH-gated) — 15 tools | **DONE 2026-08-21** — all handlers tested live; LLM turns verified. UI Automation (element-level control) deferred to the computer-use/vision phase where it pairs with screen understanding |
| 5 | Research agent: `research` tool (search → parallel fetch → extracts + citations, staged events visible in UI) + `fetch_page` (httpx/2 + trafilatura; engine-agnostic interface so Playwright can slot in later for interactive browsing) + first-run experience (boot overlay on real events + setup wizard) | **DONE 2026-08-21** — fetch verified on Wikipedia/GitHub/news sites; research end-to-end pending the user's Brave API key |
| 6 | Vision: `analyze_screen`/`analyze_image` via on-demand Gemma3-4B+mmproj server (lazy start, 5-min idle auto-stop; ~5 s warm) | **DONE 2026-08-21** — described real screen accurately through full voice pipeline |
| 7 | Reminders/routines: scheduler (one-shot + daily/weekdays/weekly), proactive spoken announcements, set/list/cancel tools | **DONE 2026-08-21** — fire/recur/cancel verified; quiet-hours + richer proactive triggers still queued |
| 8 | AI-OS interface: 6-view nav (Conversation/Research/Memory/Tasks/Diagnostics/Settings), Diagnostics w/ 12 live checks + repair actions, Research view w/ sources+conclusions, Tasks view, persistent status bar, dynamic view switching | **DONE 2026-08-21** — all views verified against live data; installer deployed |
| 9 | Security hardening, performance profiling, self-healing depth | queued |
| 10 | Plugin/MCP system | queued |

## Current Risks

- Qwen3.6-35B hybrid attention (Gated DeltaNet) may need a newer llama.cpp than b10488 → bake-off will reveal; upgrade llama.cpp if so.
- 16.8 GB model + Whisper + Piper + app under 32 GB shared RAM — memory pressure test needed before making Qwen the default.
- Piper voice quality is the weakest link in the "cinematic" feel — revisit (Fish Audio opt-in or better local model) after Phase 1.
- Google-Fonts import in UI needs local bundling before offline-first claim is true.
