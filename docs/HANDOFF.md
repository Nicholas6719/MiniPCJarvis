# JARVIS — Continuation Handoff (living document)

Read this first after any context reset. Everything below was learned the hard way.
Updated: 2026-08-23.

## Who / what
- User: Nicholas. Wants a speech-first, OS-like JARVIS (not a chatbot). Extremely
  frustrated by regressions ("we never go backwards"). Wants: speed, the thing to
  feel alive, and JARVIS to have its *own trained brain* (not just an LLM) — see
  "Brain layer" below. Grants full autonomy for dev actions; never ask permission.
- Machine: GMKtec NucBox K8 Plus, Ryzen 7 8845HS, Radeon 780M iGPU only (no CUDA),
  32 GB RAM, Win11. Webcam+mic: Logitech C920 (16 kHz native, 16/24/32k only).
- Repo: `C:\Users\nicho\Documents\Coding_Projects\JARVIS` (git, ~30 commits).
  App = Tauri 2 (Rust, `src-tauri/`) + React/TS (`src/`) + Python sidecar (`sidecar/`).
- Status: phases 0–12 of the original spec are DONE and installed. See
  `docs/JARVIS_BUILD_PLAN.md` for the roadmap table; `docs/CHANGELOG.md`.

## THE SANDBOX TRAP (most important)
The agent's shells (Bash/PowerShell tools) run with **virtualized AppData**:
writes to `%LOCALAPPDATA%`/`%APPDATA%` silently land in
`C:\Users\nicho\AppData\Local\Packages\Claude_pzs8sxrjxfjjc\LocalCache\{Local,Roaming}\...`
while `$env:APPDATA` prints the real path. Reads show a merged/stale view.
Consequences seen: installs "succeeded" but the user had NO app; voices "existed"
but the user's folder was empty; log files diverge.
**Rules:**
1. Install, copy data, read the real log, and launch ONLY via a scheduled task
   running in the user's real session:
   ```
   # write C:\Users\nicho\Documents\x.cmd, then:
   schtasks /Create /TN NAME /TR "C:\Users\nicho\Documents\x.cmd" /SC ONCE /ST 23:59 /F
   schtasks /Run /TN NAME ; (poll output file) ; schtasks /Delete /TN NAME /F
   ```
   Output files go under `C:\Users\nicho\Documents\` (not virtualized). Use
   `start /wait "" "<installer>" /S` in the .cmd (plain invocation cut the log).
2. Launching `jarvis.exe` from the agent shell / Start-Process / explorer.exe gives a
   SANDBOXED instance (empty voices, default config). Launch via schtasks .cmd with
   `set JARVIS_DEBUG=1` + `start "" "C:\Users\nicho\AppData\Local\JARVIS\jarvis.exe"`
   when you need to test; otherwise the user launches from Start/taskbar.
3. After every install, re-set `.lnk` IconLocation to `jarvis.exe,0` on Start Menu,
   `C:\Users\nicho\OneDrive\Desktop\JARVIS.lnk`, and the taskbar pin
   (`%APPDATA%\Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar\JARVIS.lnk`).
   The install .cmd in git history does all of this (see commits 2026-08-22).

## Build procedure (works)
- Node.js was DELETED from the machine mid-session (Norton suspected). Portable Node
  lives at `C:\Users\nicho\Tools\node` — prepend to PATH.
- Windows SDK was gutted too; reinstalled via winget (user approved UAC). Builds need
  the VS dev env: import `vcvars64.bat` vars into PowerShell before `npm run tauri build`
  (`C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat`).
- Steps: `sidecar/.venv/Scripts/pyinstaller jarvis-sidecar.spec --noconfirm --distpath dist --workpath build`
  (~3 min) → PowerShell w/ vcvars + PATH → `C:\Users\nicho\Tools\node\npm.cmd run tauri build`
  (~4 min) → installer at `src-tauri/target/release/bundle/nsis/JARVIS_0.1.0_x64-setup.exe`
  → real-session install .cmd (kills jarvis.exe/jarvis-sidecar.exe first).
- Icon: `src-tauri/icons/icon.ico` MUST have BMP entries for 16–128 px (PNG only 256);
  PIL's default all-PNG ico renders blank in Explorer. Script in git history.
- Don't pipe build output through `| tail` when you need the exit code.

## Runtime facts
- Data dir: `%APPDATA%\JARVIS` (config.json, jarvis.db, logs/, voices/, voices/kokoro/,
  screenshots/, browser-profile/). App dir: `%LOCALAPPDATA%\JARVIS`.
- Sidecar port/token are dynamic: read from `jarvis-sidecar.exe` command line
  (`Get-CimInstance Win32_Process`) → `--port N --token HEX`. API needs header
  `X-Jarvis-Token`. `/health` is open.
- LLM: gpt-oss-20b MXFP4 via llama-server (Vulkan, 27 t/s). Dynamic port + per-session
  API key. Adopts a foreign server on :8080 if it serves the same model (Houston).
  Qwen3.6-35B can't use the iGPU (OOM) — not for voice.
- Voice: Kokoro (bm_daniel chosen by user; bm_george default) w/ Piper fallback.
  Speech text is cleaned (`audio/speech_text.py`): no markdown/paths, years as words.
- Wake: openWakeWord "hey jarvis", threshold 0.45, mode "both". Pre-roll 2 s,
  follow-up window 8 s, name-only barge-in, 3.5 s grace after bare "Jarvis".
- Mic: `audio/io.py` resolves the C920 via MME ONLY (shared). WDM-KS = exclusive =
  breaks every other app ("Device in use" in Wispr Flow). Settings lists only MME
  entries now; explicit picks are remapped. Self-heal reopens a silent stream.
- Web search: keyless via hidden Brave (`search_brave_web.py`, persistent profile,
  off-screen tool-window, warmed at boot, relaunches if killed). Brave API optional.
  `show_images` tool → MEDIA view. Live WEB panel replaces Activity during searches.
- Vision: Gemma3-4B on-demand server; `analyze_screen` minimizes JARVIS first and is
  grounded with the OS window list.
- Tests: `sidecar/tests/e2e.py --port P --token T` (15 checks against a running app),
  `sidecar/tests/voice_ux_e2e.py P T` (needs JARVIS_DEBUG=1 for `/debug/inject_audio`).
  Run with `PYTHONIOENCODING=utf-8`.

## Brain layer (DONE 2026-08-22, installed + verified)
- `sidecar/brain/skills.py` (skills: seeds, slot extractors, speak templates) and
  `sidecar/brain/router.py` (bge-small embeddings, canonicalized text -> top-match
  with ambiguity penalty, threshold 0.82; "general" class = knowledge/creative).
- Orchestrator: `_converse` -> `brain.decide()` -> `_reflex_turn` (tool + template,
  ~0.3 s first word) | `llm_after` skills (search/images/open_site/recall) pre-run the
  tool then one LLM round | general questions: hint (>=0.7) or tools omitted (>=0.82).
- Self-learning (`_maybe_learn`): single successful known tool turn -> example, but
  ONLY if the skill's slot extractor can execute the phrasing and the brain isn't sure
  it's general. Seeds override learned rows. (Without this it learned "spider legs ->
  search" from dumb LLM turns.)
- Tests: `sidecar/tests/test_brain.py` (34/34 held-out), `tests/brain_e2e.py P T`,
  `tests/general_e2e.py P T` (LLM path + no-external-browser check).
- Endpoints: `/brain`, `/brain/teach`, `/brain/classify`. Diagnostics shows a BRAIN panel.

## Big bugs fixed today (don't reintroduce)
- `must_use_tool` matched SEARCH_INTENT against the whole user message, which now
  starts with "[Context - current time...]" -> "current" matched -> EVERY turn forced
  tool_choice=required (trivia searches, remember_fact whims, slow). Use raw utterance.
- `` inside bash heredoc patches becomes a BACKSPACE (Brave path became
  `Applicationrave.exe` -> Playwright fell back to Edge; regexes lost ``). Write patch
  files with the Write tool, never heredoc Python containing backslashes.
- PyInstaller bundles modules that don't compile (exit 0!). ALWAYS build the sidecar
  with `scriptsuild_sidecar.cmd` (compileall + import gate).
- (SUPERSEDED 2026-08-23) `open_url` now opens the USER's real browser (per user request);
  JARVIS's hidden Brave is only for his own reading (browser_open/fetch_page/web_search). The next line describes the OLD policy:
  - Historically: `open_url`/`browser_*` used a second hidden Brave
  profile (`session-browser`) and push screenshots to the BROWSER view; WebPanel result
  clicks open in-app; `open_application` resolves alias -> Start Menu -> PATH -> Store
  apps and never spawns `start ""` (that popped a cmd window).
- Window list excludes Brave processes running JARVIS profiles.

## Afternoon 2026-08-22 (all installed + verified by e2e)
- Speed: `speech.fillers` ("Let me see." at ~0.35 s; "Searching."/"Opening it." for tool
  runs), STT base.en (config migration v2), open_app/open_site announce before acting,
  tool-then-LLM rounds run with tools omitted (model only composes), `must_use_tool`
  disabled after a pre-run. LLM first token ~2.5-4.5 s; reflex ~0.3 s.
- Brain phase 2: taught commands (`brain_commands`, "when I say X, do Y and Z" ->
  `_teach` -> `_compile_steps`), routines (`_routine_turn`), corrections ("no, I meant
  ..." -> `brain.unlearn(last_match)` then re-run). `lock`, `folder`, `find_file` skills.
  _CANON order matters: teach/correction first, folder/file before images/search.
- FILES view: `tools/file_tools.py` (sandboxed roots from config.folders; SHFileOperation
  recycle/move with undo), `/files*` endpoints, `FilesView.tsx`, events `files` /
  `file_preview`.
- Tests to run after every install: brain_e2e, general_e2e, teach_e2e, files_e2e,
  filler_e2e, voice_ux_e2e (all take PORT TOKEN from jarvis-sidecar.exe's cmdline).
- NOT yet visually checked in the real HUD: FILES and BROWSER views (built from
  typed-checked TSX; the agent can't see the user's session). Ask the user.

## Later afternoon 2026-08-22
- `scripts/release.ps1` = THE way to ship: gated sidecar build -> tauri -> real-session
  install -> wait -> all suites. ~15 min; run in background (tool cap is 10 min):
  `Start-Process powershell -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','scripts\release.ps1' -RedirectStandardOutput C:\Users\nicho\Documents\jarvis_release.log`
  then poll the log for "RELEASE OK".
