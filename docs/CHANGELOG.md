# Changelog

## Unreleased — the Evolution (2026-09-01)

### Phase 0 — scaffolding
- Six new tool modules registered as stubs with real risk tiers before any of them
  had behaviour, so an integration mistake surfaces on its own. Tools 80 -> 91.
- **`requirements.txt` was missing `opencv-python` and `mediapipe`.** Both were
  installed and load-bearing for the camera work; a fresh clone could not have
  rebuilt this project. Fixed before anything new was added to it.
- New gate `test_evolution_wiring.py` asserts each tool's **risk tier**, not just
  that it registered — the tier is the security boundary.

### Phase 1 — weather and projects
- `volatile.py`: readings with a shelf life (location, health). Nothing is read
  back without its age, and `fresh()` returns nothing at all once stale.
- `get_weather` now prefers a fresh phone fix over the configured home, ignores a
  stale one, and reports `as_of_minutes` from the observation's own timestamp.
- `list_projects` / `log_progress` / `estimate_completion` over new `projects` and
  `project_steps` tables — kept clear of `tasks`, which is reminders. The estimate
  measures the rate between recorded marks and refuses to invent a date from one
  data point.

### Phase 2 — his phone (location + health, one poller edit)
- `where_am_i` / `distance_to`: Telegram live location stored as a volatile fact,
  straight-line haversine distance, place names resolved by the geocoder weather
  already uses. No routing, no Nominatim.
- `get_health`: Apple Watch metrics pushed from iOS Shortcuts into the paired
  chat. Allow-listed metric names, range-checked values, unknown keys ignored,
  size-capped before parsing — untrusted external JSON treated as such.
- **`getUpdates` now asks for `edited_message`.** A live location share is
  delivered by EDITING the original message, and Telegram does not send an update
  kind that is missing from `allowed_updates` — so live sharing would have
  delivered one fix and then gone silent, with nothing in any log to say why.
- A live share edits its message every few seconds; only the first is
  acknowledged. Replying to each would be the message-storm failure again.

### Phase 3 — describing an object
- `analyze_object`: the existing Gemma 3 + mmproj pipeline with a prompt aimed at
  a THING rather than a scene — material, construction, what is notable or
  damaged — and an explicit instruction not to invent text it cannot read.
- A photo sent to the paired chat is now looked at instead of refused. The
  largest of Telegram's sizes is used (the first is a thumbnail), the caption
  becomes the question, and the temp copy is deleted afterwards.

### Phase 4 — face as a second signal (HIGH risk only)
- `face_confirm` runs AFTER the spoken yes and can only ever REFUSE. No path
  grants permission from a face; no flag disables the spoken gate. A photograph
  would pass a bare webcam match, so it may add confidence and never confer it.
- An UNAVAILABLE signal (nobody enrolled, camera busy, model missing) leaves the
  spoken gate standing alone — failing closed would lock him out of his own
  machine over a misbehaving webcam driver.
- Built on the SFace embedder already bundled; `check_once()` added because
  `consider()` caches its verdict for two seconds and a confirmation asks about now.
- Registered LOW, not SAFE: it can turn the webcam on, and the tier must describe
  what the handler does.
- **Verified end to end on the real install**: a HIGH-risk tool was given a
  spoken YES and refused anyway — denial detail "nobody is at the camera" — while
  MEDIUM ran untouched. Driven against `_debug_confirm_high`, a HIGH-risk no-op
  that exists only under JARVIS_DEBUG, because the real HIGH-risk tools empty the
  recycle bin or cut the power and a security test must not be able to do the
  damage it exists to prevent.

### Phase 5 — fabrication scaffold
- `generate_part` (OpenSCAD -> STL), `slice_part` (PrusaSlicer -> G-code with real
  time and filament estimates), `printer_status`, and a `PrinterBackend` seam with
  only `NoPrinterBackend` behind it.
- Ships a generic 0.4 mm FDM profile, bundled in the spec and looked up under
  `sys._MEIPASS` first, or the frozen build would silently slice with the
  slicer's own defaults.
