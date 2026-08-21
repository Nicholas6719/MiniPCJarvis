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
| Primary model | gpt-oss-20b MXFP4 vs Qwen3.6-35B-A3B UD-Q3_K_XL — **bake-off in progress**, winner becomes default | Both MoE ~3B active ≈ same speed class; measuring smarts + tool calling on-device |
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
| 1 | Foundation: voice loop (VAD→STT→LLM+tools→TTS), barge-in, state machine, HUD shell (orb/conversation/activity), 7 real tools w/ risk gating, memory store+recall, secrets, tray/hotkey, NSIS installer | **IN PROGRESS** |
| 2 | Wake word ("Jarvis"), always-listening mode, settings UI, first-run setup | queued |
| 3 | Memory UI (inspect/edit/forget/pin), memory governance | queued |
| 4 | Windows control depth: UI Automation, window mgmt, volume/display/clipboard | queued |
| 5 | Browser agent (Playwright) + research mode with visible sources | queued |
| 6 | Vision: screen understanding (Qwen3.6 mmproj is on disk already) | queued |
| 7 | Tasks/automations/routines; proactive assistance w/ quiet hours | queued |
| 8 | OS-like nav (full view set), dynamic view switching, HUD polish | queued |
| 9 | Security hardening, performance profiling, self-healing depth | queued |
| 10 | Plugin/MCP system | queued |

## Current Risks

- Qwen3.6-35B hybrid attention (Gated DeltaNet) may need a newer llama.cpp than b10488 → bake-off will reveal; upgrade llama.cpp if so.
- 16.8 GB model + Whisper + Piper + app under 32 GB shared RAM — memory pressure test needed before making Qwen the default.
- Piper voice quality is the weakest link in the "cinematic" feel — revisit (Fish Audio opt-in or better local model) after Phase 1.
- Google-Fonts import in UI needs local bundling before offline-first claim is true.