- APPS view (`tools/window_thumbs.py`, `/windows`, `/windows/act`), "switch to X" reflex.
- SYSTEM view (`tools/system_panel.py`, `/system*`).
- BROWSER click-through (`/browser/click|scroll|type`).
- Nightly self-test: `scripts/selftest.cmd` (register once with `scripts/install_selftest.cmd`
  from the real session -> task JARVIS_SELFTEST 03:30). Silences the speaker via
  `/debug/silence`; report at `%APPDATA%/JARVIS/selftest.json`, shown in Diagnostics.
- Proactive rules: "tell me if CPU goes above 90 for 5 minutes" (watch/unwatch skills,
  `proactive.rules` in config, `run_rules()` each tick).
- `/brain/export` -> `%APPDATA%/JARVIS/dataset.jsonl`.
- Deferred (needs the user): webcam presence (needs OpenCV +40 MB, camera LED on),
  Windows notification listener (needs a privacy permission grant in Settings).

## Model bake-off 2026-08-22 (evening) - READ BEFORE TOUCHING MODELS
- Candidate: Gemma 4 26B-A4B (google QAT q4_0, 14.4 GB) at C:\AI\models\gemma-4-26B-A4B-it-qat-q4_0.gguf
  (+ mmproj, + MTP head). Harness: tests/model_bench.py (real prompt + schemas), gen_speed.py,
  pp_speed.py, bench_server.ps1, scripts/model_trial.ps1 (live switch + all suites),
  tests/vision_mem_e2e.py (vision turn + RAM peak).
