# JARVIS — Continuation Handoff (living document)

Read this first after any context reset. Everything below was learned the hard way.
Updated: 2026-08-30.

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
   Helper .cmd files and their output go in **`.agent/`** inside the repo
   (`.agent/scripts`, `.agent/logs`, `.agent/shots`, `.agent/session.txt`) —
   NOT in `C:\Users\nicho\Documents\` itself. That is Nicholas's own folder and
   it accumulated ~25 stray `jarvis_*.cmd/.log/.done` files before this rule
   (cleaned up 2026-08-27; `.agent/` is gitignored, see `.agent/README.md`).
   Anywhere under the profile that is not AppData escapes virtualization.
   Use `start /wait "" "<installer>" /S` in the .cmd (plain invocation cut the log).
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

## WHERE THINGS STAND (2026-08-29 evening) — resume here

Everything below this section is history, newest sections near the bottom. Today:

- **All 17 suites green.** `scripts/suites.ps1` keeps every test's full output in
  `.agent/logs/suite_<name>.log` — read that before theorising about a failure.
- **Markets are live** (Finnhub key in Credential Manager), **voice notes work from
  Telegram**, a vague question is **clarified with both answers already fetching**, and
  **tickers reach the market tools** instead of being answered off a scraped web page.
- **The room-audio guard is OFF and must stay off** until somebody works out why it
  corrupts the heap in the packaged build. Read the top of `audio/output_watch.py`: it
  lists what has already been ruled out, which is most of the obvious things.
- **`tests/soak_e2e.py` is the only test that asks whether the process SURVIVED.** Every
  feature test can stay green while the sidecar crash-loops underneath them — that
  happened all of 2026-08-29. If something is mysteriously flaky, check the Windows
  Application event log for jarvis-sidecar faults before anything else.
- OPEN, needs a Rust build (deliberately not done with the user away): the supervisor
  should re-push secrets it finds missing, because a crash loop can leave the sidecar
  without the Finnhub key while Credential Manager still has it.

## Superseded pointer (2026-08-26 morning)
- The full HUD redesign is BUILT, INSTALLED and pushed (commits a671cdc..8131e6f).
  All release suites green. Design source of truth:
  `Jarvis UI mockup improvements/design_handoff_jarvis_hud/README.md`.
- INCIDENT resolved this morning: the user stopped the session mid-release on 08-25
  ~21:20. The release's DETACHED real-session install task still fired at 21:23:59,
  killed the running (sleeping) jarvis.exe, then died before the NSIS installer ran —
  the app stayed dead all night. The user experienced this as "sleep mode closed
  JARVIS after 5-10 minutes". NOT a sleep-mode bug. Fixed by re-running
  `Documents/jarvis_install.cmd` via schtasks (that installer, 08-25 21:23, includes
  wordmark-click->settings and Escape-dismiss).
  RULE: a stopped release may still have its install task queued — check
  `jarvis_install.log`: a log that ends after the taskkill lines (no "installer exit",
  no "DONE") means the app was killed and never reinstalled. Never assume a stopped
  release left the app alive; re-run the install cmd.
- NEXT (user's stated plan): 1) finish walking him through how the new UI works,
  2) review what overnight sleep produced — proactive quiet hours default 22:00-08:00
  suppress alerts entirely; watchdogs/self-heal still log (check Settings->History and
  the activity), 3) THE BIG ONE: plan "the proper functionality of JARVIS" — the
  feature roadmap, together. Always state your understanding before starting work.

## 2026-08-26 (user away 12 h): every UI promise now has a working backend
The instruction was "make sure the UI elements we added all work". The method that
found everything: read every hint the UI PRINTS ON SCREEN and speak it at the app —
each one is a contract. Findings (commit 6163105), all fixed + gate-protected:
- "show settings" FABRICATED a settings readout (ui skill regex required a
  tab/panel suffix, so the LLM invented "volume 70%, shortcut Win+J"). slots_ui now
  takes bare sections, "settings, history", history/about views.
- "wake up" MATCHED THE SLEEP SKILL (embeds near sleep seeds) — he answered a wake
  request by going back to sleep. Guard in slots_sleep, PLUS a "wakeack" reflex
  ("wake up"/"good morning jarvis" -> "At your service.") because the LLM fallback
  answered a wake with a history echo ("Spiders have eight legs."). test_brain pins.
- "keep it" pinned the WRONG STAGE: the ui event lands after this turn's transcript
  already swapped the stage to fresh prose. Fix: store snapshots the outgoing stage
  (with its web/images/files data) on every swap/dismiss; pin/focus/restore operate
  on the snapshot when the current stage is an answerless prose. Same snapshot powers
  "bring that back" (restore) and "keep it for ten minutes" (pinUntil -> drain).
- "bigger" / "the second one" / "back to the grid" now exist: ui focus action ->
  images.focus -> featured-image layout (.images__focus).
- Folder stage's "say a file name to open it" was a lie — "open jarvis install log"
  hit open_app and failed. open_application now falls back to file_tools.open_by_name
  (token match, spaces==underscores==dots) -> preview_file stage. Hint reworded to
  'SAY "OPEN" AND A FILE NAME'. Folder/file stages hold 30 s (invite a follow-up),
  everything else 5 s (holdFor()).
- /transcript ignored ?limit -> History pane could never show >30 turns.
- Stale source chips from the previous search decorated unrelated prose answers ->
  transcript event clears web/images (unless pinned). Prose stage now opens AT the
  transcript (question at 40px, mock 03), not at first token.
- "open settings" stays open_app (Windows Settings; canon "open APP" collides) —
  "show settings" = JARVIS's stage. Deliberate split: show = stage, open = app.
- NEW GATE `tests/hud_e2e.py PORT TOKEN`: serves dist/ itself, Playwright+Brave
  headless, drives the store contract via window.__jarvis (exposed in store.ts) —
  19 checks; wired into release.ps1. test_brain now 53 cases (all voice hooks).
- Verified live on the real sidecar: gate wedges (DO IT round-trip), 4 fault wedges
  with real telemetry, sleep->ASLEEP->wake, folder/images/browser/prose stages.
- Testing artifact to know: rAF batching means assistant deltas don't flush while
  the window is HIDDEN (background tab) — streaming is fine when visible.
- QUERY CLEANING (user report: "show me iron man" searched verbatim): shared
  `tools/query_clean.py` strips command phrasing to keywords in TWO layers —
  slots_images/the tools themselves (web_search, show_images, research), because
  the LLM path passes whatever the model wrote. Spoken counts work ("5 images of
  spiderman" -> count 5). Conservative on ambiguity: "research methods in
  psychology" keeps its noun (bare search/research only strip before a
  determiner); questions pass through. research() cleans AT ENTRY so its events
  and web events carry the same query string (the store keys the browser stage
  by query). 15 unit guards in test_audit_fixes; routing seeds for bare forms
  ("show me iron man") — NOT "show me spiderman pictures", trailing "pictures"
  canonicalizes into the Pictures-FOLDER pattern.
- brain_e2e is order-sensitive: run it against a QUIET app — starting it while a
  turn is still speaking shifts every expectation off by one (looks like 0/8).
- ROUTE SWEEP (60 realistic utterances vs /brain/classify; script pattern in git
  history) found 7 misroutes, all guarded + gate-pinned: "minimize everything" and
  "be quieter" SLEPT him, "go to sleep in an hour" slept NOW, "wake me up at 7"
  got "At your service.", "put on some music" launched an app called "some music",
  "switch to a british voice" hunted a window by that title, "show me pictures
  from my trip" web-searched his personal photos. Technique worth repeating after
  any seed/skill change.
- AUTOSTART: enabled 2026-08-26 (HKCU\...\Run "JARVIS", written via real-session
  schtasks cmd — agent-shell registry writes may be virtualized). Before this a
  reboot left JARVIS dead (the 10:27 restart did exactly that; Event 1074 =
  Start-menu restart, and the WER "BlueScreen" entries that morning were OLD
  queued reports flushed at boot — check builds/timestamps before panicking).
  The Settings toggle reads the same Run value, so it shows ON and can turn it off.

## THE FACT STORE (2026-08-26 afternoon) — brain roadmap stages 1-2 shipped
Design: docs/BRAIN_ROADMAP.md (read it first). Implementation notes:
- `brain/facts.py`: FactStore singleton `facts` + module fn `record_evidence`
  (tools call it). Serve threshold 0.90 (stricter than routing 0.82). Evidence
  is INSTANCE state — calling module fns as methods was an AttributeError that
  compileall/pyflakes CANNOT catch; only facts_e2e caught it. Trust the e2e.
- Flow: web_search/research record evidence -> _converse end schedules
  _fact_intake (3 s later, background) -> REALM2 triggers on question AND
  answer -> temp-0 timeless classify (max_tokens 600: gpt-oss REASONS first;
  at 180 the YES truncated away and everything read as NO) -> store with
  sources. Read: _converse tries facts.lookup BEFORE the LLM (after reflexes;
  llm_after reflexes skip it) -> _fact_turn speaks polished answer ~0.3 s.
- The classifier is CONSERVATIVE BY DESIGN and that is correct: it rejected
  "how tall is mount kilimanjaro" because surveys revise mountain heights.
  Use completed history for tests. Stored questions are query_clean'd so
  paraphrases land close in embedding space.
- "how do you know that" -> provenance reflex speaks facts.last_served's
  source + verified date. He NEVER volunteers sources (user rule).
- /facts (list+stats), DELETE /facts/{id}, /turnstats (turn-path shares —
  roadmap stage 1; memory.log_turn_stat at both turn_done sites).
- e2e gotchas that burned an hour: (a) after a hotswap WAIT FOR THE AI ENGINE
  diagnostics check, idle is not enough — turns hit the llm-loading branch;
  (b) transcript row-count deltas break at the endpoint's cap (limit param!) —
  match your own user row + following assistant row; (c) a pending REMINDER
  can fire mid-suite and pollute the transcript (the 90-min stretch reminder
  from brain_e2e did exactly that); (d) /text while he is SPEAKING drops the
  turn silently — suites must wait for idle between turns.
- KNOWN GAP (app-level, unfixed): /text during speech should queue or barge-in,
  not vanish — a typed message mid-speech is currently lost. Candidate for the
  next UX pass.

## NIGHT SCHOOL (2026-08-26 evening) — the brain roadmap is fully built
`brain/night_school.py` — singleton `night_school`, started in main's lifespan.
- Conditions: state SLEEPING + proactive quiet hours + ≥72 h since last run
  (night_meta table in the facts DB). Checks every 30 min. Aborts between items
  the moment he wakes. Config kill-switch: facts.night_school.
- Job 1 audit: original-source re-fetch -> temp-0 SAME/CHANGED/UNCLEAR.
  UNCLEAR strikes; 2 strikes demote; CHANGED demotes instantly.
- Job 2 curiosity: recent transcript questions (what/who/when/how...) that are
  realm-1-eligible, unrouted, and unknown -> research -> SYNTHESIZE one spoken
  sentence at temp 0 ("UNKNOWN" -> skip) -> normal facts.consider gates. First
  pass stored RAW PAGE EXTRACTS as answers — a stored fact must be a sentence
  he can SAY; the synthesis step is not optional.
- Job 3 distillation: paraphrases via LLM -> brain.learn (its safety bar:
  slots-executable, general-guard, dedupe, 14-word cap, seeds override).
- Verify with POST /debug/night_school (forces a full pass, returns the
  report); GET /night_school for the last report. Audit verdict machine is
  offline-gated in test_facts.py (fetch/compare injectable).
- SMALL-MODEL TIER: measured and REJECTED — gpt-oss-20b is MoE (~3.6B active,
  27 t/s gen); a dense gemma-3-4b does 22 t/s GPU / 14 CPU beside it. The
  bakeoff (scratchpad script, pattern: spin second llama-server on 8035 with
  timings) falsified the "4-5x" assumption. Do NOT ship a dense-small tier on
  this box; revisit only with a faster small MoE.

## FEATURE GAPS the route sweep surfaced (for the planning session)
Things users will say that today fall to the LLM without a real tool behind them:
- Relative volume: "turn it up / down a bit" (volume_set is absolute-only).
- Voice control: "speak slower", "change your voice", "switch to a british voice",
  "be quieter" (voice/rate/volume config exists but has no voice-command surface).
- Reminders by voice: "what reminders do i have", "cancel my reminders" (the tools
  to LIST/CANCEL don't exist; TasksView shows them but voice can't).
- "Say that again" (re-speak the last line), "take a note / write that down".
- Now-playing: "what song is this" (no media metadata source).
- Local photo/file realm: "show me pictures from my trip", "where are my
  screenshots" (screenshots folder exists; no local-image browse/search stage).
- Site-scoped search: "search youtube for lofi beats" (query passes through as
  plain keywords today).
- "Read my last email", "what time is my meeting" (no mail/calendar integration).
- Media backend proper (play actual music, now-playing stage §6.7), table stage
  (§6.8), split/two-tasks, apps/window-layout stage — designed but unbuilt.

## HOW THE NEW UI WORKS (for explaining to the user)
- Rest = radial: reactor centred, room faded. Speak, or Ctrl+Shift+J, or click the orb.
- A turn that produces something to read = anchor: core slides left (.9s), the STAGE
  opens beside it. The renderer is chosen by what the turn does: answer -> prose ·
  search/research -> browser · pictures -> images · file -> file · folder -> folder ·
  settings/history/memory/tasks/about -> settings rail.
- After the spoken answer: 5 s hold (drain bar; folder/file 30 s), then back to
  radial. Escape or "hide everything" dismisses. Wordmark click opens Settings.
- Voice surface phrases (ui skill): "show settings" / "settings, history" / "show my
  memory" / "show tasks" / "keep it" / "keep it for ten minutes" / "bring that back"
  / "hide everything" / on images: "bigger", "the second one", "back to the grid"
  / in a folder: "open" + a file name.
- Gate: room amber, radial, DO IT / NO or spoken yes/no, never times out.
  Faults: room red, four wedges, RESTART IT is real (/repair).
- Media/table/split/apps stages: renderers not built — no backend data yet (no music
  player, no comparison engine, no task concurrency). These are features to plan.
- Embedded-Brave-in-stage (design §6.4) deliberately deferred — the one open design
  question; the browser stage renders JARVIS's real search/read events meanwhile.

## THE HUD (2026-08-25): arc reactor, two geometries, one stage
The UI is the design in `Jarvis UI mockup improvements/design_handoff_jarvis_hud/` —
read its README before touching `src/`. The reasons behind the layout matter; several
obvious "improvements" were tried in design and explicitly rejected (bright nucleus disc,
read-only browser, bottom status strips, per-breakpoint layouts).
- `ArcReactor.tsx` = the core; `App.tsx` = geometry + atmosphere + chrome; `Stage.tsx` =
  every content surface; `Wedges.tsx` = faults + the gate. There are NO view tabs and no
  status bar — the utterance selects the stage renderer via store events.
- `--rim` (one variable per state) drives the whole room. `--s` drives ALL dimensions
  (calc(Npx * var(--s))): chrome/wedges re-anchor to the real viewport, stage right edge
  follows the screen.
- Dismiss: stages hold 5 s after the answer (drain bar), voice "keep it"/pin holds.
- UI iteration: `vite build` (~0.6 s) + `node scripts/serve_dist.cjs`, page at
  `http://127.0.0.1:5173/?port=8790&token=devtoken123` against `scripts\dev.ps1`.
  Vite's DEV server silently wedges on this machine (accepts, never responds) — don't
  use it. Port 1420 is silently firewalled — don't use it either. Screenshot with
  Playwright headless (sidecar venv) for pixel checks against `screens/*.png`.