- OpenSCAD 2021.01 and PrusaSlicer 2.9.6 installed as the official PORTABLE
  builds under `C:\AI` (the winget packages demand admin and a scheduled task
  cannot answer a UAC prompt). The gate now renders a real STL and slices it:
  **17m 8s, 3.73 g, 1249.41 mm** for a 20 mm cube. Zero skips.
- Closing the skip immediately found a bug it had hidden: the estimate parser
  read the last 8 KB of the G-code, but PrusaSlicer writes its estimates and
  THEN dumps its whole configuration after them — 353 lines for a cube — so the
  numbers sat just out of reach. Every offline test passed because captured
  output put them at the end. Reads the last 512 KB now.

### Adjusted from the handoff after reading the repo
- `tools/weather.py` already existed (Open-Meteo, keyless) — extended, not created.
- `analyze_image()` already existed on the Gemma 3 + mmproj pipeline — phase 3 is a
  prompt and an input path, not a pipeline.
- Nominatim not needed: `weather._geocode()` already resolves place names through
  Open-Meteo's free geocoder. Kept as a fallback only.
- insightface not used: `vision_identity.py` already does SFace embeddings, bundled
  and gated. insightface would add a Cython build and a ~300 MB **runtime** model
  download, breaking the offline guarantee and the bundle-the-models pattern.

## 0.4.0 — 2026-08-24

### Voice
- Shutdown/restart confirmations can be answered out loud ("yes" / "no"); anything else
  is treated as a decline. Cancel words tolerate the STT's multilingual drift.

### Performance
- Idle HUD CPU cut ~50x (41.7% of a core -> 0.8%): static glow layer, no animation once
  settled, no live panel blur, batched streaming renders, polling only while visible
- "What's on my screen" 18.8 s -> 8.8 s (condensed OCR, faster capture)
- SYSTEM panel snapshot 1.60 s -> 0.034 s

### Look
- Icon-first tab bar; the active tab keeps its name

### Security
- Session token and llama-server key are no longer visible on any process command line

## 0.3.0 — 2026-08-23

### UX
- Ambient HUD: orb-only by default; panels surface on use and fade back after a hold; voice tab control
- "Open X" (YouTube/Netflix/site) now opens the USER's real browser; JARVIS's hidden browser is for his own reading only
- Open/close apps and recycle/move/rename files no longer ask for confirmation (reversible); only shutdown/restart do — asked aloud, answerable by voice

### Speed / models
- STT: Parakeet TDT 0.6B v3 (int8) default; Gemma 4 26B-A4B selectable; OCR-first "what's on my screen"
- Ears warm in parallel with the LLM at boot; prompt cache pre-warmed

### Audit fixes (2026-08-23)
- Security: file-tool path-traversal escape closed; auth can no longer be disabled by an empty token
- Correctness: number words parsed in order ("twenty five"=25, was 5); "shut down the pc" no longer hijacked by close-app; "close X" can't fan out to unrelated windows; reminders honor "tomorrow"
- Robustness: LLM recovery restores service; mic self-heal no longer mutes the speaker; Piper cancel deadlock fixed; confirmation future leak fixed; stalled UI client can't freeze the voice pipeline

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

### Evening: model bake-off
- STT: Parakeet TDT 0.6B v3 (int8, onnx-asr) replaces whisper: 3x faster, ~0 errors on commands
- Gemma 4 26B-A4B benchmarked on-device (faster first token, smarter) but RAM-bound
  with vision on a 32 GB PC -> selectable, not default; harness kept in tests/
- Keyless weather reflex (Open-Meteo); vision tool 3-4x faster; prompt tuning

### Afternoon additions
- Speak-before-thinking fillers; STT base.en (3x faster); announce-then-act for
  apps/sites; tool-then-LLM composes without tools
- Teach-by-voice commands and routines, correction learning, lock skill
- FILES view (browse/find/preview/rename/move/recycle) with sandboxed file tools

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