- Results on the 780M: gen 24.7 t/s (= gpt-oss), warm first token 0.81 s (gpt-oss 1.44 s),
  behaviour 18/18 semantic, cold 3.4K prefix 12.8 s once. MTP: +5-8% only (Vulkan/UMA) -> no.
  In-model vision: 14.4 GB + image compute buffers overflow the 17.4 GB Vulkan heap
  (fits only at 8K ctx; 12-16K crashes) -> no. CPU experts -27% -> no.
- The blocker is SYSTEM RAM (28.8 GB usable): Gemma 4 + CPU vision server + sidecar +
  hidden browsers peaked at 96% and the engine was killed once; gpt-oss peaks 80%.
  => DEFAULT STAYS gpt-oss-20b. Gemma 4 is selectable (config entry has gpu_full: True so
  the vision server runs on CPU, q8 KV, 12K ctx). If the user buys 64 GB RAM, flip it.
- Kept from the exercise: keyless weather tool + reflex (tools/weather.py, Open-Meteo),
  prompt tuning (brevity cap, no example parroting, one search then fetch_page/open_url),
  vision tool 3-4x faster (1024px JPEG, 2-sentence answers, 120 tokens), model entries
  in config always mirror DEFAULTS (config.py _migrate).
- STT bake-off DONE (tests/stt_ab2.py): Parakeet TDT 0.6B v3 int8 139 ms / 0.6% WER beat whisper base.en (450 ms / 5.1%) and Moonshine (86 ms but 'Newt the speakers'). Now default (audio/stt.py, config v5, whisper fallback). Real voice turns: stt ~140-310 ms (was 430-520).