- Flagged deviations (handoff §17): embedded Brave in the stage not built yet (browser
  stage renders JARVIS's real browsing from events); media/table/split/apps stages have
  no backend data; the gate's scoped-permission button needs backend support first.

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

## Panels, not tabs
Files, media, browser, research and web all surface themselves when JARVIS uses them, take
the SAME main slot with the orb on the left, and fade back to the orb when the turn ends.
The tab strip is no longer how you get anywhere — it appears only on a deliberate reach
for the top edge, or "show me the tabs". `showView()` must NOT set `navVisible`.
Two things that made them feel inconsistent, both fixed:
- `web` events set `rightPanel` but never `view`, so a search while the files panel was up
  left the files in the centre and squeezed the results into a side strip. Web now claims
  the main slot like the others (a `research` run keeps its own view).
- `show_images` announces progress as a `web` stage, which parked an empty WEB panel next
  to the pictures once they arrived. The `images` event now clears it.
Verify visually with `/debug/hud.png` — one turn at a time. Firing several turns back to
back races them and the screenshot shows the wrong panel, which looks like a bug and isn't.

## JARVIS's browsing is INVISIBLE; what he asks for is VISIBLE
Two opposite requirements sharing one browser, and both are easy to break:
- **Research/search/fetch**: no window, ever. It runs in the background and he reads it in
  the JARVIS panel. Chromium ignores STARTUPINFO show flags and re-shows its window on
  every tab creation and navigation, so hiding once at launch does NOT hold — spawn with
  `--window-position=-32000,-32000` on the command line (no visible frame at all) and
  re-hide after `_tab()` and after every `goto`.
- **`open_url` ("open YouTube")**: must land in a window he can SEE. Chromium is
  single-instance per profile, so `os.startfile` hands the URL to the hidden instance and
  the tab opens inside the hidden window — it looks like nothing happened. Use an explicit
  `--new-window` with a position, then foreground it.
- Telling the two apart cannot use the pid — same process. `_HIDDEN_HWNDS` tracks which
  windows JARVIS hid; `hidden_hwnds()` is how open_url finds "the one I opened for him".
- `_we_spawned` gates ALL window manipulation. If HE opened Brave, JARVIS touches nothing
  and its work is just a background tab in his window.
Guarded end to end by `tests/brave_search_check.py`.

## NEVER put JARVIS in his Brave profile (learned the hard way, 2026-08-25)
The CAPTCHA that made research useless was **Brave Search**, not automation and not the
throwaway profile. From a brand new profile: Brave Search challenges, Google challenges,
**DuckDuckGo serves normally** and returns ten good results. Changing the ENGINE was the
whole fix. Moving JARVIS into his profile was never needed and broke his browser badly:
sharing his profile means sharing his windows, and Chromium hands the last window's state
to the next one it opens. Every concealment leaked into HIS windows —
- off-screen wrote `left:-1240` into his profile's saved `window_placement`, so his own
  Brave opened off-screen and clicking the taskbar icon did nothing (repaired; backup at
  `Default/Preferences.jarvis-backup`)
- minimised made his next window open minimised
- hiding the HWND still leaked the state
…and he could not use his own browser while JARVIS was open.
So: JARVIS runs an ISOLATED Brave — own profile under APP_DIR, own process, off-screen and
off the taskbar, DuckDuckGo as the engine. No CDP attach (it could latch onto his). It
cannot reach, drive or reconfigure his browser. `open_url` still opens HIS Brave for
things he asks to use.
Guarded by `tests/brave_search_check.py`, which asserts JARVIS is NOT in his profile and
that clicking Brave gives him a usable window.

## Superseded: HIS browser, not ours
JARVIS **attaches** to Brave, it never owns it. It spawns Brave detached (minimised,
`--remote-debugging-port`) and connects over CDP, so "JARVIS started it" and "he started
it" are the same path and letting go of the driver never touches the browser. Do not go
back to `launch_persistent_context` on his profile: Playwright kills what it launched, so
his Brave died with the sidecar, tabs and all.
Chromium is single-instance per profile (verified): once JARVIS has started Brave, him
opening it from his own shortcut joins the SAME process — no second browser, debug port
still live, and JARVIS can open its own tab and search while he is browsing.
Anything that touches the window must ask `self._own_browser` first. Three things were
tolerable against a throwaway profile and are unforgivable against his:
- the idle reaper called `close()` after 15 min → now closes only JARVIS's own tab
- shutdown closed the whole context → now only our tab
- `warmup()` called `bring_to_front()` → his browser jumped in front of him at boot
- the image path flung the window to -32000 and stripped its taskbar button
Also: JARVIS keeps its OWN tab (`_tab()`). When attached, `pages[0]` is whatever he is
reading.

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

## 2026-08-28: "did you test everything?" — no, and what testing then found

Four things had been reasoned about and gated offline but never once exercised on the
real app. Running them for the first time failed immediately and turned up five genuine
bugs. New gates: `tests/endpoint_e2e.py`, `tests/hands_e2e.py` (both in `suites.ps1`).

1. **Every turn transcribed its audio TWICE.** The semantic endpoint check runs Parakeet
   over the utterance to judge whether the sentence is finished; the turn then ran it
   again over the same audio. ~1.5 s of dead air on every single voice turn. The capture
   loop now keeps that transcript (with the speech-frame count it was taken at, so it is
   only reused when nothing more was said) and hands it to the turn. `stt_ms` on the
   transcript event is now 0 where it was ~1500.
2. **Endpointing looked like it worked and did not.** Its decision was correct and logged,
   but nothing downstream got faster, because of (1). The transcript event now carries
   `silence_ms` / `budget_ms` — what he actually waited and what he was waiting for — and
   the e2e asserts on those, not on wall-clock (injected audio is paced with
   `asyncio.sleep`, and Windows' ~16 ms timer granularity drifts hundreds of ms over a
   clip; measuring from the test process reads ~2.4 s for both cases and proves nothing).
   Measured on the install: finished sentence cut at 0.65 s, dangling one held 1.96 s.
3. **A half-spoken command was being cut off BECAUSE the brain understood it.** "Remind me
   to" matches the reminder skill perfectly, and a brain hit outranked every other cue, so
   the most common way to trail off got the FAST budget. Trailing-off now outranks the
   brain. Related: Parakeet punctuates every clip from grammar alone, finished or not
   ("Remind me to.", "What's the weather in?"), so a trailing . ? ! after a function word
   is not evidence of anything and is ignored. Costs ~1.5 s on questions that legitimately
   strand a preposition ("who's it by?") — the right trade, since waiting is a pause and
   cutting someone off makes them say it all again.
4. **Dictation and the capture loop drank from the SAME mic queue.** Say his name while
   dictating and the wake word fires, the capture loop starts pulling blocks, and the two
   of them split the audio — each getting half a sentence. Dictation now takes its own
   `mic.subscribe()`, and the wake word is muted while dictating (his name may well be in
   the text being written).
5. **`click_control` could hang forever.** UIA `Invoke` is a call INTO the other app, and
   an app that is busy or showing a modal dialog simply never answers it — no exception,
   no return. The tool sat there until its 30 s timeout and the click never happened at
   all. Now bounded (`INVOKE_TIMEOUT_S = 2.5`) with a real mouse click as fallback.
   Also: menus and dialogs are their own top-level windows, so the walk now includes
   same-process windows **of popup/dialog class only** — an app's other ordinary windows
   must stay out, or a click by name can land in a different window.

Proven end-to-end on the install, not argued: dictation records -> transcribes -> pastes
into another app (Windows names the Notepad tab after the pasted text, which is the
receipt), and `click_control` clicks a named control with a visible consequence.
`/debug/tool` (JARVIS_DEBUG only) runs one tool with exact arguments through the real risk
gate, so a test can prove a click really clicks instead of testing the model's phrasing.

STILL UNTESTED: market tools (`get_stock_quote`, `get_analyst_view`, `get_company_news`,
`get_market_movers`) — they need the Finnhub key in Settings -> Markets. Everything else
about them is gated; the live call is not.

Known and NOT fixed: the title-bar Close/Minimize/Maximize buttons do not appear in the
UIA tree for WinUI apps, so "click Close" cannot work — `close_application` is the path
for that. Win11 Notepad's own unsaved-changes dialog stops responding to both UIA Invoke
and real mouse clicks at the correct coordinates (its process still reports Responding);
this looks like a Notepad quirk, not ours, and the test avoids it.

## 2026-08-29: markets are live, and a vague question now costs nothing

**Finnhub key is in.** Windows Credential Manager, target `finnhub_api_key.JARVIS`
(keyring 3 builds the target as `{user}.{service}` — confirmed in the crate source, not
guessed). The Rust core pushes it to the sidecar at startup, so it survives restarts. All
four market tools verified live against real data, and by voice: "what's apple trading
at" -> "Apple Inc is at 319.7 dollars, up 5.12 or 1.63 percent today."

**Speculative clarification (his idea, and a good one).** "Any news on Tesla" splits two
ways — the company or the stock — and guessing wastes a whole turn. He now ASKS... and
starts fetching BOTH readings while the question is being spoken. When the answer comes
back the winner is already warm and the losers are cancelled:

| | first word | tools |
|---|---|---|
| cold "what's tesla trading at" | 1.45 s | get_stock_quote |
| vague -> he asks | 0.07 s | both branches start |
| "the stock" -> answer | 0.06 s | NONE (already fetched) |

`sidecar/clarify.py` holds the engine; the list of ambiguities it knows is deliberately
short. Do not replace it with "ask the LLM how many readings this has" — that costs the
seconds the whole thing exists to save.

RULES, enforced rather than assumed (see `tests/test_clarify.py`):
- **only read-only lookups may run on speculation.** `validate()` refuses any branch whose
  tool requires confirmation, and refuses an UNKNOWN tool rather than assuming it is safe.
  Nothing that sends, buys, opens or deletes may ever run on a guess about what he meant.
- at most 3 branches, so a vague question cannot fan out into a stampede.
- cancelled on: an answer, "never mind", a change of subject, sleep, or a stale question
  (75 s). A sentence that matches BOTH readings equally is not an answer — it is a new
  request, and the speculation is dropped.
- it must not fire when the request already says which half it means: "how's tesla stock
  doing", "any news on the tesla recall" are answered straight. Being asked a question you
  already answered is worse than a wrong guess.

**get_news now SEARCHES for a named subject** (Google News search RSS, keyless). It used
to sieve ~20 general feeds for the keyword, which meant "news on Tesla" reliably returned
NOTHING — general feeds carry world news and rarely name a company in the last few hours.
This fixed the clarify branch and the tool: "news about the election" now returns stories
from minutes ago. Finnhub's company news was the other candidate and is investor
commentary ("Most active S&P500 stocks in Friday's session") — not what anyone means by
news about a company.

Gates: `tests/test_clarify.py` (49 checks) in the build; `tests/clarify_e2e.py` in
`suites.ps1` — its load-bearing check is that the CHOSEN branch makes no tool call, which
is the whole feature.

## 2026-08-29 (later): the remote path, and two bugs only Telegram could show

Everything built this week was verified at the PC. Nothing had been run over Telegram,
which is how he actually uses JARVIS when he is away. Two real bugs came out of testing it:

1. **A question asked from the phone opened the MICROPHONE at the PC.** `_ask_clarification`
   armed the conversation window unconditionally, so after he asked something from his
   phone, anything said near the machine was treated as his reply to a question he had
   asked from somewhere else entirely. It only arms when `remote_turn` is false now.
2. **A whole sentence could be swallowed as an answer to a pending question.** "What's the
   stock market doing" contains "stock" and "market", both words for the stock branch — so
   with a question open about Tesla it answered with Tesla's price. An answer to "the
   company or the stock?" is two or three words; `MAX_ANSWER_WORDS = 4`, and anything
   longer is what it plainly is: a new request.

And one improvement the phone deserves: **a two-way question is now two BUTTONS**, not a
typing exercise (`clarify:<label>` callbacks, same mechanism as the DO IT / NO
confirmation gate). Tapping one feeds the label back as the next thing he said, and the
answer for it is already fetched. The plain reply text is suppressed when buttons carried
the question, so it does not arrive twice.

`/debug/telegram` (JARVIS_DEBUG only) hands the bridge an update as though it arrived from
his phone. Only the INBOUND half is simulated — there is no way for the bot to receive a
message he did not send — everything it sends back is a real message to the real chat.

`tests/telegram_e2e.py` is in `suites.ps1` but **opt-in**: without `JARVIS_TELEGRAM_E2E=1`
it prints SKIPPED and passes. A test suite has no business putting notifications on his
phone unasked. Run it deliberately after touching the bridge, clarify, or the market
tools. Verified 9/9 that way: message -> turn, vague question -> buttons with both
branches fetching, tap -> answered with NO second fetch, mic NOT armed, markets from the
phone.

Note when reading its output: `answerCallbackQuery: query is too old` in the log during a
run is the TEST's synthetic callback id, not a fault — a real tap carries a valid one.

## 2026-08-29 (evening): the market gate, and what it cost to make it green

`tests/market_e2e.py` is in `suites.ps1`. It checks the numbers against THEMSELVES —
price minus previous close must equal the reported change, and the percentage must match
that change — because "the call came back" passes just as happily with the fields
shuffled. It self-skips with a clear line if the Finnhub key is ever removed.

It found two real bugs on its FIRST run, and fixing them broke a third thing:

1. **A bare ticker had no name**, so he read the letters out: "A A P L is at 319 dollars".
   `_resolve_symbol` short-circuited on anything ticker-shaped and returned it as its own
   name. It resolves and CACHES now (a company's name for its ticker does not change),
   and de-shouts Finnhub's "APPLE INC" to "Apple Inc".
2. **Tickers never reached the market tools at all.** "What's AAPL trading at" missed the
   brain, fell to the LLM, and was answered off a SCRAPED WEB PAGE — a different price
   from Finnhub's, in 25.9 s. NVDA happened to route; AAPL and TSLA did not. Seeds do not
   fix this and I tried: the embedder sees NVDA, AAPL and PLTR as three unrelated rare
   tokens, so seeding one teaches it nothing about the next (0.47-0.78, all under the
   threshold). The fix belongs in canonicalisation, which is what that layer is for — a
   ticker is an object, and objects become placeholders before embedding.
   `_ticker_to_company` runs BEFORE `_light` lowercases, because capitals are the whole
   signal: without them "how is mom doing" is the same shape as "how is AMD doing".
   Unseeded tickers now route at 0.99-1.00. Live: 25.9 s -> 1.5 s, and from the right source.
3. ...and that rule then ate a graphics card: "the current price of an **RTX 5090**"
   became "an apple 5090" and a research question went to the stock tool. Two guards,
   both principled rather than a blocklist: a ticker takes no ARTICLE ("an AAPL" is not
   a thing) and is not followed by a MODEL NUMBER; and an explicit "look up / search /
   research" is never a request for a quote. Pinned in `test_brain.py` (111/111).

**The research suite was calling a correct answer a shrug.** `len(reply) >= 40` — and
"The RTX 5090 is listed at about $6,810." is 39 characters. A sourced, correct, concise
answer failed for one character, and concise is the house style: the gate was penalising
the behaviour we want. A reply carrying a concrete figure now counts however short it is.
Three consecutive 5/5 runs after.

Also changed while chasing that (it stands on its own): after a search the model was told
"no more tools this turn", so when the snippets carried no price its only exits were to
invent a figure or shrug. When the question asks what something COSTS and no result
contains money, it may now open the most promising page and read it. Narrow by design —
every other search keeps the fast path.

**AMBIENT AUDIO, worth telling him:** during a suite run the mic picked up film dialogue
playing near the PC ("Could you show Dr. Banner into his laboratory", "Protox. Very hard
to get hold of") and he acted on it — a real web search for "Protos shield" — and it
killed a clarifying question that was open at the time. The wake word is not the hole
here; the CONVERSATION WINDOW is: after any turn, plain speech is accepted with no wake
word, and a television walks straight through it. Not changed unilaterally — he tuned the
wake threshold himself last time and this is the same kind of call. Options when he wants
them: shorten the window, require the wake word while audio is playing, or gate it on
speaker output being active.

A clarifying question now SURVIVES an interruption for the same reason: something said in
the room is answered as the fresh request it is, but must not throw away the answer he is
still about to give.

## 2026-08-29 (evening, user away 9h): the television, and voice notes

**A television can no longer talk to him.** The wake word was never the hole — the
CONVERSATION WINDOW is: after any turn, plain speech opens a turn with no wake word, and
film dialogue walked straight through it (he ran a real web search on "Protos shield").
While another application is producing sound that window closes and his name is required
again. Nothing else changes: his name still works over the noise, and a quiet room is
exactly as it was. `audio/output_watch.py`, config `wake.ignore_while_audio_plays`.

It took three goes, and the first two LOOKED like they worked:
1. It never fired on real audio, only on the test double. The scan runs on an asyncio
   worker thread and **COM is per-thread** — without CoInitialize every call failed and
   the check answered "nothing is playing" forever, which is indistinguishable from
   working. (tools/uia.py has always opened with CoInitialize for this reason.)
2. With that fixed, the FIRST call worked and every one after it failed with "Cannot find
   window class" — a single-threaded apartment on a pool thread with no message pump.
   Fixed with `CoInitializeEx(COINIT_MULTITHREADED)`.

Both failures were silent, so two things now make them impossible to miss:
- a `wake_suppressed` EVENT whenever speech is ignored, with the app that caused it — a
  thing that ignores you must be able to say why, and the gate asserts on the event
  rather than on an absent log line (absence proved nothing, and cost an hour).
- a permanent **Room Audio** line in /diagnostics: "nothing else is playing" /
  "powershell.exe is playing - his name is required" / a warn if it cannot read at all.
  Verified in the PACKAGED app against real sound, which is the only test that counts —
  the venv having pycaw says nothing about the bundle.

Gates: `test_output_watch.py` (includes the thread-pool COM regression) and
`wake_guard_e2e.py` in suites. Note the wake guard e2e uses `/debug/audio_playing` rather
than real sound: an end-to-end test with actual audio kept landing on the 8-second
conversation-window boundary and was flaky for TIMING reasons, proving nothing either
way. The two halves are proven separately instead — detector against real sound in the
bundle, wiring by the gate.

**Voice notes from Telegram work.** The bridge used to answer "voice notes are coming in
the next update" — a promise the product made and did not keep, in the one workflow he
uses when he is away. `audio/decode.py` turns OGG/Opus (or anything else PyAV reads) into
the 16 kHz mono float32 the recogniser wants; PyAV rather than shelling out to ffmpeg,
which is on PATH here and would not be on an installed copy. What he said is echoed back
in quotes before it is acted on — a misheard word is otherwise invisible when he is not
in the room. Junk, silence, truncated files and anything over 20 MB are refused with a
sentence rather than fed to the recogniser.

Gates: `test_voice_note.py` decodes a real Opus fixture (`tests/fixtures/voice_note.oga`)
and checks the DURATION, because a wrong sample rate is the silent failure here — it
decodes happily and transcribes as nonsense. The live half is in `telegram_e2e.py`, which
uses `/debug/telegram_send_voice` to put a clip into the chat and hand its real file_id
back as though he had recorded it; everything after that is Telegram's own download, the
decoder and Parakeet.

Also: clarify now reads TWO headlines, not three. Three was about twenty seconds of
talking at him.

## 2026-08-29 (evening): the room-audio guard is OFF, and why

**Read audio/output_watch.py before touching this.** The guard works — a television
really does stop being able to talk to him — but with it enabled the packaged sidecar
corrupts its own heap and dies. It ships OFF (`wake.ignore_while_audio_plays`, default
False) and `wake_guard_e2e` self-skips unless `JARVIS_WAKE_GUARD=1`.

One real bug was found and fixed on the way, and it was mine: a `cast()` wrapped around a
`QueryInterface` result. QueryInterface already returns a typed, reference-counted
pointer; casting it makes a second pointer owning no reference, the interface is freed
underneath it, and the next call reads freed memory. Nine `_ctypes.pyd` access violations
in one afternoon, each a silent forty-second restart, nothing in the log because there is
nothing to log. (The cast IS correct on the raw pointer `Activate()` returns — which is
where the pycaw examples put it, and why it looked right.)

Fixing it was not enough: the minimal one-interface rewrite crashes too. RULED OUT, so
nobody repeats the work: the session enumeration; the shared thread pool; PortAudio churn
on the same device (300 stream open/close cycles while metering, clean); both COM
apartments at once (578,000 scans against concurrent UI Automation, clean); 4,000
back-to-back scans (clean). It has NEVER been reproduced outside the PyInstaller bundle,
which is the one remaining difference and the place to look.

**A second bug from the same feature, worth remembering on its own:** COM apartments are
per-thread and cannot be changed once set. The audio watcher needs MULTITHREADED; the UI
Automation behind "click the Send button" needs the default. Both were on asyncio's shared
pool, so whichever landed second failed — "couldn't read that window's controls",
intermittently, by which thread it drew. Every remote click would have started failing at
random. It has its own thread now, and `test_output_watch.py` gates both halves.

**tests/soak_e2e.py is new and is the only test that asks whether he SURVIVED.** Every
feature test stayed green all afternoon while the process was crash-looping underneath
them — a dead sidecar restarts in forty seconds and answers the next question perfectly.
It compares the sidecar's pid at the end against the start; a crash is otherwise invisible.
PACE MATTERS: the first version hammered at ~60x anything a person does, and half of what
it reported were the SUPERVISOR restarting a merely-busy sidecar (no crash event in the
Windows log — that is how you tell the two apart). A test that can only fail by being
unfair teaches you to ignore it. Under realistic load he is stable: 30 turns and 4
diagnostics over 4 minutes, same process, zero errors.

**Secrets can be lost across a sidecar restart.** The Rust core owns Credential Manager and
pushes secrets in at startup — but the supervisor gives up after three restarts in ten
minutes, and today's crash loop exhausted that. The sidecar then behaves exactly like a
man who never had a key: "add a Finnhub API key in Settings", for a key already added.
Two mitigations landed, and one is still open:
  * diagnostics now reports **Markets: ok / no key in this session**, saying explicitly
    that reopening JARVIS restores it — a silent degradation is the worst kind.
  * `GET /secrets` returns the NAMES the session holds (never values) so the core can
    reconcile.
  * STILL TO DO (needs a Rust build, not done while he was away because a failed install
    once left the app dead all night): have the supervisor compare `/secrets` against
    KNOWN_SECRETS on its 20 s tick and re-push anything missing.

When the Windows event log matters: `Get-WinEvent -FilterHashtable @{LogName='Application';
ProviderName='Application Error'}` filtered to jarvis-sidecar names the faulting module.
Three different modules (_ctypes.pyd, ntdll.dll, ucrtbase.dll) with the same fault bucket
is the signature of heap corruption — the crash surfaces wherever the damaged allocation
is next touched, so the module tells you nothing about the culprit.

## 2026-08-30: three bugs in four lines of a real Telegram exchange

    > Remind me every night at 9 pm to wear my retainers please
    < Reminder set for 9:00 PM Sunday.        <- ONE Sunday, not every night
    > Not just Sunday, every night
    < Reminder set for 9:00 PM daily.         <- untrue: it had stored 3:46 PM

1. **"every night" was dropped.** `slots_reminder` read the time and ignored the
   repetition entirely — there was no recurrence extraction in it at all. It now reads
   every night / every day / every weekday / every Monday, strips the schedule words out
   of the reminder TEXT (he was being reminded to "every night wear my retainers"), and
   drops trailing politeness. A named weekday also sets the START date, or "every Monday"
   began on whichever day it was set and read back "every Sunday".
2. **Correcting it left both.** His scheduler held two rows, the one-off and the daily.
   `set_reminder` now REPLACES a pending reminder whose text matches.
3. **He said something untrue, and that is the worst of the three.** The stored row was
   3:46 PM; the model narrated its own intention rather than the result. The confirmation
   sentence is now built inside `set_reminder` from the values actually written, returned
   as `spoken`, and `say_reminder` prefers it. Being told the wrong time is worse than
   being set the wrong time — there is nothing to notice.

**A bare "Jarvis" typed at him answered with the time.** By voice this is handled in the
turn ("he only said the wake word — acknowledge and listen"), but the text path stripped
the name and handed an EMPTY string to the router, and an empty string is nearest to
something. `_converse` now answers "At your service, sir." Gate: `tests/test_reminders.py`,
in the build.

His actual data was repaired by hand: ids 166 and 167 cancelled, one daily 21:00 reminder
in their place.

## 2026-08-30: he now works a shift, and has an opinion

Phases 2 and 3 of `docs/PROACTIVE_PLAN.md` are in. Four modules, four gates, all
wired into `scripts/build_sidecar.cmd`:

    delivery.py       WHERE it goes  - present at the PC, or Telegram
    significance.py   WHETHER to say - four tiers, rules not a model
    briefing.py       WHEN           - 4 briefs a day + a 10-minute watch
    analyst.py        WHAT to make of it - a stance, not a data dump

Build against `/debug/brief` and `/debug/tool {"tool":"market_take"}`. Both compose
the real thing and send NOTHING. Every bug below was found through them rather
than by messaging his phone, which is the only sane way to develop this.

**Things that were true and are worth not rediscovering:**

- His 07:30 brief sat INSIDE the proactive quiet window (which ends 08:00), so it
  would have been suppressed every day. Briefing keeps its own window, 22:00-07:00
  (`briefing.quiet_start/quiet_end`). Two of his own decisions conflicted.
- The watch must PRIME on its first pass, or every restart dumps the whole feed at
  him as breaking news.
- `is_local` matched `" mass."` with a leading space to dodge "mass shooting" -
  which meant a headline STARTING "Mass." was not local. Word boundaries now
  (`REGION_RE`/`TOWN_RE`).
- Finnhub free tier: `/news?category=general` returns 100 items with `related`
  EMPTY on every one. There is no ticker tagging. Names come from headlines.
- Ticker symbols COLLIDE across exchanges, and a US-listing check cannot catch it.
  BDL is Bharat Dynamics in Bombay and Flanigan's Enterprises (a Florida
  restaurant chain) here, and he was told experts were discussing the restaurant.
  Evidence is graded in `analyst.candidates()`: tagged `(NVDA)`, adjacent to
  "Stock", or a known name = trusted at one mention; a bare word in a list needs
  two. Plus: no analyst coverage, not in the take.
- Finnhub returns filing names. `analyst.speakable()` is used EVERYWHERE a company
  is spoken, or the same brief says "Nvidia Corp" and "Nvidia" in one breath.
- The market take must never carry an imperative. Gated in `test_analyst.py` and
  `market_e2e.py`; every reply ends "not advice".

**Still unverified:** whether the wake word really takes foreground from a focused
Excel window. Windows resists foreground steals and the ALT-key nudge is a
workaround. He was asked to try it by hand.

## 2026-08-30 (night): the freeze, and the watchdog that could not see it

He said "audit the code base". The audit found the thing that had frozen his
JARVIS earlier the same evening, and a second bug that turned it from a glitch
into forty minutes of silence. Both are fixed; both have gates.

**1. Event-loop deadlock in `audio/io.py`.** `play_chunk` wraps PortAudio's
blocking write in `to_thread` + `wait_for`, which is correct. What was not
correct is what happened on timeout: it called `abort()`, and `abort()` did
`with self._wlock:` - ON THE EVENT LOOP THREAD - while the writer thread still
held that lock, stuck inside `stream.write()` on a dead output device.
`stream.abort()` is supposed to free it and, on a device that has gone away,
does not always manage it. The comment on the line read "writer has returned;
safe to close". It had not. `close()` had the same bug, reachable from
`_ensure()`.

Result: no speech, no HTTP, no wake word, process alive, 19:40 to 20:21.

Both now go through `Speaker._release()`, which waits `_LOCK_WAIT_S` (0.75s) and
then ABANDONS the stream rather than blocking. A leaked handle costs a handle; a
blocked event loop costs the whole assistant. Gated: `tests/test_audio_io.py`
holds the lock exactly as a stuck writer does and asserts abort/close return.

**2. The supervisor could not tell "frozen" from "fine".** `Sidecar::is_alive()`
was `matches!(child.try_wait(), Ok(None))` - "the process has not exited". A
wedged sidecar satisfies that forever, so it was never restarted. There is now
`Sidecar::is_responding()` (GET /health, 3s) and the supervisor rebuilds after 3
consecutive misses (~60s), reusing `restart()`, which already stop()s the whole
tree. The crash-loop backoff is unchanged.

`/health` is the ONE unauthenticated route (66 of 67 are token-checked) and it is
now load-bearing for this. Do not put a token on it.

**How to test a hang** (killing the process only ever proves the OLD path):
`.agent/scripts/wedge_test.cmd` SUSPENDS every thread of the sidecar - alive by
every OS measure, answering nothing - and watches for the rebuild. Run it from a
real-session scheduled task like everything else.

**Audit notes worth keeping:**
- 66/67 routes authenticated; `_auth` uses `!=` rather than a constant-time
  compare (negligible on loopback, noted for completeness).
- All Rust HTTP calls carry timeouts, so the supervisor cannot hang itself.
- History/turns/_seen are all trimmed; no unbounded growth in hot paths.
- No unawaited coroutines (AST-checked); the apparent hits are sync `mic.start()`
  and sqlite `execute()`.
- 62 `except: pass` with no logging, mostly legitimate Windows fallbacks in
  windows_tools/uia/window_thumbs - but indistinguishable from real bugs.
- A hung audio write still LEAKS a thread from the default executor. The
  deadlock is fixed; the leak is not. Enough of them would starve `to_thread`,
  which every sync tool handler uses.
- `scripts/model_trial.ps1` is BROKEN: it parses `--token` from the command
  line, but the token moved to stdin, so it always reads an empty token.

**Do not run the e2e suites while he is at the machine.** They drive the same
orchestrator he is talking to; on 2026-08-30 six suites "failed" purely because
he was using JARVIS at the time. And never start a second deploy while a suite is
running - the hot-swap pulls the app out from under it.

## 2026-09-01 — the 2,600-message night, and a misdiagnosis to learn from

**What he woke up to.** Roughly 2,600 Telegram messages asking about his
retainers. He had shut JARVIS down the previous evening after ~50 of them; it
came back and ran until morning. *"I'm done."*

**Why it came back: my fault.** I created scheduled task `JARVIS_HOTSWAP` with
`/SC ONCE /ST 23:59` and left it armed. It fired at 23:59, and
`jarvis_hotswap.cmd` ends with `start "" jarvis.exe`. It relaunched the app he
had deliberately killed. **Helper tasks are created, run, and deleted in the
same breath — never left with a time-of-day trigger.** Task deleted.

**Why it flooded: three faults in a row.**
1. **`brain/router.py load()` never committed its deletions.** It drops stale
   seed examples on every start, but its only `commit()` sat inside
   `if missing:`. On any start where rows were dropped and nothing needed
   re-seeding, that connection held an open write transaction — and SQLite's
   single write lock — for the entire life of the process. Everything else then
   got "database is locked" forever. *This is the disease; the two outages were
   its symptoms.* Found 2026-09-01 from the live log: `brain loaded: 518
   examples` against 521 rows in the table — three deleted, never committed.
   Gate: `tests/test_write_lock.py`, verified to fail without the fix.
   (`registry._audit` also caught write errors without rolling back, which would
   hold the lock the same way; fixed too.)
2. `tasks/scheduler.py _fire` announced BEFORE it rescheduled. With the update
   failing, the row stayed `pending` with a past due time and the 10-second loop
   re-announced it six times a minute.
3. `delivery._too_soon` opened with `if not key or tier == URGENT: return False`,
   and `scheduler.announce` passes no key — so "unnamed" meant "unlimited".

**Fixes.** Claim-then-speak in `_fire`, with `_suppressed()` and a one-hour
back-off when the schedule will not write. `_key_for()` derives a dedup key from
the message text when none is given. URGENT now repeats on
`urgent_repeat_minutes` (10) instead of being exempt, while a genuine
*escalation* (higher tier, same key) still passes immediately. And
`proactive.max_messages_per_hour` (12) is a hard ceiling over every outbound
route — the backstop for the next bug, whatever it turns out to be.
Gates: `tests/test_reminder_flood.py`, `tests/test_delivery_budget.py`.

### The misdiagnosis — read this before touching anything in AppData

Last night I concluded the database was corrupt and spent hours salvaging it.
**It was not.** The file I read from the agent shell was the container's stale
copy; the real `%APPDATA%\JARVIS\jarvis.db` was `integrity: ok` all along:

| | agent shell saw | real file |
|---|---|---|
| memories | 4 | **12** |
| brain_examples | 91 | **521** |
| tasks | 4 | **226** |
| transcript | 6,001 | **15,135** |

`Get-FileHash` matched on both paths because BOTH resolved to the mirror.
No user data was lost — the real file was never written by any of it.
`.agent/scripts/jarvis_dbcheck.cmd` now prints the resolved path, size, mtime,
row counts and the full `tasks` table from the real session. **Run it before
diagnosing anything under AppData.** `tools/db_repair.py` and its gate remain
useful and correct; they were simply aimed at the wrong file.

## 2026-08-31 evening — turns stopped completing (cause corrected above)

Symptom: health green, wake word fine, routing correct, **no reply to anything**.
Every turn died at `memory/store.py log_turn` with `database is locked`.

Cause: `PRAGMA integrity_check` reported SQLITE_CORRUPT over ~60 pages of the
`transcript` b-tree (`audit_log` and `turn_stats` too — all three disposable
logs). **A corrupt page cannot be checkpointed out of the WAL**, so `jarvis.db-wal`
froze at 4.1 MB from 16:02 while the main file kept being written, the writer
never drained, and every write waited out `busy_timeout` and raised. Nothing
noticed, because SQLite only reports a bad page when a query touches one.

Salvaged with the new `sidecar/tools/db_repair.py`; the file is CLEAN and
writable again (write lock now 1 ms, was refused outright).

| table | kept | note |
|---|---|---|
| memories, facts, tasks, night_meta | all | untouched |
| brain_examples | 91 of 521 | **506 of the 521 keys were canonical seeds — `brain.load()` re-seeds them. Only 15 were learned; their text is in `%APPDATA%\JARVIS\salvaged-keys-brain_examples.txt`** |
| transcript | 6,019 of 10,278 | conversation log |
| audit_log | 3,185 | |
| turn_stats | 0 | instrumentation only |

Fixes that outlive the incident:
- `log_turn` / `recent_transcript` can no longer raise. Bookkeeping must never
  cost him the assistant — that single un-guarded INSERT was the whole outage.
- `config.open_db()` runs `check_and_repair()` **once per process before the
  first connection**, so damage is found at boot, not hours later.
- `db_repair` **refuses to swap** when a PRECIOUS table (memories, facts,
  brain_examples, brain_commands, tasks) loses rows, unless forced. Gate:
  `tests/test_db_repair.py` — which caught four bugs in the repair tool itself
  (unbounded rowid probe, write-errors miscounted as corruption, an infinite
  loop on the WITHOUT ROWID path, and a crash when Windows refused the swap).
- An intact index outlives its table: `sqlite_sequence` supplies max rowid when
  `MAX(rowid)` raises, and a UNIQUE index still enumerates keys — so a lost row
  can be NAMED rather than silently vanish.

**News, third narrowing — verified live, not just unit-tested.**
`tests/news_emergencies_live.py` runs the real sweep and prints both columns.
Before: 6 of 60 got through, 3 of them junk — a Somerville bar "taking shots"
(a shot of Malort), a Times Square stabbing riding Boston.com's wire feed, and a
jury verdict about the 1996 Tupac killing read as "somebody died close to home".
After: **2 of 60**, both real (an active Lawrence school shooting, a Sudbury
bridge closure). Three new guards in `significance.py`: `NOT_VIOLENCE`
(innocent senses of "shot"), `FAR_PLACE` (a dateline beats the desk a story
arrived on), `ADJUDICATED`/`STILL_ACTIVE` (a courtroom is not an emergency).

**Do not widen the national door.** I tried, to let a magnitude 7.1 California
quake through; `test_significance.py` failed instantly on "Hurricane makes
landfall in Florida, state of emergency declared" — which is on the list HE
named as "WAY too many news reports", beside "Tornado kills 14 in Oklahoma".
The bar is his: attack, nuclear accident, pandemic, grid down, toll in the
hundreds. `briefing.news_scope = "national"` restores the old behaviour.

## 2026-09-01 — the camera can see, count, and recognise; two phrasings could not

Camera phase 3 shipped: `vision_identity.py` (SFace, embeddings only, cosine
0.363 = OpenCV's documented 99.80% point), `vision_hands.py` (MediaPipe
HandLandmarker, 21 landmarks a hand, 8 ms a frame), and the HUD holds the camera
view anchored while it is on instead of tearing it down to go back to listening.

**The lesson worth keeping: a guard is not an outcome.** Two of his own phrasings
were broken in the shipped build and every offline test was green.

  * `"open the camera"` launched the Windows Camera app. An earlier fix had
    stopped camera seeds from stealing `open spotify` by deleting them all — the
    theft was fixed and this was created in the same stroke.
  * `"remember my face"` did **nothing at all**. The memory skill's guard refused
    it exactly as designed. But `_CANON` had already rewritten the words "my
    face" out of the sentence, so the fallthrough re-classified text that no
    longer mentioned a face, found nothing above threshold, and returned `None`.
    Silence. `test_identity.py` asserted that memory REFUSES the phrasing and
    never that anything CATCHES it.

Both were `_CANON` eating the object noun before the embedding saw it.
`camera|webcam` now sit in the same exclusion list as `music`/`volume`, and face
phrasings are excluded from the remember-canon. When a guard makes a skill step
aside, **gate where the utterance lands**, not just that it stepped aside.

**Verifying against the running app needs the OS, not a fixed port.** The Rust
core hands the sidecar an EPHEMERAL port every launch (52322, then 62683), so
scanning 8790-8799 finds nothing and any port file would be stale. Ask psutil
which port `jarvis-sidecar.exe` is listening on. See
`sidecar/tests/camera_live.py` (run it with `scripts/camera_live.cmd`) — routing, latency and the spoken sentence, run in
the real session against the installed build.

Measured live on the shipped bundle: `count_fingers` 0.53 s, `look` 0.71 s,
`camera_status` 0.02 s, camera opens in 0.47 s. His "it was buffering" is gone.

**Still needs him in the chair** (nothing offline can prove these): say "learn my
face" while seated to enroll, then "can you see me" should answer *"I can see
you, sir"*; and hold up fingers to check the count against reality.

## 2026-09-01 (later) — he asked JARVIS who he was and was told "user"

**JARVIS_PERSONA.md was loaded by NOTHING.** 274 careful lines that no code path
had ever read, while the persona actually shipping lived in a hardcoded string in
`llm/prompts.py`. They had drifted into contradiction: the document said "sir"
should be rare and optional, the running system says it at the measured film rate
of 37%, which is what he wants. Nobody noticed because nothing could notice. The
document is the spec now, its behaviour-bearing parts compile into the prompt,
and `tests/test_persona_sync.py` fails the build if they disagree.

**Naming him in the prompt was not the fix.** It corrected "who do you work for"
and did nothing for "who am i" — that question never reached the model, matching
the memory RECALL skill at cosine 1.000 and coming back as a list of stored
notes. Second time this shape has bitten in one day (see "remember my face"): a
prompt cannot answer what the router already answered. "who am i" is a reflex
now; recall keeps "what do you know about me"; both gated together.

**Most of what it "knew" about him was false**, and it was being injected into
every turn as context. He struck it himself: no Regina, no black coffee, no desk
lamp, two conflicting favourite-colour rows, plus octopus trivia and a stale
session note filed as personal facts. Twelve memories down to two, backed up
first. A cached research answer claiming the Ryzen 7 8845HS supports ECC was also
wrong — AMD validates ECC on the **PRO** variant only — and deleted, which
self-heals because the facts table re-researches what it does not hold.
His towns are Framingham and **Sudbury** (not Natick), the colour is blue, quiet
hours end **05:30**, and he follows the market broadly, not five tickers.

**The camera lag he reported had three causes**, found by probing rather than
reasoning (`.agent/scripts/camlag.py`, `fpsprobe.py`):
1. The loop read one frame then slept to pace 15 fps while the device delivers
   30, so declined frames QUEUED — three deep, ~100 ms — and every read returned
   the oldest. It consumes every frame with `grab()` now (device wait, not CPU).
2. The stream slept on its own clock and could re-send a frame the HUD already
   had. It blocks on a condition variable until a genuinely new frame exists.
3. My own fix then drifted to 10 fps: a deadline of "now + 66.7 ms" set from the
   frame just encoded lands a hair after the next device frame on a 33.3 ms grid.
   A stride by COUNT cannot drift.

**The camera ignores every mode request** — asking 1280x720@60 returns 1080p30 —
so 60 fps is not available at any resolution. 30 is the ceiling and is now the
target: 7.9 ms per frame all in (decode 4.9 + encode 3.0), 238 ms of each second,
~24% of ONE core of sixteen, 3.6 MB/s over loopback.

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