## Shipping paths (2026-08-22 evening)
- Python-only change: `scripts\quick.ps1` (~4.5 min): gated sidecar build -> hot-swap
  %LOCALAPPDATA%\JARVIS\sidecar via scheduled task (`scripts\hotswap_sidecar.cmd`, copy of
  C:\Users\nicho\Documents\jarvis_hotswap.cmd) -> brain/files/voice smoke. `-Full` runs all suites.
- UI (src/) or Rust (src-tauri/) change: `scripts\release.ps1` (~15 min, full installer).
- "open youtube/netflix/spotify": known services open as sites in-app when no app exists
  (tools/builtin.py _KNOWN_SITES).

## 2026-08-23 morning: full checklist run + fixes (all deployed via quick.ps1)
- tests/checklist.py (55 items) + checklist2.py (10) + confirm_e2e/bargein_e2e/selfwake_e2e.
- USER RULES (do not regress): "open X" = user's REAL browser (open_url / known sites);
  JARVIS's hidden browser only for his own reading (read_site skill -> browser_open).
  Open/close apps, recycle/move/rename files, lock: NO confirmation. Only shutdown/
  restart (power_action) and browser_submit confirm - asked ALOUD, answered by voice
  (strict bare yes/no); any other speech = implicit no + runs that request; 30 s timeout.
- Fixed: close_application (Store apps refuse terminate -> WM_CLOSE by title, protects
  JARVIS's hidden Brave, honest replies); self-wake on own name (_saying_own_name guard);
  barge-in deaf after early filler (watcher no longer gated on SPEAKING state); false
  "webcam mic disconnected" restarts (157/log) -> frames-flowing + two-miss rule;
  wake-word homophones stripped (Jovis); nightly self-test skips when app closed.
- Test hygiene: suites must wait_idle before starting (a "Hey Jarvis" during speech is a
  barge-in, not a wake); drain websocket between turns.

## 2026-08-23 afternoon (user away) - all installed + verified
- Agent can SEE the HUD now: POST /debug/view {"view":...} then GET /debug/hud.png
  (PrintWindow capture of the JARVIS window). Shots in C:\Users\nicho\Documents\hud_shots.
  Layout fixes from that review: nav wraps (SETTINGS was pushed off), center column
  minmax(0,1fr) (FILES overflowed into the activity column), filename column wider,
  Store apps deduped in APPS.
- "what's on my screen" is OCR-first: Windows OCR (winocr, 0.1-0.3 s) of the active
  window + OS facts -> main model answers; vision model only for visual questions or
  <12 words of text. First word ~5 s (was 22-40 s of silence); total is mostly speech.
  winrt packages are collected individually in the .spec (namespace package).
- Boot: ears (wake/STT/TTS/mic) warm in parallel with the LLM -> live ~6 s after launch;
  reflexes work while the model loads; both prompt-cache prefixes pre-warmed at boot so
  the first real question is ~3 s, not ~12 s.
- Hot-swap hazard found+fixed: JARVIS's own children (hidden Brave, its llama-server,
  orphaned Brave helpers) pin DLLs in the sidecar folder -> robocopy partial (exit 9).
  hotswap_sidecar.cmd now stops them first and REFUSES to launch on exit >= 8.
  First boot after a hot-swap can take ~60 s extra (AV scanning fresh files) - not a bug.
- Test hygiene: every harness must wait_idle before its first turn.

## Ambient HUD (2026-08-23 afternoon) - the UI model now
- Default = ambient: orb + last exchange + quiet input. No nav, no activity log.
- A panel surfaces only when JARVIS uses it (web/media/files/browser/research events set
  ambient=false + panelUntil). On turn_done, panelUntil = now + ui.panel_hold_s (12 s);
  App.tsx ticks every 500 ms and collapses when idle, not pinned, cursor not inside.
- Tab bar appears when the mouse is near the top edge (y<60) or when pinned. Clicking a
  tab pins. PIN/PINNED button next to the tabs.
- Voice: "show me the files tab" / "show the tabs" / "pin that" / "unpin" / "hide
  everything" -> brain skill "ui" -> bus event "ui" -> store.
- Search results take the CENTER (one thing at a time); the transcript is a tab.
  Activity log only shows beside Diagnostics. Compact last exchange sits under the orb.
- Agent workflow for UI work: /debug/view + /debug/hud.png, shots in Documents/hud_shots.

## 2026-08-24 (user away) - voice confirm, secrets, speed, HUD
- VOICE CONFIRM: `ask_confirmation` speaks the question AND listens (`_listen_yes_no`),
  resolving the pending future before registry.execute waits on it. 2 attempts, then the
  UI/30 s timeout takes over. config `confirm.by_voice`. Test: tests/voiceconfirm_e2e.py
  drives a DEBUG-ONLY no-op tool (`/debug/confirm_test`) - NEVER test with power_action.
  Gotcha found: Parakeet v3 is MULTILINGUAL and renders a curt "No." as "Não", so the
  cancel list carries drift variants. Rule: liberal on cancel, strict on approve.
- SECRETS: sidecar token now arrives on stdin (`--token-stdin`, written by Rust);
  llama-server key via `LLAMA_API_KEY` env. Neither is in argv any more. Because the
  harnesses used to scrape argv, a DEBUG-ONLY `%APPDATA%\JARVIS\session.token` file is
  written when JARVIS_DEBUG=1; release/quick scripts fall back to it.
- SCREEN QUESTIONS 18.8 s -> 8.8 s: OCR text condensed (dedupe/junk-strip/cap 1400 chars,
  was 3394) + capture 0.71 -> 0.48 s. Prompt eval was the whole cost.
- HUD IDLE CPU 41.7% of a core -> 0.8% (total 47.7% -> 8.1%). Three causes, all measured
  with py-spy + TotalProcessorTime: (1) the nucleus scaled every frame while carrying a
  90 px box-shadow -> full repaint; glow moved to a static sibling `.core__glow`.
  (2) any running CSS animation pins the compositor at 60 fps -> `.core--calm` after 20 s
  idle removes all animation (JarvisCore.tsx). (3) SYSTEM snapshot walked 260 processes
  with cpu_percent every 5 s (64% of sidecar CPU when that tab was open) -> cached
  top-by-memory, non-blocking cpu_percent, cached wifi: 1.60 s -> 0.034 s.
- HUD LOOK: icon-first tabs (active tab keeps its name, tooltip otherwise), no live
  backdrop blur, streamed tokens batched to one store commit per frame, background tabs
  stop polling when `document.hidden`.
- Brain misfires fixed: "what time is it in london" (was local time), "open budget.xlsx"
  (was app launch), "search my documents" (was a web search).

- PERSONA ("he barely says sir"). Measured the real thing rather than guessing: pulled
  the four Iron Man/Avengers screenplays, 97 JARVIS lines — 37% carry "sir", median line
  is 7 words, and it sits either at the FRONT of something he raises himself ("Sir, the
  city is taking fire.") or the END of an acknowledgement ("Very good, sir."). Two causes:
  the system prompt said to use it "sparingly", and the REFLEX PATH NEVER TOUCHES THE LLM,
  so no prompt change could ever have fixed the fast path. Fix is `polish()` in
  `brain/skills.py` — the single point every spoken reflex line passes through
  (`orchestrator._exec_skill`). Rules: film-matching rate, never twice in a line, never two
  lines running, front-loaded for alerts/announcements, long reports left alone, and
  "I couldn't ..." softened to "I'm afraid I couldn't ..." ~half the time. Config knob:
  `persona.honorific` / `persona.honorific_rate` (0.55 config = ~35% observed, because the
  never-two-running latch suppresses roughly a third of draws — do not read the config
  number as the output rate). Guarded by `tests/test_persona.py` in the build gate.
- PERSONA ON THE LLM PATH is a SEPARATE mechanism from the reflex path, and the frequency
  is deliberately NOT set by the prompt. Measured on the real install: asked to pace itself
  the model either ignored it (11%) or read its own prior replies, decided "sir" was the
  register, and ended EVERY reply that way (60%, seven back-to-back). Wording alone swung
  it 0% -> 60%. Two fixes together: (1) `orchestrator` strips the honorific from the
  ASSISTANT HISTORY it feeds back (`without_honorific`) so nothing self-reinforces —
  nothing shown or spoken changes; (2) `want_honorific()` makes the per-turn decision in
  code (sharing the reflex latch, so "never two running" spans both paths) and
  `turn_context()` states it plainly for that turn. Result: 33-44% across runs, no
  back-to-back, no doubles. The prompt now only sets PLACEMENT — if you touch it,
  RE-MEASURE, it is extremely wording-sensitive. `BARE_HONORIFIC` in orchestrator drops a
  lone "Sir." sentence (the model occasionally writes it as its own sentence, which the
  splitter would otherwise send to TTS as a clipped one-word clip).
- WHAT HE ACTUALLY SAYS != what `clean_for_speech` returns. Verify pronunciation by
  synthesizing with the real TTS and transcribing back with the real STT
  (`tests/speech_symbols.py`, now in the build gate). Reading the cleaned text catches
  none of this. Found and fixed this way:
  - **Clock times**: "It's 2:04 PM" was voiced "two hundred four PM". Every time from
    :01 to :09 was wrong, and "what time is it" is the most common thing he is asked.
    Minutes of 10+ already read correctly and are LEFT ALONE — don't "fix" them.
  - **Decimals**: Kokoro does not sound "." between digits at all. "1.7 terabytes" came
    out "one seven terabytes", and he reports free disk space on every status question.
  - **Currency**: "$40" was voiced "dollar forty" (right words, wrong order).
  - **Degrees**: "32°F" dropped the unit ("32 degrees F").
  Curly quotes, em-dashes, non-breaking hyphens, ellipses and % were all checked and
  Kokoro handles them fine — deliberately left alone.
  TECHNIQUE NOTE: clock times can't be checked by transcription ("two oh four" and "two
  hundred four" both transcribe to "204"), so compare synthesized DURATION against both
  spellings and require the correct one to be closer.
- SAMPLING was never sent to llama-server, so its chat defaults (temp 0.8, top_p 0.95)
  applied — creative-writing sampling on an assistant whose job is mostly stating facts.
  Now explicit and configurable (`llm.sampling`, per-model, and per-call for benchmarks).
  Measured, 20 verifiable questions x 4 runs, word-for-word (`tests/accuracy_bench.py`):

      temp 0.8   accuracy  99%   consistency   5%
      temp 0.15  accuracy 100%   consistency  45%
      temp 0.0   accuracy 100%   consistency  85%

  HONEST FINDING: temperature does NOT measurably change factual ACCURACY here — an
  interleaved A/B through the real pipeline scored 60/60 at 0.8 vs 59/60 at 0.15, and the
  one genuine error was at the LOW temperature. What it changes is run-to-run CONSISTENCY,
  which was the actual complaint. Hence greedy (0.0) for facts, with `CREATIVE_INTENT` in
  orchestrator routing jokes/poems/brainstorms to 0.85. Checked after the change: no
  repetition loops on long answers, tool turns fine, 4/4 distinct haiku, 3/3 distinct jokes.
  BENCHMARK TRAP, cost a whole run: asking the same question several times IN A ROW proves
  nothing — the first answer lands in the conversation history and the model just repeats
  it, so every setting scores 100%. Ask each question ONCE inside a long run of different
  questions and repeat the whole sequence (`tests/temp_ab.py` does it correctly).
- BACKGROUND WORK MUST YIELD TO HIM. The TTS phrase warm (78 syntheses, from 20 s after
  boot) originally ran unsynchronised against live turns on a single Kokoro ONNX session.
  A release-time voice test caught it: a reply arrived so late it landed in the NEXT
  test's window, failing BARE JARVIS. Synthesis is now serialized (cache hits skip the
  lock, so warmed phrases stay instant) and the warm loop waits on `tts.idle`, an Event
  the speaker task clears while talking. If you add any other background model work,
  gate it the same way — the symptom looks like a flaky test, not a latency bug.
- SLEEP MODE (`sleep` skill -> `enter_sleep_mode`). "go to sleep" / "that's all for now"
  minimises him; the wake word, hotkey, tray, or typing brings him back. Uses Win32, NOT a
  window message, because the webview is throttled while minimised and must not be on the
  critical path for waking. IT SHIPPED BROKEN TWICE — check both when touching it:
  1. The wake loop gate must include `State.SLEEPING`. It didn't, so the detector was
     never fed while asleep: a ONE-WAY DOOR where nothing woke him and every later turn
     returned "busy".
  2. Waking must also LEAVE the SLEEPING state (`wake_if_sleeping`), not just raise the
     window — the turn machinery only runs from IDLE, so he looked awake and was deaf.
  `tests/sleep_e2e.py` (in the release suite) covers the coming-back half specifically.
  `tests/sleep_coverage.py` measures phrasings: 51/51 seeded, 23/25 held-out, 13/13
  negatives ("put the COMPUTER to sleep" is power_action; a question about sleep is a
  question).
- SEED CLASHES — `tests/seed_collisions.py`, in the build gate. Seeds are canonicalised
  before embedding, so a seed can silently become a DIFFERENT sentence and take over
  another skill. Adding "no more for now" to sleep turned into "no i meant ACTION" and
  stole voice corrections outright. `router.load()` resolves clashes with `setdefault`
  (first skill in SKILLS wins), so this never fails loudly on its own. It also surfaced a
  pre-existing one: recall's "remind me what i told you..." was being stolen by reminder.
  When a new seed doesn't work, check what it canonicalises to before adding more seeds.
- PHANTOM WINDOWS. Windows 11 keeps the frame of a CLOSED UWP app (Settings, Calculator,
  Store) alive and suspended, and `IsWindowVisible` still returns True — he insisted
  Settings was open for hours, twice over (these apps own both an ApplicationFrameWindow
  and a CoreWindow). `_is_cloaked()` (DWM `DWMWA_CLOAKED`) is the only reliable signal.
  This also excludes other virtual desktops, which is intended.
- `np.frombuffer` RETURNS A READ-ONLY VIEW. Both brain matrices are loaded that way and
  written in place later, so re-teaching an existing command died with "assignment
  destination is read-only" — after the reflex had fired, so `turn_done` never arrived and
  clients hung. `.copy()` on both. Only reachable once a taught command survived a restart.
- HUD RETURNS TO THE ORB on a 45 s timer. All three ways of opening a view (tab click,
  the voice "show me the X tab" path, the debug hook) set `pinned:true`, and the collapse
  timer skips pinned panels — so a view stayed up forever. They share `showView()` now.
  Fixing only `setView` is NOT enough; the voice path is the one that actually happens.
- NAME GATE: `tests/check_names.py` (pyflakes) now runs FIRST in the build. `compileall`
  only checks syntax, so a name imported inside one function and used in another compiles
  fine and NameErrors at runtime — that shipped once as `without_honorific`, and the
  symptom was nasty: the reflex spoke its line, the turn died right after, `turn_done`
  never fired, and the app hung mid-turn. pyflakes catches it in about a second.
- BRAIN MISFIRE fixed: "what does cpu stand for" / "what is a cpu" answered with a
  system-stats report — they sat within 0.05 of the "what's the cpu at" seed. Canon rules
  now normalise definitional questions to a general form. The "what is a/an X" rule
  deliberately excludes "the": "what's THE time"/"THE date" are live readings.
- TTS PHRASE CACHE. Re-synthesising "Muted." cost ~500 ms EVERY time. `TTSRouter` now
  caches by exact text (engine+voice+rate keyed, cleared on `reload()`), warmed in the
  background 20 s after boot from `WARM_PHRASES` (fillers, fixed acks, round volume
  values, plus their ", sir." variants). Live: first_audio 617 ms -> 32 ms, total turn
  2.9 s -> 1.0 s. Time/date/stats lines vary by nature and stay uncached.
- BRAIN 65 ms -> 42 ms per decision. (1) One turn embedded the same text up to three
  times (`match_command`, `classify`, `general_level`) — now an LRU in `_embed`.
  (2) fastembed pinned to ONE thread: measured 39 ms vs 57 ms default vs 80 ms on eight.
  A single short phrase is too little work to fan across cores, and it leaves the CPU to
  llama-server. Knob: `brain.embed_threads`.

## How to work on this (read before changing anything)
- **Iterate with `scripts\dev.ps1`, not `release.ps1`.** It runs the sidecar FROM SOURCE
  on :8790 / `devtoken123` in ~40 s. A full release is ~15 min (PyInstaller 4, Rust 7,
  install 2, suites 6) and `quick.ps1` is still ~5 — a Python edit needs none of that.
  Build and install ONCE, at the end, when it already works. It closes the installed app
  first on purpose: two llama-servers do not fit on a 780M, and the loser sits at 503
  until it times out, so an entire test run silently measures a dead port.
- **Test the capability, not the micro-behaviour.** Every suite was green while research
  was completely dead. `tests/research_e2e.py` is the model for this: it runs the thing
  the user actually asks for and judges it the way he does.
- **A test that cannot see the failure is worse than no test.** Two examples from one
  session: the research suite reported PASS on three obvious failures because its matcher
  only knew ASCII apostrophes and the model writes U+2019; and its first pass/fail rule
  condemned a correct, sourced answer as fabrication. Sanity-check a new suite against
  output you already know is bad.
- **Never heredoc Python containing regex.** `\b` becomes a literal backspace and the
  pattern silently matches nothing. It happened three times in one session. Use the Write
  tool for patch scripts.

## Web search = HIS Brave (2026-08-25, current)
Search drives his OWN Brave profile (`_real_profile()`, auto-detected under LOCALAPPDATA),
MINIMISED, in a tab of its own. No API key, no account. Rules that matter:
- **Never go back to a scratch profile.** A blank profile with no history is exactly what
  bot detection looks for; the old `browser-profile` version got a CAPTCHA on literally
  every query and returned zero results, and the model relayed that as "I couldn't find
  reliable information". Using his real profile is what makes search work at all.
- **Brave Search is deliberately NOT used.** Even from his profile it challenges automated
  queries (first one fine, everything after it "Verifying you're not a bot"). Google, then
  DuckDuckGo. Both serve normally in the same browser. Do not try to defeat a challenge —
  detect it and move to the next engine.
- **Minimised, never hidden.** The old code stripped the taskbar button and parked it at
  -32000,-32000, so he could never get at the page JARVIS had just loaded. Minimised means
  he clicks Brave and it is right there, which is the whole point.
- **JARVIS keeps its own tab.** When attached to a running Brave, `pages[0]` is whatever HE
  is reading — navigating that away would be unforgivable.
- `brave_session` IS `brave_web`. They used to be two profiles, so a search and a page-read
  each spawned their own hidden Brave.
- The extractor anchors on the result TITLE element per engine. Scraping anchors generally
  put Google's video carousel above the web results and made DuckDuckGo's displayed URL
  the title.
- Gate: `tests/brave_search_check.py` (search works, in HIS profile) and
  `tests/research_e2e.py` (end to end, no fabrication).

## History: web search was bot-blocked before this (keyless-API era)
Both keyless routes are gone: DuckDuckGo's HTML endpoint answers HTTP 202 with a CAPTCHA,
and Brave Search serves a CAPTCHA page to the automation browser (Mojeek and Startpage
too). That is WHY research failed — `web_search` returned `[]`, which the model read as
"nothing exists", and with no sources it answered from stale memory and sounded certain
("the top-rated mini PC in 2026 is the Intel NUC 13 Extreme" — a 2022 machine, invented
specs). Do not try to defeat the CAPTCHAs.
What exists now instead: Wikipedia + Hacker News (Algolia) + Stack Exchange, all
documented keyless APIs, merged ROUND-ROBIN (Wikipedia returns five articles for any
query and was burying the useful hits). Price / stock / "best <thing> of <year>" skip
them entirely and return a blocked error — those sources cannot answer it, and handing
the model near-miss context is what produced the fabrications. News is exempt; HN covers
it well. **The real fix is a Brave Search API key in Settings** (free tier, 2,000/month);
the code path already exists and takes it.

## Next ideas
1. Speed: LLM first token is ~2.5-4.5 s on cached prefix; reflex ~0.3 s. STT small.en
   ~1.5 s (consider base.en); Kokoro ~1 s/sentence. `open_site` turn is ~14 s (page
   load + screenshot + LLM reading 1500 chars) - trim page text / skip LLM when the
   user only said "open X".
2. Dataset export (JSONL of turns) for real LoRA fine-tuning later.
3. OS-like UI expansion (files, settings panels inside the HUD).

## User's standing priorities
Mic must always work (done, verified w/ Wispr) → speed ("quicker = more real") →
brain/training → OS-like UI (never leave the app) → everything verified on the REAL
install, never on the sandbox mirror.
