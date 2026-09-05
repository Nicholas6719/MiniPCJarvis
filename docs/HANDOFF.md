# JARVIS — Continuation Handoff (living document)

Read this first after any context reset. Everything below was learned the hard way.
Updated: 2026-09-03.

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

## 2026-09-01 (evening) — the Evolution, phases 0 and 1

Six new capabilities, worked in phases with a gate between each. What is worth
not rediscovering:

**Four of the six were already built, and reading the repo first saved the work.**
`tools/weather.py` already existed — Open-Meteo, keyless, registered — so phase 1
extended it rather than creating it. `analyze_image()` already ran the Gemma 3 +
mmproj pipeline phase 3 names, so that phase is a prompt template and an input
path. `weather._geocode()` already resolves place names through Open-Meteo's free
geocoder, so **Nominatim is not needed** — no second network dependency, no
1-req/sec policy, no User-Agent requirement (kept as a fallback, not the first
reach). And `vision_identity.py` already does face embeddings with SFace.

**insightface was declined, deliberately.** It would duplicate SFace while adding
a Cython build (MSVC risk on Windows), an opencv dependency that risks the fight
mediapipe already caused, and — the disqualifying one — a **~300 MB model
download at RUNTIME to `~/.insightface`**, which breaks both the offline
guarantee and the bundle-the-models-in-the-spec pattern every other model here
follows. SFace is already bundled, already gated by 22 tests, and already stores
embeddings and never images.

**Phase 0's first finding was not in the handoff at all.** `requirements.txt` was
missing `opencv-python` and `mediapipe` — both installed by hand during the
camera work that morning, both load-bearing, neither declared. A fresh clone
could not have rebuilt this project. Nothing new goes into an environment that is
not reproducible, so that was fixed first.

**`jarvis.db` already has a `tasks` table** and it is reminders and errands.
Projects got `projects` and `project_steps` of their own; two unrelated features
writing the same rows is how a nightly retainer reminder ends up in a project
list.

**`volatile.py` is where phone-derived readings live**, and its one rule is that
nothing is read back without its age — `fresh()` returns None past its window
rather than a value a caller might use without checking the clock. That is not
pedantry: the identical mistake shipped in the camera the same morning, where
presence held him "present" for twelve seconds after he left and "can you see me"
borrowed it to say "I can see someone, sir" about an empty frame. A four-hour-old
location fix is the same lie with a different subject.

**Neither OpenSCAD nor PrusaSlicer is installed** (checked, both absent), so
phase 5's test will SKIP loudly rather than pass. A green tick for a tool that
never ran is worse than an honest skip.

**The wiring gate asserts risk TIERS, not just registration.** The tier is the
security boundary — a tool that quietly becomes SAFE stops asking permission — and
it counts remaining stubs so a phase landing behaviour without its test is
visible instead of silent.

**Build time is now the bottleneck: ~50 minutes**, most of it 51 gates (several
with real multi-second sleeps) plus a PyInstaller pass that bundles mediapipe and
four models. Worth knowing before planning a phase around "just rebuild it".

## 2026-09-01 (night) — the Evolution, phases 2 to 5

**Phase 2 nearly shipped broken in two independent ways, both found by reading
the poller before writing to it.** A live location arrives as `edited_message`,
not `message` — Telegram EDITS the original as he moves — and the handler only
read `message`. Worse, `getUpdates` was called with
`allowed_updates=["message","callback_query"]`, and Telegram does not merely
ignore a kind missing from that list, it never SENDS it. Either alone looks
exactly like "the feature doesn't work" with nothing in any log. A third was
designed out rather than discovered: a live share edits its message every few
seconds, so only the FIRST fix is acknowledged — replying to each would be the
2,600-message failure arriving by a new route.

Health JSON is untrusted external input and is written like it: size-capped
before parsing, allow-listed metric names, range-checked (a heart rate of 4,000
is a unit mix-up, not an emergency — dropped, and the last believable value
stands), unknown keys ignored, and nothing can raise into the poller. The sniffer
is strict the other way too: "what's my heart rate" is something he SAID.

**Phase 4's security property is the POSITION of the check, not the comment above
it.** `face_confirm` runs in `registry.execute()` after the spoken yes and can
only refuse. The test that matters is not "does it recognise him" — it is that a
MATCHING face with no spoken answer does not run the tool, and it asserts the
source ordering, so moving the check above the gate fails the build instead of
silently turning an additive signal into a replaceable one.

The judgement call, made deliberately: "both must pass" is unsatisfiable when the
second signal does not exist. Failing closed would lock him out of every
HIGH-risk tool the first time a webcam driver misbehaved. So UNAVAILABLE leaves
the spoken gate alone; AVAILABLE-and-wrong refuses. It can only ever ADD a
refusal.

`check_once()` was added to vision_identity rather than reusing `consider()`,
which caches its verdict for RECHECK_S — the same staleness that had "can you see
me" claiming to see a man who had left.

**Phase 5 did not fully run, and the suite says so.** Neither OpenSCAD nor
PrusaSlicer is installed, so generate-and-slice SKIP loudly and the notice
appears in the build log. Everything that does not need a binary is tested for
real, including a slice that reports no numbers — a warning, never a silent
success, because an invented 0 g estimate is worse than none.

**An audit finding on my own new code:** `face_confirm` was registered SAFE while
being able to turn the webcam on. SAFE means read-only with no side effects, so
it is LOW now — and deliberately not MEDIUM, which would demand a confirmation
for something that runs inside one. Worth repeating as a habit: audit the tiers
against what the handler DOES, not what it is for.

**And a bug in the test tooling, found because a gate needed it:** `suites.ps1`
read the port from `.agent\session.txt`, which only quick.ps1 and release.ps1
write. Every hotswap deploy left it stale — it pointed at a port from the
previous evening — so a full suite run would have failed wholesale for reasons no
commit could fix. It asks the running process now.

## 2026-09-02 — the hologram, phase B: will it print?

Phase A put a model on the stage. Phase B asks the questions worth asking before
plastic is spent, and every threshold is the industry one rather than a number I
picked: **45 degrees from vertical** for overhangs, **0.8 mm** as the thinnest
wall any FDM machine will lay down, **1.5 mm** as the thinnest worth trusting
with load. New: `sidecar/printcheck.py`, `sidecar/gcode.py`, the `inspect_part`
tool, `/holo/printcheck`, and `tests/test_printcheck.py` (60 assertions, gated in
`build_sidecar.cmd`).

**The layer preview is the real toolpath, not a simulation.** `gcode.py` parses
the file PrusaSlicer actually wrote. A simulated preview agrees with itself and
therefore tells him nothing; this one disagrees with his expectations exactly
where the slicer did something he did not expect, which is the only time a
preview earns its keep. Verified against a real 100-layer slice of a 20 mm cube:
0.25 first layer, 0.2 steps, top at 20.05 mm — the profile's arithmetic, exactly.

**G-code is a dialect, not a format**, and the parser handles the differences
that silently produce nothing: M82 vs M83 (PrusaSlicer writes absolute extrusion,
Cura relative — read a Cura file as absolute and every move looks like a
retraction, so the preview comes back EMPTY rather than wrong), G92 extruder
resets, `;LAYER_CHANGE` vs `;LAYER:` vs no comments at all, and travel moves that
must not be drawn. Each has its own gate.

**Four bugs, all mine, found by tests written to fail:**

1. **A 45 degree chamfer was flagged as an overhang.** `asin(-nz)` for a true 45
   degree face returns 45.00000000000001, and a bare `> 45.0` therefore reported
   the commonest deliberate feature in a printable part as a defect. There is a
   0.1 degree tolerance now — nothing about FDM is precise to a tenth of a
   degree, so it costs nothing real and removes a whole class of false alarm.
2. **`bed_fit` called a 50 x 50 x 400 tower too wide for the bed.** It sorted the
   three dimensions and called the two largest the footprint. The footprint is X
   by Y, and height is checked separately against the printer's 250 mm Z, because
   "turn it" and "cut it in half" are completely different answers.
3. **The model was lying on its side on its own bed.** STL, every slicer and the
   overhang maths all treat +Z as up; three.js treats +Y as up. Phase A fed STL
   coordinates straight in and hung the bed grid off `size_mm[1]`. Harmless while
   the hologram was only pretty — wrong the moment overhang faces are painted on
   it, since a face flagged as pointing "down" would have pointed sideways on
   screen. An inner `orient` group rotates once and everything downstream agrees.
4. **`degenerate_faces` could never be anything but zero.** It asked a
   `trimesh.Trimesh` built with `process=True`, which drops zero-area faces on
   load. A check that cannot fail says nothing; it counts the raw triangles now.

**The wall estimate needed no compiled dependency after all.** trimesh's ray
engines all want `rtree`, which is a PyInstaller problem for one number. But
these rays are axis-aligned, which makes the general Möller-Trumbore machinery
unnecessary — project each triangle onto the other two axes, test containment in
2D, solve the plane for the third coordinate. Pure numpy, 0.14 s for a real part,
and it catches a 0.6 mm plate. It is still an ESTIMATE and is labelled one in the
tool description, in the returned dict and in the sentence JARVIS speaks:
rigorous minimum thickness needs a medial-axis transform, and telling him a part
is sound when it is not would be worse than saying nothing.

**`slice_part` now looks at the mesh before handing it over.** Not to refuse —
the slicer's own repair is usually right, and refusing his file would be worse —
but because the failure that costs something is the quiet one: PrusaSlicer
accepts a leaky mesh, repairs it its own way, and prints a part that is not
quite the part he asked for.

**Inspecting does not seize the stage.** Asking whether a part he is not looking
at will print should answer him, not replace what is on screen. Gated live.

**Deferred to phase C, deliberately:** the exploded view, which the plan lists
under B. It is a control like rotate, section and reset, and it belongs with the
control surface rather than ahead of it.

**A third orphaned scheduled task, found by looking rather than by being bitten.**
The habit that grew around real-session verification was
`schtasks /Create /SC ONCE /ST 23:59`, with 23:59 used to mean "never". It does
not mean never; it means tonight. `JARVIS_PCLIVE` — a task I registered this
morning to run the phase B live check — was still registered and would have run
a generate-and-slice against his live app at 23:59 while he slept.
`JARVIS_SOUND`, written the same way on 2026-08-29, had already fired at exactly
that time. Same shape as the orphaned `JARVIS_SUITES_FULL` that sent him Telegram
messages at midnight. Both deleted — and then made structural, because a rule written in this file is
not a guard and this one had already been written here before the third escape.
`scripts/runonce.ps1` (tracked, not under the gitignored `.agent\`) does
create-run-wait-delete with the delete in a `finally`, and
`sidecar/tests/check_stray_tasks.py` is now the FIRST gate of every sidecar
build: it refuses to build while any task pointing into `.agent\` or named
`JARVIS_RUNONCE_*` is still registered, and prints the exact `schtasks /Delete`
line for each. Proven by planting one and watching the gate go red — a check
that has never failed says nothing. His own tasks are left alone;
`JARVIS_SELFTEST` is allowlisted by name and is the only permanent one.

**Two environment facts worth not rediscovering:** node is a portable install at
`C:\Users\nicho\Tools\node`, on nobody's PATH — not the agent shell's, not the
real session's; `where node` finds nothing in either.
`.agent\scripts\jarvis_tscheck.cmd` names the path outright. And
`JARVIS_SELFTEST` (daily 03:30) is legitimate, not another orphan: its suite list
contains no Telegram suite and it deletes the test reminder it creates.

## 2026-09-02 — the hologram, phase C: he works on it with it

Phase A projected a model, phase B told him whether it would print. Phase C lets
him change it by talking. New: `sidecar/holo_angles.py` (the way he actually says
angles), the `holo_control` tool, `edit_part` / `revert_part`, six skills
(`holo_move`, `holo_show`, `holo_hide`, `holo_check`, `holo_edit`,
`holo_revert`), and `tests/test_holo_control.py`. Routing accuracy is 177/177.

**The line that matters most: what is the VIEW and what is the PART.** Rotating,
scaling, sectioning and exploding are view state — the STL is untouched and its
millimetres do not move. `edit_part` rewrites the OpenSCAD source and re-renders,
so it changes the real thing, and it is the only one that says "that's the part
changed". `holo_control` returns `note: view only` on every call and the tool
description forbids saying the part got bigger. This is a part he is about to
spend an hour printing; the two must never sound alike.

**Voice editing works only because the source is on disk.** `generate_part`
keeps `<name>.scad` beside `<name>.stl`, so "make it twelve millimetres thick"
is a parameter change and a re-render, which either compiles or does not —
rather than mesh surgery, which is a research problem with unreliable results. A
part with no source (every tier-3 and tier-4 mesh) is refused in words rather
than approximated. The previous source is kept as `<name>.prev.scad`, so "put
the old version back" is a file copy. After an edit JARVIS names the dimension
that moved — "that was 6 millimetres, it's 12 now" — which is the half of an A/B
that still works when he is not looking at the screen. A side-by-side visual A/B
is NOT built; revert plus the spoken before-and-after covers the intent.

**"Slice it" needed a new kind of clarification.** It means either a cross
section of the hologram or a run through PrusaSlicer, and the existing clarify
engine speculatively runs every branch — which is right for two lookups and
wrong here, because both readings ACT. Speculating would cut the model open on
screen while he was still being asked, and start a real slice for an answer he
might never give. `Branch.speculative` is new: a deferred branch is asked about
and then run. It is only ambiguous while something is on the stage; with nothing
up, "slice it" plainly means the slicer and asking would be pedantry.

**Four more canon erasures, found by the gates rather than by him:**
`open the hologram` became `open APP` (it went looking for a program called
hologram to launch), `hide the hologram` became `hide everything`,
`close the hologram` became `close APP`, and `hide the layers` became
`hide everything` too. `3d` was being turned into `Nd` by the digit rule — and
`3d` is the word that distinguishes "create a 3D image of that" from any other
kind of image. A fifth turned up from the seed-collision gate:
`go back to the previous version` folded onto `switch to APP` and would have
hunted for a window called *previous version*. All five now have exclusions and
their own cases in `test_holo_control.py`.

**His own correction is gated in both directions.** "Show me Spider-Man" means
PICTURES and still routes to `images`; a hologram is only ever an explicit
request. Both are cases in `test_brain.py`, because that is where utterances land
rather than where they parse.

**Three bugs in my first cut of the control surface, all caught by tests written
to fail:**

1. **The phrase fallback guessed.** Anything it did not recognise fell through
   to "rotate", so "reset it" and "show me the layers" both spun the model. A
   wrong action is worse than an admitted miss — he watches it do the wrong
   thing and then has to undo it. `parse_action` returns None now.
2. **"Turn it upside down" rotated about the vertical axis**, which leaves the
   part standing exactly as it was and merely facing backwards. "Upside down"
   and "flip" name an outcome, not an axis, and the outcome is only reachable
   about a horizontal one.
3. **A slot invented parts.** "Does it fit on the bed" extracted a part named
   `bed` and sent `inspect_part` hunting for bed.stl. No stop-word list fixes
   that honestly — `bed`, `printer` and `supports` are all fine names for a part
   — so the work folder decides: a name counts only if an STL by that name is
   actually there.

**And a gate that had been quietly incomplete for two phases.**
`test_evolution_wiring.py` registers the tool modules by hand to mirror
`main.py`, and `holo_tools` was added to the app in phase A and never added
here. It passed the whole time, because nothing referred to those tools yet; it
only went red when phase C added skills pointing at them. It now parses
`main.py` for `X.register_all()` and fails if its own list is missing any of
them — proven against exactly the drift that occurred.

## 2026-09-02 — the hologram, phase D: in the background, with an estimate

Anything can become a hologram now, and it happens while he keeps talking. New:
`sidecar/render_queue.py`, `sidecar/render_estimates.py`, `sidecar/create3d.py`
(the four tiers), `tools/render_tools.py`, three skills (`holo_make`,
`render_stop`, `render_how`), and `tests/test_render.py`. Routing is 183/183.

**The estimate is measured, not invented — and the first measurement proved my
seed wrong.** I seeded tier 1 at 8 seconds on the reasoning that OpenSCAD is
fast, deliberately under the ask threshold so it would never interrupt him. The
first real run measured **27.97 seconds**, because the slow part is not OpenSCAD
at all: it is llama-server writing the source. The seed is 25 s now and tier 1
asks, which is right — half a minute of waiting deserves a heads-up. Tier 2, a
traced contour with no model involved, is the one that stays under the threshold.
This is the calibration doing exactly what it was built for, one run in.

**The question is a COST question and deliberately not the risk gate.** His
correction: an estimate alone is not enough, "because maybe I don't want to do
it if it's going to take over an hour". `generate_part` writes a file and is
honestly LOW; promoting it to MEDIUM to force a confirmation would corrupt what
the tier means — the same error as `face_confirm` at SAFE while able to open the
webcam. So a tool returns `_ask` and the orchestrator arms a conversational
yes/no on the clarify machinery. **Declining runs nothing**: the "leave it"
branch has no tool at all, so there is no half-written file to tidy up. `Branch`
with an empty tool is new, and `validate` skips it rather than calling
`risk_of("")` and refusing the whole question.

**Asking for a hologram of something he hasn't got now offers to make one.**
"I don't have a model to project" is true and useless; the machinery to say how
long and ask already existed, so `show_hologram` uses it. Only when he NAMED
something — with no name the newest part is meant.

**The four tiers, and honesty about which one ran.** Tier 1 OpenSCAD (exact,
voice-editable), tier 2 an image traced and extruded (sharp, cv2 + OpenSCAD),
tier 3 photo→mesh, tier 4 text→mesh. Every result carries its tier and a note,
because what he can do NEXT depends entirely on it — a tier-1 part can be edited
by voice, a tier-3 mesh has no parameters at all.

**Tiers 3 and 4 are NOT installed and say so.** They need PyTorch (~2.5 GB); the
sidecar is already 980 MB. They live under `C:\AI\model3d` in their own
environment, invoked as a subprocess, the same shape as `llm.server_binary`
pointing at llama.cpp. Missing means a sentence naming where it would live — and
specifically NOT a fall back to another tier, which would look like success and
be wrong. Their gate cases SKIP LOUDLY. **Worth knowing before installing them:**
the GPU here is an AMD Radeon 780M, so stock PyTorch on Windows would run them on
CPU (CUDA is NVIDIA-only; DirectML is the only other route). Expect minutes
rather than the plan's 40 seconds, and let the calibration find the real number.

**Two weak assertions of my own, both caught by looking rather than by a gate.**
The live cancel check asserted `cancelled in (True, False)` — a check that cannot
fail — and it passed while quietly testing an empty queue, because tier 1 had
started asking and nothing was ever submitted. And a queue test waited on the
jobs recording themselves rather than on the queue going idle, which is a race:
a job appends from inside its own thread a moment before `busy` goes false.

**A sliver is a failed generation, not a part.** Watching the finished loop in
the HUD turned this up: asked for "a hex spacer 12 mm tall", the local model
produced OpenSCAD for something **0.4 mm wide**, and the pipeline measured it,
projected it and announced it as ready. The only clue was a dimension rounding to
zero in the panel header. Anything under half a millimetre in its smallest
dimension now says so in the same breath as "ready" — half a millimetre being
comfortably under the 0.8 mm minimum wall, so a legitimately thin plate is never
caught by it. Tier 1's quality is the local model's quality; the pipeline's job
is to notice when it has produced nonsense, and now it does.

**Verified:** 56 build gates; 25 live checks against the deployed build,
including a real background render with the sidecar answering throughout —
40 status calls, worst response well under a second — the duration landing in
`render_times.json` under its tier, and the finished part projecting itself onto
the stage without being asked.

## 2026-09-02 — making it fast: 27 seconds to 0.2, and why the GPU was no help

He asked for it to be fast and performant. The measurement said the bottleneck
was not where the plan assumed.

**THE GPU CLAIM HERE WAS WRONG AND IS CORRECTED BELOW** (see the 2026-09-02
tiers 3 and 4 entry). `torch-directml` on a 2048-square matmul came back **1.3x**
the Ryzen 7 8845HS, and I wrote that down as "no acceleration to be had". A
matmul is a bad proxy — memory-bound, flattering to eight Zen 4 cores with
AVX-512, and `torch-directml` is not the best DirectML implementation available.
Re-measured against the ONNX models this project already ships, through ONNX
Runtime's DirectML provider, the 780M is **3.76x** on YOLOX at 640×640. The
conclusion that followed from the bad number — that the fast paths below were
the only option — happened to be right for a different reason, but the number
itself should not be trusted or repeated.

**The real bottleneck was llama-server writing OpenSCAD.** Tier 1 measured 27.5 s,
of which OpenSCAD is about 0.2. So most requests no longer go near the model:
`sidecar/parts_library.py` writes cubes, plates, cylinders, spheres, spacers,
washers and tubes directly from his own numbers. **Measured 0.12–0.30 s against
27.5 s**, and the parts come out exactly right — the dimensions are the ones he
said. This is now **tier 0**, with its own estimate bucket, and it never asks.

It is also more CORRECT than the model. Asked for "a hex spacer 12 mm tall" the
local model produced OpenSCAD for something 0.4 mm wide; the template produces a
6 mm hex post, 12 mm tall, with a 3.2 mm M3 bore. **The rule that keeps it
honest: match only when certain.** Anything carrying meaning a template cannot
hold — a bracket, a gear, a fillet, "shaped like a swan" — falls through to the
model, because a confident, exact, WRONG part is far worse than waiting half a
minute for a right one. The gate has more decline cases than match cases.

**A photograph now becomes something printable in a tenth of a second.**
`relief_stl` turns any picture into a lithophane — brightness becomes height,
dark becomes thick, so held up to a light the photograph appears. No model of any
kind, watertight, sliceable, 0.10 s measured. A picture therefore DEFAULTS to the
relief rather than to a minutes-long reconstruction he did not ask for; tier 3 is
reserved for when he says "scan" or "mesh". Dark-is-thick is gated explicitly:
backwards, it prints a photographic negative.

## I "found" a corrupt database that was never corrupt (2026-09-03)

**This is the AppData virtualization trap, for the second time, and it cost most
of a night.** It is worth reading before touching anything under `%APPDATA%`.

At 01:50 a stray import — a test run that had no `JARVIS_DB` set — opened
`%APPDATA%\JARVIS\jarvis.db` from the agent shell and reported
`SQLITE_CORRUPT` across `transcript`, `tasks`, `turn_stats`, `night_meta` and
`audit_log`. I checked it the way you are supposed to check a false positive:
copied the file somewhere else, opened it with no WAL alongside, got the same
answer. Ten `jarvis.db.corrupt-*.bak` files sat next to it going back to
31 August. Every one of those observations was real, and every one of them was
about **the container's shadow copy**.

The real database, read through `.agent/scripts/jarvis_dbcheck.cmd` in the user's
own session:

    integrity: ok        3,485,696 bytes
    memories 3 · facts 3 · brain_examples 794 · tasks 229 · transcript 15,854

Against the shadow's 110,592 bytes and 17 brain_examples. **Nothing was ever
wrong with his data**, and nothing I did touched it: agent-shell writes land in
the mirror too, so the "repair" rewrote the shadow and his own file was never
opened for writing.

`docs/HANDOFF.md` already carried this exact warning from 2026-08-31, in the same
words, about the same file — "the REAL database was `integrity: ok` the whole
time" — and I read the database from a Bash tool anyway. **The rule is not
"be careful with AppData". It is: an agent-shell read of anything under AppData
is not evidence. Run `.agent/scripts/jarvis_dbcheck.cmd` through
`scripts/runonce.ps1` FIRST, every time, before forming any opinion at all.**

### The db_repair bugs are real, and were found for the wrong reason

Three of them, all latent, all still worth having fixed — they would have fired
the first time the real database ever did need salvaging, and they would have
refused it:

**A table with no usable rowid counted 500 lost rows per failed read.** `tasks`
reported "1500 lost" from a table whose `sqlite_sequence` high-water mark is 229.
That number is what refused the swap. A failed read is one failed read now,
counted as `unreadable`, never as a row — a rowid that will not read cannot be
told apart from one deleted years ago.

**`INSERT OR IGNORE` counted rows it threw away as saved**, reporting "kept 38"
for a table that ended with 17 rows in it. Plain `INSERT` now.

**Then deduplication looked like data loss.** Those 21 were the same 17 rows
handed back through different rowids by a damaged b-tree. `IntegrityError` is a
duplicate; anything else is a row we read and dropped, and only that still
refuses the swap.

**And the refusal was the wrong shape** — the "a guard is not an outcome"
pattern again. Refusing because a precious table is unreadable preserves a
database in which that table is exactly as unreadable and loses everything else
with it. It is reported loudly now (`precious_unreadable`) and does not block.

Gated in `tests/test_db_repair.py`, each case named after the number that was
fiction.

## "There is no limitation to this" — the render always happens (2026-09-03)

His correction, after being told Iron Man Mark III lived behind an account:
*"don't worry about an account. Find an alternative to that... If I say 'Render
Iron Man Mark 3', I expect it to render whatever he has to do: take an image
from the web and then create that into 3D. If I say 'A duck', I expect it to be
in 3D. There is no limitation to this."*

And separately: *"Don't worry about printing yet. Right now we're just focused
on the actual holographic rendering."*

**So a locked website is never an outcome.** Tier 5 tries to download a real
sculpture; when it cannot, tier 4 reconstructs one from a reference picture, and
that is now the fallback for EVERYTHING that reaches tier 5 rather than only for
things `_ORGANIC` recognised. "Iron man mark 3" used to fall to tier 1 and have
OpenSCAD write code for a suit of armour. Pages that need an account are still
reported, but as an aside — never as the answer.

### The reference picture IS tier 4's quality ceiling, and choosing it was broken five ways

Every bad mesh this session came back watertight, correctly measured and
`sliceable: true`. Rendering them was the only check that could see anything.
Each fix below was found by looking at the picture that produced the lump:

| what it fed TripoSR | what came out |
|---|---|
| plain `"a duck"` — a mallard **half under water**, a close-up of duck **feet** | a lump |
| `"...white background"` — a **cropped bust** of the Mark III, cut at the waist | a lump |
| biggest-image-wins — TurboSquid's **orange squid logo**, 869x1017, served from DuckDuckGo's `/ip3/` **favicon** endpoint | a tangle of tentacles |
| a catalogue shot with the **same figure twice**, front and back | two Iron Men side by side |
| a **474-pixel thumbnail**, every single time | soft everything |

**The query now asks for what the model needs**: `"{desc} full body single object
on white background 3d render"`, checked side by side across a character, an
animal and a mug, because a phrasing that only works for figures is not a fix.

**Site icons are refused.** `/ip3/` is DuckDuckGo's favicon endpoint.

**Framing is measured, and the BOTTOM EDGE DOES NOT COUNT.** Border colour ->
mask -> bounding box. Measured across eight real candidates: every one touched
the bottom, because things stand on the ground. A rule that called that a crop
rejected the two best pictures in the set. Left, right and top mean a crop.

**Two subjects is a rejection.** A column of pure background inside the bounding
box means two objects; a single object never has one — a mug's handle is
attached, and the gap between a duck's legs still has duck above it.

**But nothing is filtered to nothing.** Rejecting outright returned no picture
at all and therefore no model. Candidates are ORDERED — one subject, then whole,
then fill, then size — and the worst picture still beats no picture.

**And the aspect cap was rejecting portraits.** At 2.2 it threw away both
474 x 1159 full-body Mark III pictures and left only the two-figure catalogue
shots. A standing figure is tall. It is 3.2 now, and it exists to catch banners.

**Full resolution, not the thumbnail.** The search hands over a DuckDuckGo proxy
around Bing's thumbnail service, and that service resizes on request: the same
Mark III at `?h=1200&rs=1&pid=ImgDetMain` came back a clean 490 x 1200 full-body
cutout. Height only — `w=1200&h=1200` pads it into a square. The thumbnail stays
as a fallback.

### The reconstruction came out lying down, and nothing knew

TripoSR works in the camera's frame: the input image's vertical becomes mesh
**X**. Measured on two meshes whose reference pictures I had in front of me — a
standing Iron Man came out `60 x 25 x 21` lying along X, and a duck came out on
its back. Every consumer assumes Z is up: the hologram, the bed footprint, and
the 45-degree overhang check, which is meaningless if it does not know which way
down is. A cyclic permutation `(x,y,z) -> (y,z,x)` is a proper rotation, so the
mesh is rotated and not mirrored; a mirrored part is a subtly wrong part that
passes every check. Iron Man is now `25 x 21 x 60 tall`, the duck `60 long x 31
x 44 tall`, and both LOOK right.

**This lives in `C:\AI\model3d\photo_to_mesh.py`, outside the repo and outside
the sidecar build**, exactly like `C:\AI\llama.cpp`. Reinstalling model3d loses
it. Backup at `photo_to_mesh.py.bak`.

### OBJ, because the hologram is not a printer

Everything read STL and only STL — `fetch` advertised `.obj` and then refused it
with "I can only read STL today, sir", and the GitHub scan never looked for one.
STL is a printing format: three vertices and a normal, nothing else. Anything an
artist sculpts is exported as OBJ. `meshio.load_obj` handles polygons (fan
triangulated — most sculpted exports are quads) and negative indices (relative
to the vertices seen SO FAR, which is why it cannot be done afterwards), and
drops materials, normals and texture coordinates because the stage draws
translucent faces and bright edges. glTF/GLB is the other format worth having
and is a bigger job; it is not here and is not pretended to be.

### Verified by looking

`.agent/scripts/render_mesh.py` draws front/side/plan from the STL, shaded by
face normal. It also learned which way is up the hard way: it assumed Z, TripoSR
writes Y, and three models in a row got a wrong verdict from what was actually a
squashed plan view.

Results: **Iron Man Mark 3** — a standing armoured figure, helmet, shoulders,
gauntlets, boots, 60 mm tall. **A duck** — beak, neck, body, tail, webbed feet.
Both from "render X" with nothing else said. Shots in `.agent/shots/look-*.png`,
reference comparisons in `refs-*.png`.

## Tier 5 — found, not made (2026-09-03)

His requirement: *"is there a way where he can 3D render anything? If I say
'render Iron Man Mark III', is it going to be able to do it? It needs to
happen."*

**The honest answer is that nothing on this machine can invent it.** Single-image
reconstruction gives a soft lump; OpenSCAD is a solid modeller and cannot sculpt
armour. Those are limits of the techniques, not settings. But nobody 3D-prints an
Iron Man suit by generating one — they download a model somebody spent weeks on.
So tier 5 finds the existing model. `model_find.py` searches, follows a GitHub
result to a raw file, downloads it size-bounded, and parses it before trusting
it. `create3d.from_the_web()` is the tier; `tools/model_tools.py` is the tool.

**Measured, not assumed:** Printables, Cults3D and MyMiniFactory all have the
models and all put the file behind a JavaScript app and a session. GitHub serves
raw files with no account. So GitHub is followed to the file and every other host
is offered as a page to open, with "that site needs an account" said out loud.

**Tier 5 falls back to the tier the request would otherwise have used** — 4 for
something sculptural, 1 for everything else, and never to a tier that is not
installed. That is what makes it safe to try the web first for anything that is
not a template, not dimensioned, not mechanical and not flat: being wrong costs
one search rather than a worse object. It fell back to tier 4 flatly at first,
which meant a baseball the web did not have came back a reconstructed lump
instead of the researched sphere it would have been.

### Eleven subjects run live, and seven wrong answers before it worked

Testing the headline case alone would have shipped every one of these. They did
not arrive as errors — each came back with a triangle count, a bounding box and
`sliceable: true`, and looked exactly like success:

| asked for | fetched | |
|---|---|---|
| a d20 dice | a webcam calibration card, 512 tris | wrong |
| a mandalorian helmet | a keyslot bracket | wrong |
| iron man mark 3 | a flat print plate of a forearm | wrong |
| an arc reactor | nothing — 8 real files rejected | wrong |
| a chess knight | nothing from the repo that had one | wrong |
| a coffee mug / a baseball | a mug / nothing, built one instead | right |

**Drawing them was the only check that could see it** —
`.agent/scripts/render_mesh.py` renders front/side/top from the STL. Third time
this session that a metric was correct about the wrong object.

**The root cause was one score answering two questions.** Repo relevance and file
selection are different. `crashworks3d_arc_reactor` holds six files and none of
them is named "arc reactor" — the repo IS the object. `D20-IRL-detection` names
"d20" and is a camera rig whose biggest file is a calibration card. The test that
separates every case in the sample: **does the repo name carry EVERY word of the
subject?** If so, any file in it that is not supporting hardware qualifies and
the biggest is the main piece. If not, the filename itself has to name the
subject.

**Git LFS.** `Poesghost/mandalorian_helmet` is the one repo on GitHub holding
real Mandalorian helmet shells, and it looked empty: every mesh in it is a
133-byte LFS pointer, filtered out as too small. The pointer states the true size
and `media.githubusercontent.com` serves the content with no account — an 11.3 MB
shell downloads. Repos worth having were exactly the ones being discarded.

**Print plates are not objects.** "Iron Man Mark 3" fetched a 19 MB,
395,174-triangle mesh measuring 15 x 30 x 3 — a perforated forearm shell laid
flat on a bed. Rejected on thinnest-over-longest; measured ratios: plate 0.10,
arc-reactor grid 0.17, helmet panel 0.76, mug 0.74, d20 0.87. Threshold 0.14, and
note the arc reactor sits only 0.03 above it. Body count does NOT discriminate —
the plate has 8 bodies and the arc reactor 11.

**Parts get announced as parts.** Wearable props are published as multi-part
print plates, so the best-matching STL in a helmet repo is usually one panel.
Being handed a quarter of a helmet and told it is a helmet is the same failure as
the emblem that was a disc. `is_piece` and the siblings ride along, and the
instruction tells the persona to offer the rest. Penalising piece-ness in the
ranking was tried and was worse: it put a 400 KB `helmet_attachments1.stl` above
the 13 MB front panel of the actual helmet.

**An STL carries no units.** Everything downstream believes the numbers are
millimetres: the bed check, the wall-thickness warning, the sliver guard.
`_unit_doubt` says so when the longest side falls outside 8-600 mm and names
inches or centimetres.

**Two truncation bugs, mirror images of each other.** GitHub results were
appended to an already-sorted list and then sliced off the end, so tier 5
reported "nothing fetchable" while four real repos sat just past the cut. Fixing
it by putting GitHub first then filled the whole list with GitHub, so the pages —
"Printables has real ones but they need an account", the honest answer for Iron
Man — became unsayable. Two lists with their own room, not one list and a slice.

**What works now, verified by looking:** a 3DBenchy at exactly 60 x 31 x 48 mm,
an anatomical human skull at 632,304 triangles, a numbered d20 at 21 mm, a mug
with a handle, an arc reactor lower grid at 99 mm reported as 1 of 5 parts, a
Mandalorian helmet panel reported as 1 of 3. Iron Man Mark 3 honestly reports
that Printables has real ones needing an account, and builds an approximation
meanwhile. Screenshots in `.agent/shots/look-*.png`.

**A bug the new tier caused, and the gate caught.** `make_hologram` used
`tier: int = 0` as its "he didn't say" sentinel — and 0 became a real tier. Every
request skipped tier selection and came back as a parametric template; "a dragon"
was answered in a fifth of a second. The sentinel is -1 now.

**A race the speed exposed.** With tier 0 finishing in 0.2 s, jobs started
landing exactly as the queue emptied — and the pump was restarted only when
`self._pump.done()` was true. The drain coroutine passes its `while self._jobs`
check, finds nothing and begins returning, and during that window the task is
not yet done: a job appended right then saw a live pump and **sat in the queue
forever**. It surfaced as a relief that never rendered and a "stop that" which
said nothing was running while a job was plainly queued. A plain `_draining`
flag closes it, cleared in a `finally` that re-checks the queue with no await in
between. The slow paths hid this; making things fast is what found it.

**And a status that lied, found the same way.** `status()` reported `busy: False`
while a job was QUEUED but not yet picked up — so asking "is it done yet" one
second after requesting something was answered "nothing's rendering, sir". Only
visible once renders got fast enough for that window to matter. Queued now reads
as busy with a `starting` flag, and JARVIS says "it's just about to start".
The same window made a live check wait on the wrong condition and conclude a
relief had failed when it simply had not begun.

**A note that disagreed with the work.** Tier 2 is two techniques wearing one
number, and the note was looked up by tier at submit time — so submitting a
photograph promised "traced from the picture and extruded" and then delivered a
relief. `create3d.note_for()` now decides it the same way `build()` decides the
work, so they cannot drift.

**Where tiers 3 and 4 stand:** still scaffolded, still honestly unavailable,
still refusing to fall back to another technique. On this hardware they would be
minutes of CPU whatever we do, and the fast paths above cover what he actually
wants from a picture. The seam is there for when a machine with real GPU compute
is.

## 2026-09-02 — the hologram, phase E: hands, designed around fatigue

New: `sidecar/hand_gestures.py` (pure functions over landmarks),
`sidecar/hand_control.py` (the tracking loop), `vision_hands.read_pose`, the
`hand_control` tool, `hands_on`/`hands_off` skills, a HUD indicator, and
`tests/test_hand_control.py`. Routing is 188/188.

**The research is blunt and it shaped the design rather than decorating it.**
Sustained mid-air gesturing causes measurable arm fatigue within a minute or
two — "gorilla arm" — so three rules, each of which is asserted in the gate:

1. **Hands are never required.** Every gesture emits exactly the payload a
   spoken command emits, so `holo_control` stays the single control surface.
   The gate asserts the gesture vocabulary is a subset of `_ACTIONS`: if they
   ever diverge, hands become a second way to do things words cannot.
2. **Supported postures.** `ROTATE_GAIN` is set so a quarter of the frame turns
   the model most of the way round — a few centimetres of travel with the elbow
   bent and the forearm resting, not an arm outstretched sweeping the screen.
   That number is asserted, not left to drift.
3. **Short engagements.** Tracking ARMS on a pinch and RELEASES on an open palm
   or a hand leaving frame, so the resting state is hands down and nothing idles
   waiting for him to hold a pose.

**`read_pose` is deliberately not `read_many`.** The existing hand reader takes a
majority vote across six frames, which is right for "how many fingers am I
holding up" and completely wrong for following a hand: a vote over half a second
is half a second of lag, and lag is the whole difference between a control that
feels attached to his hand and one that does not.

**It does not switch the camera on.** A hologram appearing arms nothing; he has
to ask. The tool is LOW rather than SAFE because it reads the webcam
continuously for as long as it is armed, and it disarms itself when the stage
closes, when the camera stops, and after 45 s with nothing in frame. The HUD
shows WATCHING YOUR HANDS the whole time — a camera reading continuously has to
be visible while it is doing it.

**The mirror.** No `cv2.flip` anywhere in the capture path and no CSS mirror in
the HUD — checked rather than assumed — so the flip belongs in `grip_point`. He
moves his hand right, the raw pixel moves left, and without the flip the model
turns the wrong way, which reads as broken rather than reversed. Gated in both
directions.

**What it costs, measured rather than assumed — and made three times cheaper.**
First measurement: **+49% of a core** over the camera alone. Decoding frames at
half size (`IMREAD_REDUCED_COLOR_2` — a quarter of the pixels, and landmarks are
normalised so nothing downstream changes) took that to +43%, which showed the
decode was never the cost: the landmarker is, at ~30 ms a frame. So the rate is
the lever, and 14 fps became 10. **Final: about +30% of a core** — which is
arithmetic, not luck: 10 detections a second at ~30 ms each. Repeated runs read
+13% and +32%, because the camera's own CPU swings with what the HUD is doing and
the delta is therefore noisy; **the +13% first reported was the lucky sample, not
the truth.** The live gate asserts under 45%: above the true value with real
headroom, still below the ~49% that full-size decoding at 14 fps cost, which is
the regression worth catching. 100 ms of latency, fine for the coarse path when
the precise path is a sentence.

**A sixth canon erasure**, found by the seed-collision gate:
`stop watching my hands` folded onto `stop watching METRIC` — the system-monitor
rule — so turning the gesture tracker off collided head-on with cancelling a CPU
alert, and the word `hands` was erased before anything could act on it. Excluded
now, with cases both ways in `test_brain.py`.

## 2026-09-02 — tiers 3 and 4 are real, and the GPU number was wrong

He asked for tiers 3 and 4 to be made possible. They are, and getting there
overturned the measurement the previous entry rested on.

**THE 780M IS WORTH ~4x, NOT 1.3x. I was wrong, and the bad number was mine.**
The 1.3x came from a `torch-directml` 2048-square matmul — memory-bound work that
flatters eight Zen 4 cores with AVX-512, run through what turns out to be the
weaker of the two DirectML implementations. Re-measured against the ONNX models
this project already ships, through ONNX Runtime's DirectML provider:

```
yolox  1x3x640x640   CPU 65.3 ms   780M 17.3 ms   3.76x
sface  1x3x112x112   CPU  7.9 ms   780M  4.7 ms   1.67x
```

A real convnet gets nearly 4x; the smaller the tensor the less it wins. The
lesson is not about DirectML, it is about benchmarks: **a microbenchmark that
does not resemble the workload is not evidence.**

**Tier 3 is TripoSR (MIT), running here, measured.** 18.8 s inside the worker,
**32.8 s end to end through the sidecar** — the difference is starting the
subprocess and importing torch. Watertight, sliceable, and scaled into real
millimetres, because TripoSR works in a normalised space and an unscaled mesh
measures about two millimetres across, which the sliver check would rightly
reject.

**Background removal was 20 of the first 36 seconds, and was almost entirely a
bad default.** rembg 2.x defaults to `bria-rmbg`, a far heavier transformer:

```
bria-rmbg   CPU 11.16 s/image   780M 5.51 s     (the default)
u2net       CPU  0.28 s/image   780M 0.06 s     (what TripoSR was built around)
```

Forty times the cost for one silhouette. Asking for `u2net` explicitly, and
passing `DmlExecutionProvider` explicitly — rembg's own provider selection checks
CUDA, ROCm and OpenVINO and then falls through to CPU, so it would never touch
the iGPU here — took a warm run from **36.6 s to 18.8 s**.

**Tier 4 is tier 3 with a reference picture in front of it, deliberately.**
Direct text-to-3D (Shap-E) is another 1.3 GB, minutes of CPU, and produces the
blobs the plan itself called "rarely printable". Finding a picture and
reconstructing that reuses the image search JARVIS already has and the model
already installed. It is honest only because it says so, and `TIER_NOTE[4]` does:
what comes back is a mesh of a picture of a duck.

**Marching cubes without a compiler.** TripoSR imports `torchmcubes`, a CUDA/C++
extension. `model3d/_mcubes_shim.py` registers a scikit-image implementation
under that name before `tsr` is imported.

**It is reproducible.** The worker and its shim live in the repo under
`model3d/`, and `scripts\install_model3d.ps1` rebuilds `C:\AI\model3d` from a
clone — venv, CPU torch, deps, the TripoSR checkout, the worker, and the weights
pulled up front so his first request is not a minute of downloading. The first
version of this existed only on this machine, which is the same mistake as the
camera stack's undeclared dependencies.

**Two false matches in the template library, found by probing rather than by a
test.** "A plate with 4 mounting holes 60 by 60 by 5 mm" came back as a plate
with ONE centred hole, and "a cube 20 mm and a plate 30 by 30 by 2 mm" came back
as just the cube — confident, exact, and not what he asked for, which is the one
failure that library must not have. And the first fix broke what it protected: a
rule counting `\d+` read "a 25 mm sphere" as a count and declined it. A digit
before a unit is a dimension; the real signals are plural nouns, counting words,
and "and a".

**Two stale assertions the new capability exposed**, both now testing the better
behaviour: `test_holo` asserted that naming an unknown part is an error, which
became an offer to make one; and `test_render` asserted tiers 3 and 4 refuse,
which they no longer do. The refusal path is now forced by pointing
`model3d_dir` at nowhere, so it is exercised deliberately rather than depending
on what happens to be installed.

## 2026-09-02 — the audit: six bugs, and a 7.5 MB payload

Audited for bugs and performance. The theme is that **tier 3 changed the inputs**
— meshes went from 150 triangles to 38,000 — and several things that were fine
at the old scale stopped being fine, silently.

**1. The wall estimate called every reconstructed mesh unprintable.** A
watertight 60 × 46 × 20 mm duck measured a **0.01 mm wall**. Those spans are
real: marching cubes leaves hair-thin slivers wherever the isosurface met the
grid tangentially, far below the 0.375 mm voxel and far below the nozzle. The
minimum was therefore a true measurement of an artefact. `thinnest_wall` reports
the **5th percentile** now, and the raw minimum alongside it as
`thinnest_seen_mm`, so nothing is hidden. Known answers all hold: 20 mm cube →
20, 0.6 mm plate → 0.6 and flagged, 1.2 mm plate → 1.2. Duck → 4.51 mm.

**2. `/holo/geometry` blocked the event loop for half a second.** `to_payload` is
0.48 s of parse, weld, feature edges and centring on a tier-3 mesh, and it ran
inline. Harmless with 150-triangle brackets; half a second of dead loop the
moment a reconstructed mesh went up, in the middle of whatever else he was
saying. It is in a thread now.

**3. "Stop that" did not stop it.** Cancelling the awaiting task does not touch a
subprocess — proven by watching the process survive — so a cancelled tier-3
render kept 1.7 GB of TripoSR weights and a core busy for another half minute
after he had been told it had stopped. He would have heard the fans. `_run` now
kills the child on `CancelledError` and closes the pipes, and there is a gate
that starts a real process, cancels it, and asserts nothing is left.

**4. The calibration could learn the wrong tier.** The queue timed the tier it
SUBMITTED, not the one that ran. A parametric template that fails to build falls
through to the model, and a 27-second run filed under tier 0 would drag its
median from a fifth of a second up to a wait he would then be asked about.

**5. Two things named `busy` meant different things.** `status()` learned to
count a queued job as busy; the property did not. Anything waiting on
`not busy` for work to finish therefore stopped waiting before it started —
which is exactly how the calibration gate above concluded a job had never run.

**6. Two more false matches in the template library**, found by probing with
realistic phrasings: "a plate with 4 mounting holes" produced a plate with ONE
centred hole, and "a cube 20 mm and a plate 30 by 30 by 2 mm" produced just the
cube. And the first fix broke what it protected — counting `\d+` read "a 25 mm
sphere" as a count of 25. A digit before a unit is a dimension; the real signals
are plural nouns, counting words and "and a".

**THE PAYLOAD: 7.48 MB → 2.01 MB, byte-exact.** A tier-3 mesh is 344,556
coordinates, and as a JSON array of numbers that is 7.5 MB the browser parses one
number at a time. As base64 float32 it is 2.0 MB arriving as a single typed
array — and it loses nothing, because three.js converts to float32 anyway.
Rounding the JSON was tried first and saved almost nothing: numpy's float32
widens back to float64 on `tolist()`, so `round(x, 2)` produced
12.34000015258789.

Also fixed: a reference picture downloaded from a web search had a floor of 2 KB
and **no ceiling at all** — a hundred-megabyte image would have been pulled into
memory whole. And `hand_control` cancelled its own task from inside its own loop,
which delivers a `CancelledError` to whatever happens to be awaiting next; it
stands down and breaks instead.

## 2026-09-02 — what the research changed, and the bug it uncovered

Four changes taken from the research pass, in the order they mattered. The third
one turned into the most important finding of the session.

**A layer slider on the toolpath preview.** Every slicer has one; ours drew all
hundred layers at once, which is why a sliced cube looked like a solid green
block. He can now say "show me layer fifty", "next layer", "the top layer", or
nudge it with the arrow keys, and a scale up the right-hand edge shows how far up
the print he is looking.

The layers go into ONE buffer bottom to top, so scrubbing is a
`geometry.setDrawRange` — one draw call, instant, no per-layer meshes. Two things
had to agree for it to work: asking for a LAYER implies wanting the layers up, so
the store sets `showLayers` for the `layer` action too (without it the visibility
effect switched the toolpath back off a frame after the scrub appeared), and
`parse_layer` is checked BEFORE the `layers` switch in `parse_action`, or "show me
layer fifty" merely turns the preview on again. `_CANON` erases plain digits before
embedding, so the 50 only survives because the slots parse the RAW sentence.

**A grab affordance.** The mixed-reality toolkits all landed on the same answer:
show what is grabbable before the grab. Ours had exactly that gap — the camera
could be armed and the model looked identical either way, so the only way to find
out whether it was listening was to wave at it. Eight corner brackets now appear
when hands arm and brighten when he has hold. Corners rather than a full
wireframe box, because a box hides the part inside it, and sized at a twelfth of
the shortest side so they read as the corners of THIS object.

**Fillets and chamfers — where the real bug was.** OpenSCAD has no fillet
operator, so anything rounded goes to the model. I asked the running JARVIS for
three rounded or chamfered parts and built what came back. **One of the three
worked.** The three failures were all different and all real:

1. **`max_tokens` was a budget for the ANSWER, and this model spends it on
   THINKING FIRST.** "A 20 mm cube with a 2 mm chamfer" at `max_tokens=700`
   returned `finish_reason=length`, 2,443 characters of reasoning and **zero
   characters of code** — and `generate_part` reported "the model returned no
   source", which names the symptom and hides the cause completely. The same
   prompt finishes in about 560 tokens when it is allowed to think first. Fixed
   at the call site (700 → 2000) and made visible everywhere: `llm/provider.py`
   now logs a warning naming this exact condition when it sees it, so the next
   occurrence in any other call site is greppable instead of silent.
   **There are other small `max_tokens` in the tree** — `vision_tools` at 120,
   `vision_analyze` at 260, `facts` and `night_school` at 400–600. They have not
   been shown to starve, but they are the same shape of risk.

2. **It writes OpenSCAD like Python.** `arm1 = cube([40,20,4]);` — geometry
   assigned to a variable, which is a parser error, so the part never existed.
   Telling it not to in the prompt was not enough on its own, and feeding back
   OpenSCAD's own complaint made it worse: "syntax error in file ..., line 6"
   names a position and no cause, and the retry produced the same mistake one
   line lower. `_GEOMETRY_AS_VALUE` in `tools/fabrication.py` recognises the
   pattern so the retry can say the actual lesson.

3. **Its instinct for rounding is `minkowski()` with a sphere**, which rounds the
   BOTTOM face too — the part rocks on the bed, needs supports, and grows by the
   radius in every direction. "A plate 40 by 30 by 5 with rounded corners" came
   back 11 mm thick with a domed underside, and **built cleanly**, which is the
   dangerous kind of wrong. The prompt now gives the flat-bottomed idiom
   (`minkowski()` with a thin CYLINDER) and the size compensation.

Also: a build failure used to be terminal. It now retries once with the compiler's
own words fed back — not when it is already a retry from `create3d`, because a
retry of a retry is four model calls for a request that plainly is not landing.

**After the fixes, three of three build**, all spec-verified against the
dimensions in his sentence, with no libraries, no geometry-in-a-variable and no
sphere-minkowski. One of them failed its first attempt and the retry recovered it,
which is the loop proving itself on a real failure rather than a synthetic one.

The lesson worth carrying: **I found all of this by asking the running system for
three ordinary parts and looking at what came out.** The offline gates were green
throughout — they tested the parser, the queue and the maths, and nothing tested
whether the model could actually write a rounded box.

**And then the screenshot found a second one.** Photographing the armed hand-
control state showed the webcam feed where the hologram should have been: `set_
camera` gives the camera panel the stage, and hand control required him to turn
the camera on as a separate sentence — so the model he was reaching for vanished
behind a picture of his bedroom. Every functional check passed. The gestures
worked perfectly on a hologram nobody could see.

Two changes, and one of them REVERSES AN EARLIER DECISION, so the reasoning is
recorded rather than just the outcome:

* **`hand_control` now turns the camera on itself.** It used to refuse — "the
  camera's off, sir, say the word" — deliberately, so the choice stayed his. That
  was not consent, it was friction: "control it with my hands" is already an
  explicit request for a camera-driven feature. The privacy line still holds and
  is now drawn between LAYERS: `control.arm()`, the primitive, still refuses to
  start a camera (it is not a request from him), while the TOOL starts it, says
  "Camera on", and the HUD shows WATCHING YOUR HANDS the whole time. A camera
  opened for an arm that then fails is closed again. All four halves are gated.
* **The camera panel no longer takes the stage from a hologram.** Turning it on
  with a model up is nearly always the first half of reaching for the model.

Worth remembering next time: **a feature you can only judge by looking has to be
looked at.** The layer scale and the grab affordance both routed, acknowledged
and gated perfectly while one of them was invisible.

## 2026-09-02 (later) — auditing against the plan, and a test that lied

Went back through `get-ready-for-the-quirky-lightning.md` looking for gates the
plan asked for that nobody had actually run. Three were outstanding, and chasing
them turned up a test that had been reporting a working feature as broken.

**The soak was the point of phase E and it did not touch phase E.** The plan
says, in as many words: run `soak_e2e` after phase E, *because the landmark
stream is a new always-resident path*. `soak_e2e` exercises audio, COM, UI
Automation and the browser — and nothing to do with the hologram. Running it as
written would have satisfied the sentence and tested none of the new long-lived
code. It now puts a model on the stage and arms hand tracking for the whole run,
hits `/holo/geometry` and `/holo/printcheck` at the rate a person looks at a
model, and tears both down at the end — a camera left on by a test is exactly
the surprise this project spends so much effort avoiding.

Two new assertions there are worth keeping:

* **RSS growth per minute**, because a leak in a camera loop reading ten frames
  a second is a crash three days later, and nothing else in the suite would see
  it coming.
* **`frames`, not `armed`.** A tracking loop that dies leaves `armed` true
  forever: the badge stays lit, his hands stop working, and nothing is logged.
  The frame counter is the only honest witness, so `hand_status` now exposes it
  (SAFE — it reads a counter; `hand_control` stays LOW because it reads the
  webcam).

**Idle CPU with the stage open — the phase A gate nobody measured.** Now
`.agent/scripts/holo_idle.py`. The first version of it reported 0.4% in every
state and passed every threshold, which is the "a check that cannot fail says
nothing" trap in its purest form: it was sampling four processes with the window
minimised, and WebView2 throttles requestAnimationFrame to nothing when the
window is not visible. Fixed — raise the window, walk process descendants to
every depth (the rAF loop runs in a GRANDCHILD of jarvis.exe, not a child) and
exclude llama-server, which burns whole cores and would swamp the number.

The real figures, on the deployed build:

| state | CPU (of one core) |
|---|---|
| no stage | 1.8% |
| hologram up, nothing moving | 2.7% |
| toolpath up | 2.8% |
| ten seconds after a scrub | 2.7% |

**A hologram costs about nine tenths of one percent of a core**, and the toolpath
costs nothing measurable on top of the model — which is the `setDrawRange`
design paying off, since it is one draw call whether it is showing one layer or
all thirty. Nothing fails to settle.

**A TEST THAT LIED, AND THE LESSON UNDER IT.** `render_live.py` reported three
failures — "a real mesh came out of the photograph" and two more — for tier 3.
Tier 3 was fine. The script WRITES the test photograph, and run from the agent
shell that write lands in the virtualized shadow of `%APPDATA%` while the sidecar
reads the real one: the picture "exists" here, the job starts, and the render
fails instantly with nothing to work from. Run through `schtasks` in his own
session the same script gives **tier 3 in 26.3 s, a real 60 x 46 x 20 mm mesh,
ALL PASS**.

The sandbox trap is the first thing in this document and it still caught me,
because it wore a new costume: not "the install did not happen" but "the feature
is broken". So `render_live.py` now refuses to run in the wrong session — and
getting that guard right needed the trap understood properly:

> **Reads are MERGED; only WRITES diverge.** A file the sidecar writes IS visible
> from the agent shell. So "make a file and stat it" passes in both sessions and
> proves nothing — my first guard did exactly that and sailed through. The only
> question that distinguishes the two views is the other direction: write a
> marker here, and ask the SIDECAR to open it.

Comparing the path strings does not work either. Both sides print the identical
path and resolve it differently. That is the whole trap.

Also measured, the last item on the plan's verification list — **GPU contention
against llama-server**, the stated risk, since the 780M has no dedicated memory
and the HUD and the model share one piece of silicon. Measured as the thing he
would feel: how long a real answer takes, with and without a hologram and its
toolpath being drawn.

The soak, with the hologram and hand tracking resident: **36 rounds over 185 s,
same process throughout, memory flat (-6.8 MB/min), and the tracking loop turned
379 frames before standing itself down.** That stand-down is the fatigue rule
working, not a fault — and the first version of this test asserted the opposite,
demanding the tracker still be armed at the end when nobody had been in front of
the camera for three minutes. The assertion now checks what actually matters:
that the loop RAN, and that if it stood down it did so having run first, because
an instant disarm with zero frames is a broken tracker wearing the fatigue rule
as a disguise.

## 2026-09-02 — a message from his phone turned his monitor on

His report: messaged JARVIS on Telegram at night with the monitor off, and the
PC's screen came on. He does not want that — Telegram is how he talks to JARVIS
when he is NOT at the machine, so anything the remote path does to the screen
happens in a room he is not in.

`_remote_turn` called `wake_if_sleeping()`, which calls `_wake_from_sleep()` ->
`exit_sleep_mode()` -> `wake_display()` plus `SetForegroundWindow`. It needs the
first part: the turn path only runs from IDLE, so without leaving SLEEPING he is
answered by nothing at all. It does not need the second.

So **waking the STATE MACHINE and waking the MACHINE are now two different
things**: `wake_if_sleeping(surface=False)`, which the Telegram bridge passes.
Saying his name at the desk still brings him to the front and still lights a dark
panel — that behaviour is untouched and gated alongside the new one.

Checked while fixing it: nothing else on the remote path reaches the screen by
itself. The Tauri shell's three `unminimize()/show()/set_focus()` sites are all
user-initiated (hotkey, tray menu, tray double-click) and none reacts to sidecar
state; `search_brave_web` uses SW_SHOWNOACTIVATE and does not steal focus. The
`exit_sleep_mode` TOOL is left alone deliberately — if he asks from his phone to
bring JARVIS forward, that is the request. **The rule is about side effects he
did not ask for.**

## 2026-09-02 — one Telegram exchange, four problems

He sent two screenshots of a conversation with JARVIS from his phone. Two things
he asked for, and two more visible in the same exchange.

**SHOW HIM, DON'T TELL HIM.** His instruction: *"if I ever ask Jarvis to do
anything on my computer — minimizing something, opening something, deleting
something, whatever I ask him to do — he should always send me a screenshot so I
can see that he had done it and then I can instruct him further afterwards."*

He is right and the reason is worth stating: from the phone he cannot check.
"File removed, sir." is a claim; a picture of the desktop is evidence, and it is
also the context for his next instruction. In the exchange he had to type "show
me" after every single action. A remote turn that touches anything in
`CHANGED_THE_DESKTOP` now takes a screenshot on its own.

An explicit set rather than a risk tier, because LOW covers plenty of tools that
change nothing visible — a screenshot after remembering a fact is noise. **Add to
that set when a new tool moves something on screen.**

**AND STOP NARRATING IT.** *"He doesn't need to say screenshot saved every time
he does it, I'll know he took a screenshot by him actually showing me."* So
`say_screenshot` returns an empty string. Where it was SAVED survives, because a
picture cannot say that. Both reflex emit sites now skip an empty reply rather
than pushing it at the speaker — silence can be the right answer.

**HE CONTRADICTED HIMSELF ABOUT DELETING FILES**, which was not in the ask but is
in the screenshots:

    "remove the screenshot from my desktop"      -> "File removed, sir."
    "now remove Wispr Flow from the desktop too" -> "I don't have a tool to
                                                    delete files directly."

It was not lying. The tool shortlist ranks tools by embedding similarity and cuts
at 30, and **`delete_file` ranked inside the top 30 for the first sentence and
outside it for the second** — a proper noun pulls the sentence away from whatever
the verb wanted. Measured, both ways, before and after. `shortlist.py`'s own
docstring warns about exactly this: *"A tool wrongly withheld is a capability
that silently disappears."* It happened anyway, because ALWAYS listed
`list_folder`, `find_files` and `preview_file` and none of the tools that ACT.
Being able to find a file always and act on one only sometimes is not a coherent
capability. The file-mutation and window tools are in ALWAYS now; the prompt
still shrinks (43 of 62 on average), so the speed win is intact.

**AND A MISS WAS A DEAD END.** "Remove that screenshot and whisper flow from my
desktop" got "I'm sorry, sir." — the model's own words, not a canned string.
`delete_file` takes an exact path, and the file is `Wispr Flow.lnk`, which is not
how he spells it and never will be: he typed "Wisper flow" and "whisper flow".
The tool returned `not found` with nothing usable, so the model apologised and
stopped.

It now hands back near matches. Substring alone was not enough — it found the
file for "Wispr Flow" and missed both of his actual spellings — so it falls back
to a `SequenceMatcher` ratio at 0.7, loose enough for a dropped letter and tight
enough that "Documents" does not match "Downloads" (0.44). Names only; nothing is
deleted on a guess, and it still reports the miss honestly rather than pretending.

That last one is also the compound-request fix he asked for. The 8-round tool
loop already supported several calls per turn — what it did not have was a way to
recover from the first miss.

## 2026-09-02 — the follow-up window, and what "more" means

He asked for the conversation window at five seconds, and for context to survive
it closing: *"if I say 'Show me two images of Iron Man' ... then the conversation
window closes, and then I use the wake word and say 'Show me three more images'
... it should know that we're talking about Iron Man."*

**FIRST, I HAD TOLD HIM THE WRONG NUMBER.** Asked how long the window was, I read
the DEFAULT out of `config.py` and said eight seconds. His saved config said
**thirty**, which is why it felt long — nearly four times the default. The
default is what a fresh install gets; `%APPDATA%\JARVIS\config.json` is what he
is running. Read the file, not the source. Set to 5 through `PATCH /config` so
the running process and the file agree without a restart, and the default lowered
to match.

**The window and the memory were already separate**, which is the right design:
`_armed_until` decides whether the WAKE WORD is needed and is cleared on sleep,
idle and follow-up; `_history` holds the last 20 messages and is not touched by
any of them. Nothing needed fixing there.

**What was broken was "more".** Two faults, both found by running his exact
sequence rather than reasoning about it:

* `clean_image_query` understood the DIGIT and not the WORD. "5 images of
  spiderman" gave `("spiderman", 5)`; "two images of iron man" gave
  `("two images of iron man", None)` — the count lost AND the phrase "two images
  of" sent to the search engine as part of the subject. He heard "Here are some
  pictures of two images of iron man."
* "show me three more images" cleaned to the keywords **"three more"**, which
  went to the engine literally. He was shown pictures of the words "three more"
  and told so.

Fixed in `show_images` rather than in the brain's slot extractor, because both
roads arrive there — the reflex AND the model writing its own query, which had
also written "three more". `_last_subject` remembers what was last searched;
`more_request()` recognises a bare follow-up and only a bare one, so "show me
more cats" and "show me another dragon" keep their own subject. `say_images` now
reads the RESOLVED query off the result instead of the slot, or it would still
have said "pictures of three more".

**Two of my own checks could not fail, in one session.** The first version of the
context test searched the whole transcript for "iron man" — which turn ONE
contains, so it passed no matter what turn two did. Then when the probe read the
wrong JSON key and every reply came back empty, "does not search for the words
three more" passed on an empty string. Both were caught by looking at the output
rather than the verdict. **A green check on a value you have not printed is not
evidence.**

Verified on the deployed build with the window shut in between: turn one says
"pictures of iron man", nine seconds of silence, then "show me three more images"
answers "Here are some pictures of iron man."

## 2026-09-02 — the render worked and then he could not talk to it

Three things he found in one sitting, and the middle one was the serious one.

**THE LAYOUT WAS THE CAMERA'S, NOT THE HOLOGRAM'S.** The plan asked for a full
frame: *"the core shrinks to the corner and visibly projects the model"*, with
`App.tsx` listed for "full-frame geometry, shrunken projecting core". What
shipped was the side-panel geometry the camera uses — core at 380 px, vertically
centred, at 0.85 scale, stage starting at 760 px — so the model got half a
screen. Now, for holograms ONLY, the core drops to the bottom-left corner at
0.44 and the stage spans the frame. Every other anchored stage keeps the layout
he approved for the camera.

`.column` had to go with it. It is the divider between a left-hand core and a
right-hand panel — "the core casting light on the stage" — pinned at 722 px for
that two-column geometry. With the core in the corner it stood in the middle of
the bed like a post through the part.

**HE SPOKE TO IT AND IT DID NOT ANSWER.** The part was made, JARVIS said so, and
then "thank you" and "rotate it" got nothing; he hovered the core, saw "Toggle
listening", and concluded it had stopped listening. The log is unambiguous:
wake word at 18:22:29, the plate at 18:22:35, "Go for it" at 18:22:40, and then
NOTHING until it slept at 18:25:01.

`announce()` — the path a finished render, a reminder or an alert speaks through
— never called `_arm_conversation()`. That window is only armed at the end of a
turn HE started. So JARVIS spoke to him unprompted and then required its own name
again before it would hear the reply. **Being spoken to and then having to
reintroduce yourself is not how being spoken to works.** It now arms the window
and refreshes `_last_active`, so it also cannot drift toward sleep in the middle
of a conversation it began.

His inference was right even though his evidence was not: "Toggle listening" is a
static button label, not a status. Worth remembering — the HUD has no honest
indicator of whether the wake word is currently listening.

**AND SLEEP LEFT THE STAGE UP.** A hologram deliberately HOLDS the frame while he
works — it is a thing he is working on, not an answer that has stopped being
useful — but nothing took it down on the way into sleep, so a finished part sat
projected in front of him for half an hour. Sleep now hides it; the file is still
on disk and "show me the bracket" brings it back. Asked whether it should also
time out while AWAKE, he said leave it: a part is not an answer, and snatching
one away mid-thought is worse than a stale panel.

## 2026-09-02 — silence, then a YouTube video nobody asked for

He tested, heard nothing at all, asked some questions, and got a YouTube video.
Three separate faults, and **the second one was a regression I had shipped an
hour earlier**.

**HE HEARD NOTHING because the output device was asleep.** His monitor's speakers
sit on DisplayPort; the machine had been idle thirteen minutes, the panel had
blanked, and the audio endpoint went with it:

    19:21:24  audio write hung (0.4s of audio, 5s budget) — output device is
              not accepting data; aborting, and not trying again for 60s
    19:21:25  speech aborted: the audio output device stopped responding

That handling is correct and hard-won — it aborts rather than wedging the turn,
which is the 2026-08-27 ninety-minute freeze lesson. But the 60-second deaf
window was exactly the window he was testing in. **Left alone deliberately.**

**THE YOUTUBE VIDEO WAS MINE.** An hour earlier I made proactive announcements
arm the follow-up window, so that "the plate is ready, sir" could be answered
with "rotate it" instead of another wake word. It armed UNCONDITIONALLY:

    19:21:25  speech aborted (he heard nothing)
    19:21:28  follow-up speech (conversation window)   <- armed anyway
    19:21:33  user: "Two video."                        <- not addressed to JARVIS
    19:21:52  assistant: "Two videos are playing now."

JARVIS spoke into a dead speaker, opened the microphone regardless, and acted on
a two-word fragment. It now arms **only if audio actually reached the speakers**:
if he could not hear it there is nothing to reply to, and an open microphone is
worse than a missed follow-up.

The general lesson, which is worth more than the fix: **a feature that opens a
microphone must be designed around its failure path first.** I designed this one
around the success path and shipped it the same hour.

**AND SELF-TRAINING HAD BEEN POISONED.** He never mentioned this; it was in the
same log. A wake word fired at 0.82 on him talking to someone else, the model
put JARVIS to sleep, and `_maybe_learn` wrote it down:

    brain learned: 'i was like this is the challenge wait i need to go on
                    easier.' -> sleep

A mislearned `sleep` means stray speech dismisses him. Listing every learned
example found four more that were never commands he gave, the worst being
**"show me" -> screenshot**, two words of pure scaffolding that drag every "show
me X" toward photographing the screen. Five removed; the remaining fourteen are
real commands.

`_teachable()` now refuses rambling (over ten words), multi-sentence, self-talk
markers ("I was like", "wait", "um"), and utterances made ENTIRELY of command
scaffolding with no object. **Not a minimum word count** — that was the first
attempt and it rejected "open spotify".

**No fourth bug: the phone fallback did work.** Verified rather than assumed —
`deliver()` returns `delivered: telegram, why: "could not speak"` when the
speaker raises, so the render announcement did reach him. Now gated, because the
code was only ever asking "is he present?" and never "can he be spoken to?",
which are two different questions.

## 2026-09-02 — the deaf window, and a lock that outlived its stream

He asked what the "deaf window" was. Worth writing down plainly, because the
name misleads: **it is a SPEAKING timeout, not a listening one.** When a write to
the speakers hangs, `_DEAF_OUTPUT_S = 60` stops JARVIS attempting to speak for a
minute. The microphone and wake word are a separate stream and are untouched
throughout — he can still be heard, and the message falls through to his phone
because the attempt fails instantly instead of hanging.

It exists because PortAudio's write is blocking: when the device vanishes
mid-sentence the call never returns, and without the lockout every sentence pays
~12 seconds rediscovering the same dead device with the turn stuck behind it.

**The case it was mishandling is his NORMAL state.** His monitor blanks after
sixty seconds and its speakers are on DisplayPort, so they are asleep most of the
time he is not typing — and a sleeping endpoint refuses the first write and then
WAKES when a new stream is opened against it. That is recoverable, and it was
being treated as a dead device: one refusal bought sixty seconds of silence.

So: **one retry, on a fresh stream, with a three-second budget, strictly once.**
A sleeping device is now heard; a genuinely dead one still gives up and still
goes quiet for the full minute.

**AND THE RETRY DID NOT WORK AT FIRST, which found the real bug.** The stuck
writer thread holds `self._wlock`, and that lock guarded ALL streams — so the
fresh stream queued behind the dead one and timed out too. The invariant is
per-stream ("do not close stream X while a thread is writing to stream X"), so a
lock held forever by an abandoned writer must not gate every future stream on a
device that may be perfectly healthy. `_release` now installs a fresh lock at the
moment it abandons a stream; the orphan keeps the old one and is never released
again.

Care taken, because this file froze him for forty minutes once (a lock taken from
the event loop thread) and killed the process another time (closing a stream out
from under a blocked writer): nothing waits on a lock from the loop, the retry is
bounded by `wait_for` and cannot loop, and both pre-existing audio gates pass
unchanged.

**A note on testing this file.** The first version of the new gate failed for two
rounds and both were the STUB, not the code: the fake stream had no `abort()`,
and `_ensure` was stubbed without assigning `self._stream`, so `abort()` found
nothing to abort and `_release` never ran. If a fake device does not do what the
real `_ensure` does, the test is measuring the fake.

## 2026-09-02 — barge-in, the camera he could not see, and picking a picture

Three things from a good test session, and each had a cause worth writing down.

**BARGE-IN WAS FIRING AND THEN BEING UNDONE.** He cut in, JARVIS stopped talking,
and then nothing: he had to wait about five seconds and say the wake word again.
Barge-in works — it cancels the speech, moves to LISTENING and arms the capture —
and then the turn it interrupted carried on unwinding and reached
`to(IDLE, force=True)`, wiping that a few milliseconds later.

**A finished turn may not put the state back when a newer one has already taken
it.** `_NEXT_TURN_STATES` (LISTENING, PROCESSING) is now checked at both places a
turn ends. This is a general shape, not a barge-in special case: anything that
starts a new turn from inside an old one hits it.

**THE CAMERA HE COULD NOT SEE WAS MY DOING.** Earlier the same day I stopped the
camera panel taking the stage away from a hologram, because it hid the model he
was reaching for. Correct — and it left him with no way to see the camera AT ALL
in hand mode. His transcript shows him fighting it: "Toggle camera view?" ->
"Camera off, sir." A small feed now sits in the hologram's bottom-right whenever
the camera is on, MIRRORED, so his hand moving right moves the picture right —
matching `grip_point(mirrored=True)`, so what he sees and what the tracker reads
agree.

**PICKING A PICTURE BY NUMBER — three faults.**

1. "focus on number 6" routed to the WINDOW SWITCHER at 1.00 and went looking for
   a window called "number 6". The word "focus" belonged to switching windows and
   nothing else claimed it. `slots_switch` now declines a bare number: nobody
   names a window after one. NOT fixed with seeds — "focus on number 6"
   canonicalises onto switch's "focus on spotify" (`_CANON` erases the digit) and
   the collision gate rightly refused it.
2. "image number 6" had no reflex at all. The parser understood ORDINALS ("the
   third one") and never the cardinals he actually says. Numbers and ranges now
   parse: "image number 6", "show me image 4", "number 3", "picture six",
   "just give me 1 through 4", "give me 1-4". "show me 5 images of spiderman"
   stays a SEARCH, which is the trap in that neighbourhood.
3. The `ui` skill carries no tool — it only emits an event — so the MODEL could
   not do this at all for phrasings the reflex misses. `focus_image` is that
   tool, and it returns the picture's URL so "focus on number three and give me a
   3D printout of that" has something to chain with.

**AND THE NUMBERS WERE INVISIBLE, which nearly shipped.** The tiles now carry
their number — and on the first deploy nothing appeared. Not a rendering bug:
every tile overlay (`imtile__n`, `imtile__best`, `imtile__src`) is `.mono-sub`,
and `body.compact .mono-sub { display: none }`. Compact turns on below 1600px and
**his window is about 1000px, so compact is ALWAYS on for him** — those overlays
have never been visible. Restored for the number, which is the label he reads the
command off.

**How that was caught is the lesson.** The full-screen screenshot looked fine;
the badge is 16px in a 2560px shot scaled to fit. Cropping to the grid and
doubling it made it obvious in one glance. **"I cannot see it" and "it is not
there" are indistinguishable in a downscaled screenshot** — crop before
believing either.

## 2026-09-02 — "Sure." and nothing happened

He asked for a 3D image of the Spider-Man emblem. JARVIS thought for fourteen
seconds, said "Sure.", and went back to idle. It IS one of the tiers — tier 4,
text to reference picture to mesh — so this was a routing failure, not a
capability one.

**IT WENT TO THE IMAGES SKILL.** "Create me a three D image of Spider-Man's
spider emblem" scored **0.84 for `images`**: "image of X" looks exactly like a
picture search and "3d" is one small word against a long object phrase. The image
extractor then looked at it, decided it was not a search, and returned None — so
the whole sentence fell to the model, which **had `make_hologram` in its
shortlist** (checked, not assumed) and answered "Sure." without calling it.

Three fixes, and the first is the general one:

* **`slots_images` steps aside explicitly** on any "make/create a 3d..." request.
  The router already gives the next-best skill a turn when an extractor refuses
  (`decide` retries three times with the loser excluded) — so declining on
  purpose is worth more than losing on score, because losing on score with a
  refusing extractor means the MODEL gets it.
* **Seeds for how he actually asks**: "create me a 3d image of...", "make me a
  3d print of the apple logo". 0.97-1.00 now.
* **"three D" and "3 d"**, which is what dictation produces at least as often as
  "3d", plus `image`/`picture`/`print` in the noun list. The description had been
  reaching the tier chooser as "three d image of the spider emblem" — and that
  string is then SEARCHED FOR as the reference picture. Now: "the spider emblem".

**Also in that same exchange, unasked:** "Make that image bigger" answered *"I
can't locate that image window, sir"* — the WINDOW controls took it. `slots_ui`
had always handled those words; nothing claimed the phrasing, so the embedding
sent it elsewhere. Seeded.

**Two process notes, both mine.**

The seeds I "added" the first time were never added: the script that wrote them
died on a SyntaxError before writing the file, and I read the surrounding output
as success. The symptom was a seed scoring 0.68 as an exact match, which is
impossible — that impossibility is what exposed it. **When a number cannot be
true, stop and find out why rather than adding more of the same.**

And the build after that FAILED on the delivery gate and **I deployed anyway
without reading the exit code.** No harm: the script aborts at the failing gate,
before packaging, so `dist/` still held the last good build. That was luck. The
gate was right — my delivery check was order-dependent, spending the shared
hourly ceiling that the cases above it had already used, so it measured the
ceiling rather than the deaf-speaker fallback. It resets and restores the budget
now.

## 2026-09-02 — he starts sentences twice, and spoken numbers are weaker

Two loose ends from the same session.

**A FALSE START WENT STRAIGHT TO THE SEARCH ENGINE.** "Show me th show me three
images of Tom Hall and Spider Man" was sent to DuckDuckGo as *"th show me three
images of tom hall and spider man"*. People restart mid-phrase constantly, the
recogniser has no idea it happened, and only the repetition gives it away.
`strip_restart()` now drops the abandoned opening; that query is
`("Tom Hall and Spider Man", 3)`.

**Narrow on purpose, and the first attempt was not narrow enough** — it turned
"search for how to show me the money" into "show me the money", mangling a real
query to fix an imagined one. The rule is now that whatever sits between the two
lead-ins must be a FRAGMENT or a hesitation ("th", "sp", "uh"), never words. A
length rule is not enough: "how" and "to" are three letters and mean something.
Both directions are gated, negatives included.

**SPOKEN NUMBERS WERE WEAKER THAN TYPED ONES.** "Image number four" scored 0.81
where "image number 4" scored 1.00, because `_CANON` collapses digits to `N` and
never touched number words — and 0.81 is close enough to the threshold to be a
coin toss on the phrasing he actually SAYS. Same for "three D" against "3d",
which is what dictation produced for the emblem request. Both 1.00 now.

Collapsing only happens after the word "number", so "another one" and "the
second one" are untouched — checked against `seed_collisions` and
`test_canon_erasure`, which are the two gates that catch a canon change going
wrong.

**Not fixable here:** "Tom Hall" is a mis-hear of "Tom Holland". The query is
clean now; the name is what the recogniser heard.

## 2026-09-03 — the emblem was a disc, and metrics kept saying it was fine

He asked for a 3D image of the Spider-Man emblem and got something that "doesn't
really look right". It took THREE layers to fix, and I reported it fixed twice
before it was.

**LAYER ONE — the wrong TIER.** `_FLAT` already knew "emblem" and "logo", but it
was only consulted when an image_path was given. Text-only fell through to tier
4: find a photo, RECONSTRUCT it in 3D. A flat two-colour logo reconstructed as a
solid is a blob. A flat thing is flat whether or not he handed over the picture,
so tier 2 now claims it and fetches its own reference.

**LAYER TWO — the wrong TRACE.** `RETR_EXTERNAL` discards everything inside the
outer boundary and `max(contourArea)` then keeps only that boundary, so a badge's
outer ring wins and the spider inside it is thrown away — then `approxPolyDP` at
up to 0.05 of the perimeter rounds what is left into an ellipse. `trace_shapes`
keeps every significant part WITH its holes (RETR_CCOMP), cuts them properly with
OpenSCAD `paths`, and stops simplifying six times earlier.

**LAYER THREE — the wrong PICTURE.** After both fixes it was STILL a disc: one
polygon, no holes. The tracer was fine by then; a plain image search returns
photographs, and Otsu on a photograph gives one blob. When the picture is going
to be TRACED rather than reconstructed, `reference_image(flat=True)` searches for
"logo silhouette black on white transparent png", pulls eight candidates instead
of four, and tries transparent PNGs first — the alpha channel IS the outline, and
the tracer then does no guessing at all.

Result, rendered flat from the STL: a spider. Body, head, eight legs, sharp
edges. 60 x 65 mm, 316 triangles.

**THE PROCESS LESSON IS THE BIGGER ONE.** I called this fixed twice on evidence
that could not see the problem:

  * "tier 2, 22 KB, sliceable, zero overhangs" — every word true, and it was a
    disc. None of those numbers is about SHAPE.
  * "the tracer keeps holes now" — proven on a synthetic ring, and the real
    output was still a disc, because the input picture was the problem.

What settled it was `.agent/scripts/silhouette.py`, which projects the STL onto
its own XY plane and draws it. A flat part photographed on the holo stage is seen
nearly edge-on and tells you almost nothing. **For a shape, look at the shape.**

**Also fixed in the same pass**, all from his message:

* The camera view in hand mode never appeared: it was keyed to `cameraOn`, which
  only the `set_camera` TOOL sets — and `hand_control` opens the device directly,
  so nothing ever told the HUD. It now shows whenever hands are armed.
* Hand control picked ONE axis per frame, whichever moved more, so a diagonal
  drag flipped between spinning and tipping and the model lurched between two
  motions. Both axes at once now, with eased velocity (the camera reads at 10 fps,
  so raw deltas arrive as jerks) and gain 540 -> 450. Smoothing the POINT was the
  first attempt and was wrong: the smoothed point lags, so a hand held still kept
  turning the model. The deadzone reads the RAW movement; only the response is
  eased.
* The follow-up window is 40 s while a model is on the stage, 5 s otherwise.
  Turning a part, looking at it and thinking takes longer than asking the time.

## 2026-09-03 — the audit, and the join nothing was watching

He asked for an audit before I called the work finished: *"audit the code. To
ensure performance, productivity. And. Enhancement."* Seven real defects, each
gated, and every gate made to FAIL before it was trusted.

**The to_thread that wasn't.** `components.py` read as though the mesh join had
been moved off the event loop:

    await asyncio.to_thread(
        write_stl, np.concatenate([load(p) for p in placed]), path)

Arguments are evaluated before the call, so every load ran on the loop and only
the write went across — six components at up to 400,000 triangles each. This is
the worst shape the bug comes in: it reads as handled. `model_find.describe`
had the same problem inline in an async fetch over a just-downloaded 120 MB
mesh. An AST pass over every `async def` in the sidecar found no others.

**Per-part renders were serial.** Independent processes, distinct output files,
one source nobody writes to, sixteen cores. Now gathered behind a four-lane
semaphore — bounded, because llama-server holds the GPU and most of a working
set on this same box. Measured: 5.78s serial, 4.93s at four lanes, and six
lanes buys nothing. `gather` preserves input order, which is the property that
could silently rot, so that is what the gate asserts.

**A project called NUL was the null device.** `mkdir` returns no error and
`is_dir` is then False. Say "start a new project called null" and JARVIS would
read the name back, confirm it, and pour every model and note into nothing with
no error at any layer. Measured rather than assumed, which changed the fix: only
the BARE name breaks here — `nul.suit` and `con.x` are real directories.

**The side view was drawn at twice the scale of the front view.** 7.28 px/mm
against 14.57 on the same sheet. A front/side/plan set exists to be read
across, and it is what he judges a physical print from. One scale now, from the
model's largest dimension rather than each face's.

**Raising the draw cap had only moved the speckle.** 400,000 was still too low:
a 766,322-triangle sphere strided by two and came back pinholed. There is no
count at which a stride stops perforating, so the limit went where nothing real
reaches it — `meshio.MAX_BYTES` caps a binary STL at 2,399,998 triangles
anyway. The old gate asserted `<= 1_000_000` because "a million through PIL is
minutes"; a million is 13.9s, so the reasoning was wrong and the bound with it.

**A tight crop made the picture frame the subject.** The tracer picked its
background by majority over the whole image, and a logo reference is usually a
tight crop — a subject filling 55% IS the majority. So his own example, a red
mask with white eyes, traced the four corners of the picture as the "outline"
and sampled #ffffff, with the real face labelled a hole inside it. The frame
edge is the reliable witness. Checked against all four polarities the tier
sees.

**And the one that mattered most.** I ran the whole morning's feature list as a
checklist instead of trusting it was there. Twenty of twenty-one held. The miss
was the feature he described most concretely: *"pull up Spider-Man suit Mark
2"* could not find the Spider-Man suit, because the folder is created from one
transcription of his voice and recalled from another, and the match was a plain
substring test that one hyphen defeats. Underneath it, `workspace.note` creates
the folder when absent — so noting something under "spiderman suit mark 2" made
a SECOND folder beside "Spider-Man suit Mark 2" and wrote into the empty one.
His design log would have split in two silently.

Nothing was broken in isolation. Recall worked, notes worked, folder naming
worked. The JOIN between two working pieces was broken, and no per-module suite
was ever going to look there. That is why the checklist is now a suite —
`tests/test_feature_set.py`, 28 checks, deliberately shallow per line and wide
across features, gated in `build_sidecar.cmd`.

**On the release build.** It failed three suites — `hands_e2e`, `clarify_e2e`,
`soak_e2e` — and the leading suspect is me: the e2e window was 09:35-10:01,
exactly when the audit was rendering 766k-2M triangle meshes and running
concurrent OpenSCAD. An 86-second first token is starvation, not logic, and the
soak's "+19.4 MB/min leak" ended at 1668 MB, BELOW the 1780 MB it started from
— a real leak does not give memory back. Re-run on a quiet machine before
believing either the failure or this explanation. **Never run the suites while
anything else is using this machine.**

## 2026-09-03 — OPEN: hands_e2e's receipt, and what it is not

Re-run on a quiet machine, `hands_e2e` fails the SAME way, so unlike soak and
clarify this one is not contention. It is also not a broken capability, and the
difference matters:

    PASS  dictation transcribed what was said
    PASS  and pasted it into the focused window
    FAIL  Windows itself reports the dictated words are in the document
    PASS  clicking 'Add New Tab' by name succeeds
    PASS  and a new tab really appeared — the click did something

Dictation works. Click-by-name works and has a visible consequence. What fails
is the RECEIPT: the test reads the control tree back and looks for the dictated
words in a control's name, on the premise that "Windows names the tab after
what is IN it".

That premise looks stale. Notepad on this machine is now the rich-text version
— the tree carries Bold, Italic, Strikethrough, Table, Clear formatting,
Writing tools, What's new and User avatar — and on a clean launch its tab reads
`Untitled. Unmodified.` rather than anything about the content.

Two things I got wrong on the way, both worth knowing:

  * The six `PopupHost` entries in the failure detail are NOT the whole tree.
    The check prints `tabs[:6]`, so they are only the first six names of a
    longer list. A direct `list_controls` on Notepad returns 22 healthy
    controls including the tab item. The popups are incidental: `_collect` in
    `tools/uia.py` deliberately adds same-PID on-screen popup windows as extra
    roots (capped `roots[:6]`) so that an open menu's "Save as" is reachable,
    and XAML Notepad keeps several flyout hosts around.
  * I tried to confirm the premise by typing into Notepad and watching the tab
    name. `type_text` came back `{}` through `/debug/tool`, so nothing was
    proven either way. NOT concluded — do not repeat the claim without
    finishing this step.

So: probably a stale test receipt caused by a Windows app update, not a JARVIS
regression. Deliberately NOT "fixed" by weakening the assertion — the honest
repair is to give it a receipt that is true of the current Notepad, and the
sidecar has no tool that reads a control's VALUE (only its name), so that is a
small new capability rather than a test edit. Left open on purpose.

## 2026-09-03 evening — he watches the model resolve

His words: *"I actually wanted to see the 3D model being built"*, and then, after
seeing the reference-scan preview: *"What if we did both? What if we used the
first renderability where it, like, scans the picture, and that's how it's
working, and then it goes into watching the model build?"*

**The measurement decided the design, and it was not what I assumed.** A TripoSR
reconstruction is two steps: `codes = model([img])` — the transformer — then
`model.extract_mesh(codes, resolution=N)` — marching cubes. I assumed the
transformer dominated, which would have meant a progressive build showed him
five stages in the last few seconds of a three-minute wait and the reference
picture was doing all the real work. Measured on his machine:

    grid  64   think 17s + carve   0.3s
    grid 192   think 15s + carve   7.9s
    grid 384   think 15s + carve  54.0s
    grid 512   think 15s + carve 113.7s

The transformer is FLAT at ~15 s and carving is CUBIC. At 384 the carving is 78%
of the wait — so most of the wait is a phase where real geometry exists and can
be shown. **If I had built to the assumption I would have built the wrong thing.**

The scene code is computed once and a mesh can be pulled from it at any
resolution, so `96 -> 192 -> 384` is three genuine meshes of the same
reconstruction, not interpolations. Measured end to end:

    rung  96   on screen at 17.5s    10,204 triangles   58.5 x 58.5 x 60.0 mm
    rung 192   on screen at 23.9s    42,750 triangles   59.7 x 59.6 x 60.0 mm
    final      on screen at 75.3s   172,560 triangles   59.5 x 59.3 x 60.0 mm

against a 73.2 s baseline with no ladder — about two seconds, inside the
run-to-run noise, and he sees the object 58 seconds sooner. 288 was rejected:
another 23 s for one more step.

**The bounding box is the check, not "the file appeared."** `finish` stands the
mesh upright, fixes inside-out winding and scales to millimetres. Factoring it
out for the rungs left it referencing the argparse namespace (`a.size_mm`), so
every rung raised NameError inside a `try` and was logged as a failed preview —
silently, because a preview must never cost him a render. And the first live
check reported *"final at 79.4s"* for a render that had died, because anything
that was not a stage was taken for the answer. Each rung is now measured against
the finished part: 1.7% and 0.5%, which is a coarser silhouette of the same
object rather than one that skipped being stood upright.

**Streaming a subprocess needs both pipes drained.** `_run` used
`communicate()`; reading only stdout leaves stderr to fill its 64 KB buffer and
the child blocks forever holding 1.7 GB of weights — TripoSR and rembg both
write progress bars there. It reads chunks rather than lines for the same
reason: a progress bar is carriage returns with no newline, and `readline` on
one raises at the stream limit. The kill-on-cancel and `CREATE_NO_WINDOW`
behaviour was carried over deliberately.

**"Show me that again" with no name means the newest thing you made** — and a
rung is newer than the part it previews. Excluded from `_pick` AND cleaned up
after every render. Two guards, because a preview passing for his part is not
worth risking once.

Progressive is on only for a render he asked for **as a whole**
(`render_tools` passes `progressive=True`); the per-part builds inside a
composite would otherwise take turns on the stage. The panel shows
`resolving · 96` while rungs are landing.

### Finding 24 — the machine panel was never blank, it was zero

`/system` and `snapshot()` were both checked and healthy (real numbers in
0.04 s), so it was written down as HUD-side and left there. `useMachine` lives
in `RunStrip`, which is part of `ProseStage`, **which mounts and unmounts with
every answer**. With `[]` deps the reading started from nothing each time: first
paint had `sys === null` and the panel rendered that as `CPU 0%`, `MEMORY — / —
GB`, `DISK 0.0 TB FREE`. A short answer drained before the fetch landed. The
backend was healthy throughout, which is exactly why checking the backend never
found it.

The reading now lives outside React — one shared poll, last value kept between
stages — and the panel says `NO READING` with the reason rather than `?? 0`.
**Diagnosed by reading, not reproduced**: a cause that fully explains the
symptom, not a confirmed one. It will now say what is wrong if it recurs.

### Two traps that cost time today, both already in memory

  * **Heredoc backslash mangling.** `\\n` and `\\S` collapse inside a bash
    heredoc even with a quoted delimiter. It bit four times. Use the Write/Edit
    tools for anything containing regex or escapes — no exceptions.
  * **`.cmd` files need CRLF.** Rewriting `build_sidecar.cmd` through `awk`
    converted it to LF and the build died with `'M' is not recognized as an
    internal or external command`. The repo stores LF and autocrlf supplies the
    CRLF; a tool that rewrites every line takes it away.

And one new one: **`tests/` is `sidecar/tests/`.** `build_sidecar.cmd` does
`cd /d "%~dp0..\sidecar"` first, so a gate written to the repo-root `tests/`
is never run by the build even though it passes when invoked by hand.

### Still open
  * **#25 raise the window on a finished render** — his open question, not a
    bug. A render finishing already puts the model on the stage, which is
    surfacing it; raising the window above other apps is more intrusive and is
    his call.
  * **Spotify playlists** — needs an OAuth app registered under his account.
  * **"Nvidia" → "bye"** — an STT limit; wants a finance-domain hint.

## 2026-09-04 — the dark room, and input that reports success and does nothing

### What his overnight testing actually was

**The camera was never the render's fault.** He asked whether watching the model
build was worth the camera cost. Measured across a full render, the stream holds
28-30 fps throughout; a cold start reaches 24 fps in four seconds. It is the
room: on his camera at 06:40, mean brightness 36.9/255, a third of the pixels
near black, sharpness 13.8. Hand tracking keys on local contrast, so at 13.8
there is nothing to track. Every device control is ignored (exposure, gain,
brightness all accept a value and keep the old one), so the fix is CLAHE on luma
after capture, applied before presence and hand tracking, only while dark, with
hysteresis. 13.6 -> 94.9 sharpness for 5.7 ms; 30 fps confirmed on the install.

I FIRST BLAMED THE VISION MODELS and was wrong. The "25-second ramp from 4.8
fps" was an artifact of computing fps from status counters instead of counting
frames off the stream. The four models load in 0.93 s total. The preload still
moved off the capture thread, for the honest reason that the thread producing
frames should not be doing anything else.

**A typed yes could not answer a question.** The inline Telegram buttons carried
the confirm_id and were the only thing that could. Typing "Do it!" queued a new
turn, which waits for idle — and idle never comes, because a tool is blocked on
the confirmation he just answered. Now checked before the turn lock.

**"press enter" had no skill**, so it fell through to the LLM, which answered
with the time; and "press enter to send it" matched `to_phone` at 0.855, over
threshold, so it would have messaged his phone. Now `press_key` at 1.0.

### OPEN, and the important one: synthetic input reports success and does nothing

`press_keys` returned `{"pressed": "h", "window": "*... - Notepad"}` and the 'h'
never appeared. Dictation's `_paste` behaves identically: `pasted: True`, empty
document, sampled out to 3.5 s. The SAME win32 calls from a test process land
in the SAME window immediately.

Ruled out, each by measurement:
  * integrity level — sidecar, HUD, Notepad and the test process are all `medium`
  * focus — Notepad is confirmed foreground before, during and after
  * the clipboard restore race — real (0.35 s loses it, 1.5 s keeps it) and fixed,
    but not sufficient on its own
  * a hang in `_focus` — the EnumWindows/GetWindowText sweep is 0.00 s. What
    looked like a 40 s hang was the MEDIUM risk gate waiting for a confirmation
    my probe never answered. `press_keys` returns in 9.8 s once approved.

**The live hypothesis, untested:** every JARVIS instance I have driven today was
launched by a `schtasks` task (hotswap and release both do this). A scheduled
task's process can differ in window-station/desktop association, which is exactly
what governs input injection. When HE launches from the shortcut it may work
fine. Testing that needs a normal launch, which only he can do — so the next step
is to ask him to launch JARVIS himself and try dictation once, rather than to
keep changing code against a test environment that may itself be the fault.

Note also: `session.token` is only written under `JARVIS_DEBUG=1`. A production
launch publishes none, so authenticated endpoints 401 while `/health` answers.

## 2026-09-04 afternoon — the full audit

His instruction: *"Audit the code base fix everything that needs to be fixed and
then perfect every feature."* Six read-only subsystem audits ran in parallel
(brain, tools, audio/camera/vision, 3D, remote/main/persistence, HUD) against a
baseline silent release that was RELEASE OK. Roughly seventy confirmed findings;
what follows is what changed and why, by the thing he would have hit.

### Telegram — why yesterday's transcript happened
The poll loop handled each update INLINE. While a remote turn sat 120 s waiting
for his DO IT tap, `getUpdates` was never called again, so the tap and the typed
"Do it!" sat in Telegram's queue until the question had expired: "That question
expired" → "I didn't get a yes" → two stray "Done, sir."s. Yesterday's typed-yes
fix was correct and never executed in production. Updates are spawned now
(`_turn_lock` still serialises turns), so an ANSWER can arrive while a command
waits for one — and the offset is acked on the next poll, so a restart mid-turn
no longer replays the command.

Also: a failed send was filed as delivered (budget charged, text remembered as
told); the bot token was written to sidecar.log twice (httpx at INFO before the
loop silenced it; `raise_for_status` tracebacks carry the URL); a typo in
Settings overwrote the working token before Telegram was asked; an EDITED
message re-ran as a new command; a voice-note "yes" queued behind its own
question; a turn that raised held the bridge 240 s then said "Done, sir.";
`config.save` was not atomic and a torn file was overwritten with defaults on
the next boot (pairing, watchlist, rules gone — kept aside as
`config.json.bad-<stamp>` now).

### Brain
Six skills had `speak=None` and every one answered "Done." — "what projects do
we have" got "Done." while the list sat unread in the tool's `spoken`.
`GUESS_YES` matched the opening word only, so after "Did you mean lock, sir?"
the sentence "okay, what's the weather" locked the PC and learned the phrasing
as `lock`. A bare "no" to a guess fell through to the `correction` skill and
UNLEARNED whatever reflex had fired in the last 40 s. A cost question ("Shall
I?") was answered by word-counting, so "go to sleep" and "turn on the camera"
counted as "go ahead" and started renders (now: an anchored yes/no, or an
utterance made entirely of one branch's words plus filler — "carry on", "leave
it" — and "never mind" to a cost question is a decline, not a dropped
question; the first cut of this crashed on `Branch.name`, which no gate
covered, so `test_clarify` now has an approval section). Learned examples for number-carrying
skills were deleted on every boot (`learn` validated raw text, `load`
re-validated the normalised "N" form). "close this tab" hid the HUD; "open the
start menu" opened settings. Seventy skills would ask "Did you mean wakeack,
sir?" (identifier read aloud; the gate iterated the table so could not fail).
"Jarvis, yes" during a confirmation was never captured. "Go to sleep" re-armed
the 15 s window while asleep. "remind me in an hour" refused itself.

### Tools
File scans/reads/moves ran ON the event loop inside `async def` handlers (a
six-second walk = six seconds deaf). The audit INSERT ran on the loop with a
15 s busy timeout. `press_keys`/`type_text` hopped through the shared default
executor. `_focus` took `hits[0]` from a raw EnumWindows that includes CLOAKED
ghost frames (modern Notepad leaves them) and reported focus it never got. Every
keystroke went through `keybd_event` with scan code 0, a BYTE for Unicode (curly
apostrophe → OverflowError), and no regard for his own fingers — `keys.py` is the
replacement: SendInput, real scan codes, WORD Unicode, and it WAITS for physical
modifiers to lift (dictation is hold-to-talk on Ctrl+Shift+D; the paste fired
while they were still down = Ctrl+Shift+V). `open_with_windows` (LOW) ran .exe
and .msi. `browser_click` (LOW) submitted forms the MEDIUM tool gated.
`delete_file` recycled whole trees and, over the bin limit, deleted permanently
with FOF_NOCONFIRMATION answering yes (now refuses >512 MB). An empty query
cancelled every reminder. `exit_sleep_mode` could be called from a phone turn
and lit the monitor. DPI awareness was never declared (fallback clicks at
two-thirds of the target on scaled displays). A HUD/phone answer waited up to
8 s behind the voice listener (hook now races the future). Handler TypeErrors
were relabelled "bad arguments".

### Audio / camera / vision
`refresh_devices` called `Pa_Terminate` while an orphaned stream could still
have a writer inside it (the no-traceback crash); the whole heal ran on the
loop; a failed mic reopen was a DEBUG line and never retried (`using_preferred`
never reset). Dictation's 120 s guard stopped recording but not the session
(deaf to his name; next release pasted two minutes). YuNet/SFace shared across
three threads with no lock. `play_chunk` under silence still set `heard` and
opened the mic. Camera `start()` said ok on an open that merely timed out and
could run two capture threads. Pre-roll snapshot taken AFTER surfacing (clipped
first syllable).

### 3D
Tier 6 recursed forever: every piece of a "suit" still contains "suit", so each
component routed back to 6, and the no-components fallback re-picked 6.
`choose_tier(..., exclude=)` now; `build_each` never splits a piece. Scout's
confirmation carried the pre-scout tier and the scouted photo regardless of
route: "yes" to a fetch generated from scratch, "yes" to an emblem traced a
photograph (his emblem regression, by voice). The contract now: a fetch is tier
5 and the found model travels as `scouted_model` (tier 5 tries THAT repo
first); the scouted picture travels as `reference`, which only tier 4 reads
(and tier 5 falling back to 4) — never as `image_path`, which means "a photo
HE supplied". `test_scout` proves both tiers use what they were handed and
that "find another design" still looks again. `_pick()` with no name chose a
sub-part (newer than its whole). Tier 2 built colour parts twice, once before
the body existed. A tier-2 part was named after its reference file. Scout's
reference JPEG was never deleted (15 in his folder). `unit_scale` was computed
and never applied ("I'd scale it 25.4 times; that came out 4 by 2 by 0 mm").
"Stop" during the started-announcement cancelled nothing. A finished
reconstruction slow to exit was reported as a 900 s timeout. Gates hit the live
network and wrote into his REAL work folder (now hermetic; a termination gate
exists and fails on the recursion).

### HUD
Every event-opened stage inherited `pinned` from the outgoing stage, so a search
during a hologram replaced the model with a browser panel that never drained,
and "turn it" spoke to nothing. Fixed with `keepPin` (same-kind only); a model
command reopens the model. After "give me 5 to 8", the grid renumbered 1..4 while
the sidecar counted 5..8 — "image number 6" showed one picture and handed the
model another. The machine panel is HIDDEN in compact, and his window is always
compact: yesterday's fix was invisible. Offline never cleared a cached hologram.
The CONVERSATION badge outlived the window by up to 15 s. Hands badge stayed lit
after the tracker stood down silently. `api()` had no timeout.

### Shell
`taskkill` without CREATE_NO_WINDOW flashed a console on every close/restart.
The supervisor gave up silently after three restarts. `/secrets` accepted any
name. Ctrl+Shift+S was a GLOBAL hotkey (Save As stolen from every app): his
answer was "do whatever you need to do", so it is gone — Ctrl+Shift+J is now
both directions (pressed while the ears are open or the window is armed,
`toggle_listen` stands down; `stand_down` clears the capture flag too).
`free_port()`'s bind-then-release race is closed at the restart: `restart()`
test-binds the old port, moves to a fresh one if it is taken, `Sidecar.info`
is behind a Mutex with an `info()` accessor, and the HUD drops its cached
port on every socket close so it re-asks the core before reconnecting.
voice_ux_e2e T2 passed in release 13 (the wake-fire ordering fix, most
likely); it was never reproduced after that.

### The voice (2026-09-04 evening)
He asked for a JARVIS that "sounds even similar to the movies". Not a clone of
the actor — a voice built from the pack: `tts.voice` now accepts a BLEND
("bm_george:0.6+bm_lewis:0.4"), a weighted sum of Kokoro style vectors, and
British voices are finally phonemised as British (`lang="en-gb"`; they had
been en-us all along), with a `tts.sentence_pause` (0.3 s) for the measured
delivery. Eight candidates were synthesised and sent to him as an audition
file with measured pitch and pace; George+Lewis (126 Hz, 155 wpm) is the
default until he names a number. `test_voice_spec` gates the parsing.

### The voice, second act: Pocket TTS (2026-09-04 evening)
"Daniel is still the best but I was hoping for better." The Kokoro pack was the
ceiling, so a second engine was auditioned on this CPU (Ryzen 7 8845HS, no
CUDA): **Kyutai Pocket TTS** (~100M, MIT, `pip install pocket-tts`, torch CPU)
runs 3x realtime — same as Kokoro — but STREAMS: first 80 ms of audio 80-125 ms
after the request, against Kokoro's whole-sentence 0.5-1 s. Chatterbox Nano
(3x realtime claimed) was installed twice and never exposed its `nano=True`
loader; dropped. Pocket's zero-shot cloning needs gated HF weights (terms he
would have to accept himself); its catalogue voices do not. He picked
**George**, then variant 2 of five tunings (`temp=0.5`), "a little faster"
(tempo 0.97 — plain resampling, so slightly higher too), and ruled on
"scheduling": eight seeded takes, takes 1 and 2 right. Measured on the word's
own span (faster-whisper word timestamps → log-mel DTW against the two he
approved vs the six he did not), 1-2 say "sked-", 3-8 "shed-"; the hyphenated
respelling "sked-juling" lands the approved way under most seeds and the
recogniser still hears "schedule" for every form. His verdict: "voice is
basically perfect and locked in."

How it is built: `audio/pocket_worker.py` runs under `C:\AI\tts\pocket`'s
interpreter (torch is not bundled — same pattern as `C:\AI\model3d`), one TCP
connection per utterance on loopback, JSON request line in, length-prefixed
int16 frames out, hang up to cancel; `--fake` runs the identical protocol with
a tone so `tests/test_pocket_tts.py` gates framing/streaming/cancel/tempo/
polish on a machine with no Pocket. `audio/tts.py` `PocketTTS` starts and talks
to it; the router routes a bare voice name to Pocket, a `bm_`/`bf_` name to
Kokoro, `en_` to Piper, and falls back pocket → kokoro → piper. Config
`tts.{engine,voice,tempo,seed,pocket_temp,polish,pronounce}`; migration v6
switches a saved Kokoro voice to George when the worker exists and keeps the
old one as `kokoro_voice`. NOTE the scipy import had to move to module level in
the worker: imported inside the request coroutine it stalled the loop for good
(no exception, no stderr) — a real Windows-specific trap, cause not chased.

### Also this evening
* **Deaf after a clarifying question** (6× in two days in the real log: "stuck
  in processing for 35s… recovering to IDLE" right after "the company or the
  stock, sir?"). `_NEXT_TURN_STATES` guarded turn-ends by LOOKING AT THE STATE,
  and a turn's own state is PROCESSING, so a turn that ended without passing
  through SPEAKING left itself there. Replaced by a turn GENERATION
  (`_begin_turn` / `_newer_turn_started` / `_turn_is_current` /
  `_settle_idle`, contextvar-carried); `test_wake_display` gates it with a real
  barge-in sequence.
* `/stock/price-target` is not on the free Finnhub plan: 560 warnings, each
  with the key in the URL. A 403 is remembered for the process.
* Log noise: phonemizer "words count mismatch" (7,836 lines) and asyncio's
  proactor `_call_connection_lost` (123) filtered at source.
* `tests/speech_symbols.py` now accepts the recogniser's own "$40"/"£25"
  normalisation (the audio says "forty dollars"; the STT writes it back), and
  runs the clock-duration comparison under Kokoro explicitly — Pocket samples
  its timing, so "2 oh 4" and "2 hundred 4" land within 80 ms of each other.
* **Protocols** (`brain/protocols.py`): the film's idiom over the routines he
  already teaches. "Initiate the lockdown protocol" / "engage protocol
  lockdown" / "lockdown protocol, now" all run the routine taught as "lockdown
  protocol" (matched by name before the 0.92 embedding threshold); one he never
  taught gets "I don't have a lockdown protocol yet, sir. Tell me what it
  should do — say 'when I say lockdown protocol, do…'"; "what protocols do I
  have" lists them (skill `protocols`). Gated by `test_protocols`.
* **Sticky tool shortlist** (`tools/shortlist.py stable_order`): the real
  llama-server log showed ~800 prompt tokens / ~3.3 s of prompt processing on
  an ORDINARY turn (p50) against ~100 when the prefix holds — the per-turn
  shortlist changed the tools block, and everything after it was re-read. A
  tool once offered now stays offered, in first-seen order, new ones appended,
  cap 48 with least-recently-wanted eviction from the end of the block. Gated
  by `test_shortlist_sticky`; the live effect is to be measured with
  `scratchpad/latency_bench.py` on an idle machine.
* **Arithmetic is a reflex** (`brain/mathskill.py`, skill `math`): "what's
  17 times 23" was a 17-second model round on the idle bench; he said "that
  should be instant". Number words, percent-of, powers, roots, halves,
  decimals, precedence; a recursive-descent parser, never `eval`; refuses
  anything that is not clearly a sum ("volume to 50 percent", "remind me in
  5 minutes"). `test_math` gates 30 sums, 18 non-sums, and the routing.
* **THE 16-SECOND FIRST TOKEN.** Measured on an IDLE machine against release
  16: reflex turns answer in 0.7-2.2 s, but every LLM turn ("who directed
  jaws", "17 times 23") took 16-24 s to its first token. The llama-server's own
  log had the reason: `n_slots = 4` (this build's default), tasks landing on
  slots 0/1/2/3 in turn, and `prompt eval time = 13971 ms / 4066 tokens` —
  the WHOLE prompt, every time, because the cache is per slot. The handoff's
  "2.5-4.5 s on cached prefix" had quietly become 15 s at some llama.cpp
  upgrade. Fix: `-np 1` in both GPU model arg lists (mirrored into his config
  on load).
  Release 17 measured (idle): `n_slots = 1` confirmed; math reflex 2.4 s
  (was 17.6 s); but "who directed jaws" / "octopuses" still 16-17 s, prompt
  eval 4,217 / 4,594 tokens. Then the tell: "who wrote hamlet" twice → 733
  then 128 tokens (cache HIT), "who painted the mona lisa" right after →
  4,506 (MISS). The sticky block was capped at 48 with no low-water mark, a
  new question brings up to 30 tools, so it evicted on nearly every distinct
  question and every eviction broke the prefix. Release 18: cap 72, one cut
  to 48, never dropping what the turn asked for (`test_shortlist_sticky`
  gates the hysteresis).
  Release 18 measured: STILL trimming ("tool block trimmed to 48" once a
  minute in the real log — a question brings up to thirty tools, so 48→72 is
  one question wide) and the trimmed turns re-read 4,217-5,899 tokens
  (15-21 s); turns without a trim hit (peru 2.7 s, hamlet 4.4 s, octopuses
  8.2 s). Release 19: NO eviction in practice (cap 10,000), the boot warm
  primes the exact block a session starts with (`shortlist.warm_block`), and
  gpt-oss-20b gets a 20,480 context for headroom.
  Release 19 measured: plain turns now HIT (peru 2.7 s, jaws-again 2.8 s,
  octopuses 3.1 s, down from 16-24) — but the turn AFTER a factual answer
  still missed (mona lisa 22 s, hamlet 7.8 s): `brain/facts._classify_timeless`
  runs a small model call with its own prompt after such answers, and with
  one slot that call REPLACES the conversation's cache. Release 20: two
  slots (`-np 2`, context 32,768 so each keeps 16k), the conversation pinned
  to `id_slot` 0 (`_llm_with_tools`, `_warm_prompts`), every other
  `local_llm.stream` call defaulting to slot 1; `llama.learn_slots()` reads
  `/props` so a one-slot server (gemma) simply omits the field.
  `test_llm_slots` gates it with a faked HTTP client.
  Release 20 measured: same-shape turns 1.5-3.5 s (peru 2.8, jaws-again 2.7,
  jupiter 3.0, octopuses 3.3, hamlet 4.6); side calls on slot 1 confirmed.
  Two turns still re-read 5k+ (21-22 s): both were GENERAL-knowledge reflex
  turns, which the model composes WITHOUT the tools block
  (`_no_tools_first` → `round_tools=None`), and a prompt without the block
  shares only the system prompt with one that has it — two shapes, one
  cache. Release 21: the no-tools shape runs on slot 1, the tools shape on
  slot 0; each keeps its own prefix.
  The boot warm primes each shape on its own slot the same way.
  Release 21 measured (idle, six knowledge questions in a row): hamlet 4.3 s,
  mona lisa 3.6, peru 2.4, jaws 3.0, jupiter 2.4, matrix 2.5 — EVERY model
  turn hits. The bench's very first no-tools turn after the suites (11.3 s)
  is the one re-read the side calls on slot 1 can still cause; a third slot
  would remove it at the cost of another 16k of context.
  Where the evening started: 16-24 s to the first spoken word of any model
  answer. Where it ends: 2.4-4.3 s, with arithmetic at 2.4 s as a reflex.
  Still open: the pure-reflex first-audio floor (~0.6-0.9 s with Pocket;
  needs timing inside `PocketTTS.synthesize_stream` to attribute).
  Reflex first-audio is 0.75-2.2 s and is mostly the TOOL (cpu sampling,
  weather fetch); the pure-reflex floor with Pocket is ~0.8 s and is the next
  thing to look at (worker request + speaker start).

### 22:35 — "the app is frozen", and the voice that was never live
He reported the app frozen. The sidecar answered `/health`, the HUD's socket
was ESTABLISHED, `jarvis.exe` reported Responding — but the HUD renderer
had burned 257 s of CPU in 25 minutes and, after a relaunch, sits at ~14%
idle. Not attributed (no devtools on the installed build); the relaunch
(Stop-Process + `jarvis_relaunch.cmd` through a one-shot task) brought it
back. Watch the renderer's CPU on the next session; if it climbs again the
suspects are the reactor's rAF loop and the reconnect path in
`src/lib/sidecar.ts`.

The real find in the log: **`pocket tts ready` appeared ZERO times all
evening.** The worker is RUN AS A FILE by the C:\AI interpreter, and
PyInstaller does not ship `.py` sources — `Path(__file__).with_name(...)`
pointed into a bundle where the file did not exist. Every sentence spawned a
python that died at once ("worker said ''", 1,077 spawns), then fell back to
Kokoro `bm_daniel`. So everything he heard live tonight was the fallback; the
Pocket George he approved was only ever the audition files. Release 22 ships
`audio/pocket_worker.py` as a spec data file, `PocketTTS.worker_path()`
looks beside the module and under `sys._MEIPASS/audio`, stderr is captured
into the error, and a failed start backs off 60 s. `test_pocket_tts` now
checks the spec line.

Also release 22: **the app icon is the arc reactor** — drawn by
`.agent/scripts/make_icon.py` in the HUD's own palette (#27c7ff on the dark
well, twelve coils, three spokes), PNG sizes plus an ICO with BMP entries
(Explorer does not always render PNG-compressed ICO frames; the shortcuts
point at `jarvis.exe,0`).

## 2026-09-05 overnight — the detailed render, and small JARVIS things
His brief before bed: "improve Jarvis even more, especially his 3D rendering
ability and then his overall JARVIS feel and ability… continue silently
overnight and ensure your testing does not even turn on my display."

### 3D: Hunyuan3D-2mini on the CPU, as the DETAILED render
Researched the 2026 field: TRELLIS 2 (4B, MIT) and Hunyuan3D 2.1 lead on
quality but want 10-16 GB of CUDA; SF3D's weights are gated. On this machine
(Ryzen 7 8845HS, 780M, no CUDA) the runnable candidate was
**Hunyuan3D-2mini** (0.6B DiT, ungated, shape-only). Installed under
`C:\AI\model3d\hy3d` (torch CPU) with the repo at `C:\AI\model3d\Hunyuan3D-2`.
Measured: 30 steps / octree 256 = 18.4 min (the volume decode at 256 is 15 of
them); 20 steps / octree 128 = 5.3 min end to end on the mug photo. Quality:
on the probe picture TripoSR returned an 18-unit-thick blocky relief and
Hunyuan a smooth, fully volumetric figure (`.agent/shots/look-hy3d_probe.png`
vs `look-probe.png`). On the coffee-mug photo (`look-hy3d_mug.png` vs
`look-triposr_mug.png`): Hunyuan a clean watertight cylinder with a proper
handle in all three views; TripoSR a shell that is right from the front and
torn and skewed from the side. So it is the DETAILED render, never the default:
* `C:\AI\model3d\hy3d_to_mesh.py` — same protocol as `photo_to_mesh.py`
  (stage JSON lines, then the final object), latents once and the mesh
  carved at 64 → 128 → final so he watches it resolve; FlashVDM decoder when
  it loads; Y-up rotated to Z-up like TripoSR's.
* `create3d.detailed_python()` / `available()["detailed"]`; `from_photo(...,
  detailed=)` runs it under its own interpreter (`_run_model3d(python=)`),
  threads through `from_text`, `build`, `render_tools.make_hologram`; a
  request for a detailed one on a machine without it runs the ordinary
  reconstruction and says so.
* The adjective is the signal: `brain.skills._DETAILED` ("detailed", "high
  quality", "proper", "in full detail", "take your time and…") sets
  `detailed=True` and is kept out of the object's name; `_MAKE_STRIP` learned
  "render" as a verb.
* Its own clock: `render_estimates.SEED[8] = 420` s, filed under 8 by the
  queue when the result says `detailed`, so five minutes never becomes the
  estimate for an ordinary photo render. The cost question fires as for any
  long render. `test_detailed` gates all of it offline.

### Feel
* `brain/persona.py`: the bare wake word is answered "Yes?" / "Sir?" / "Yes,
  sir?" / "Go ahead." / "At your service." rotating, never twice running; after
  six hours away the first word is a greeting by the time of day.
* Speech timing lines in the log ("speak: first chunk of … after N ms" /
  "… to the speaker after N ms") to attribute the 0.6-0.9 s reflex floor.
  Release 24 measured: first chunk 105-136 ms for a fresh sentence, 0 ms for
  a cached phrase, and the consumer plays it within 1-2 ms — first_audio on
  reflexes 46-205 ms (was 600-1,900). The floor did not reproduce; the
  earlier benches ran within minutes of a hot-swap, while `warm_phrases` was
  still holding `_synth_lock` for one phrase at a time (~0.4 s each), which
  is the likeliest explanation. Done in release 26: under Pocket the warm
  covers only the sixteen shortest lines, a second apart (`warm_phrases`).
* **"While you were away."** `delivery.deliver` now records every proactive
  outcome (spoken / telegram / held / budget, subject, text) in
  `delivery.ledger`; `persona.briefing()` turns the entries since he last
  spoke into one reporting sentence ("While you were away: 2 things reached
  you — the market brief, Your dentist is at 4 tomorrow; one thing I held
  back."), the greeting after six hours away carries it, and "what did I
  miss" / "catch me up" / "anything while I was gone" is a reflex (skill
  `briefing`). Gated in `test_persona`.

### 01:55 — release 25 failed three live suites, and it was the model server
research_e2e's eclipse question hung 302 s (TOOL-ERROR), facts_e2e's web
turn never answered, endpoint_e2e heard nothing: the sidecar log shows six
"stuck in thinking/speaking/listening for 35 s" recoveries and `turn
failed: httpx.ReadTimeout` (the 300 s read timeout on the llama stream). The
llama-server log ends in an infinite loop — `slot create_check: id 1 | task
432 | erasing old context checkpoint (pos_min = 1484, pos_max = 1611…)`
sixteen times a second — and the process had burned 1,669 CPU-seconds by
02:05. gpt-oss is a sliding-window-attention model; without a full-size SWA
cache llama-server keeps per-slot "context checkpoints" for prefix reuse,
and that machinery wedged on slot 1 (the no-tools shape). Release 26 adds
`--swa-full` to gpt-oss's args (no checkpoints, ~0.8 GB more KV at 32k,
and honest prefix caching for an SWA model). The sick server was killed and
the sidecar's watchdog brought a fresh one up (it answers again; the first
knowledge turn after the restart was 13 s, a cold cache). Release 25's
SIDECAR BUILD was fine and is installed; only its suites failed.

### 02:30 — release 26, where the night ends
RELEASE OK (hands/sleep/telegram skipped by the quiet run, voice_ux T2 flaky
as before). Idle bench: reflex first audio 48-213 ms; knowledge questions
1.9-3.1 s to the first spoken word (hamlet 2.0, mona lisa 3.1, peru 1.9,
octopuses 2.6) — the first model turn after a hot-swap and the suites' side
calls still pays a cold cache once (10.4 s). Where Thursday evening
started: 16-24 s for every one of those. Installed and pushed.

### Not done / open
* voice_ux_e2e T2 ("CONVERSATION WINDOW") is FLAKY, not fixed: PASS in
  release 13 and in a standalone run at 00:36 on the 5th ("heard: ['And
  what day of the week is it?']"), FAIL inside the suite runs of 16-23 and in
  one standalone run at 19:11 ("heard: []"). T1's wake scores 0.63, a hair
  over the 0.60 threshold, every time. The armed-window path itself
  (`follow-up speech (conversation window)` → `_listen_flag.set()`) is
  sound; whatever it is depends on timing or on what the previous suite
  left behind. Non-fatal; next step is the sidecar log between T1 and T2 of
  a failing SUITE run.
* The HUD's clarify chips / dictation pill were verified by tsc and hud_e2e
  only — he stopped the preview pane, so they were not seen rendered.
* Dictation/press_keys not landing in the hands_e2e harness — the keys.py
  rewrite (scan codes, modifier wait, cloaked-window-aware focus) addresses
  every identified cause; `/debug/desktop` answers the window-station question
  from inside the process. Needs the next hands_e2e run to confirm.

## 2026-09-05 morning — "make Jarvis PERFECT", and the news he actually wants

He came back at 06:56 with one line: make him perfect. The method was the
same as the audit's — read the live log since the last release, measure the
running install, believe only what was observed — and it found five real
defects in a system that reported itself healthy, plus one instruction from
him mid-session.

### The mic could never heal itself
`Microphone.start()` asked for the RUNNING loop; the audit had moved every
reopen off the loop (`asyncio.to_thread`), where there is none. Seven times
since the 4th the self-heal fired, failed with "no running event loop"
before touching the driver, and logged a warning nobody read. It also fired
on "no stream open", which is true for the seconds a debug utterance is
being fed. Now `start()` keeps the loop it was first bound to, `mic.failed`
says whether the last open really failed, and the device watch retries on
THAT. Gate: `test_mic_offloop.py`.

### The HUD kept rendering in the taskbar
Measured with JARVIS asleep and minimised: renderer 11%, GPU process 18% of
a core. wry only stops resizing WebView2 on SIZE_MINIMIZED; it never tells
it the window is gone, so `document.hidden` stayed false and every rail and
reactor animation ran for nobody. The App.tsx comment that said minimising
sets `document.hidden` was wrong. `lib.rs` now calls
`controller.SetIsVisible(false)` on the Resized event while minimised and
`true` on restore — WebView2's own guidance. This is the "idle renderer CPU"
open item from the 4th; it was never the reactor loop.

### Dictation: the paste raced the clipboard, and the receipt was confounded
hands_e2e on the live install: every step passed except the document read
back, which contained a shell script — Git for Windows' `usr/bin/notepad`.
Two things at once. (1) Modern Notepad restores its last session; a tab
holding that file was open from earlier, so the receipt read the wrong
document. (2) Even so, Ctrl+V asks the app to read the clipboard whenever it
gets round to it, and the clipboard is shared with everything else. So
dictation now TYPES short transcripts as Unicode key events (no clipboard,
no restore, no race; lands in terminals too), spaced 15 ms apart — at 4 ms
modern Notepad turned "quarterly" into "uuarterly oooooooooook", at 10 ms it
was clean — and keeps the clipboard path for long text, restoring only if
the clipboard still holds our text. `open_application` also returned a
dot-relative `notepad.exe` (shutil.which searches the CWD, which a scheduled
task sets to System32); it is absolute now. `/debug/desktop` on the
schtasks-launched install says WinSta0/Default/same desktop, so the
window-station hypothesis from the 4th is dead.

### voice_ux T2 was a race against his own five-second window
The test synthesised the follow-up INSIDE the window (Kokoro on a busy CPU:
most of a second), waited a fixed three seconds, then injected — against
`conversation.window_s = 5`, his setting, not the eight the file assumed.
It now pre-synthesises every phrase, waits for the `conversation armed`
event, and injects at once. Not a bug in the window.

### Where the model's time goes, measured
llama's own timings: every no-tools turn evaluated ~300 new tokens at
5 ms/token (the tools shape sits at 7.4k tokens; the sticky shortlist is the
reason and it is deliberate). ~150 of those tokens were the ten PINNED
memories, re-read every turn because they rode in the per-turn note. They
live in the system prompt now (`prompts.pinned_block`), which changes only
when he pins something. New marks in the turn breakdown — `brain_ms`,
`memory_ms`, `llm_sent_ms` — so the next person can see pre-model overhead
without guessing. Also fixed: gpt-oss writes U+202F between a number and
its unit and inside "Mount Everest"; `clean_for_speech` folds every odd
space to a plain one.

### The news, by his instruction (mid-session)
*"I want only local and national EMERGENCIES and then for his nightly brief
(last of the day) he can tell me general news so that I'm still informed."*
Two changes. The 07:19 URGENT that chased his phone — "US envoys in Moscow
in new push for peace between Russia and Ukraine" — came from the SUMMARY:
"Russia's full-scale invasion of Ukraine", overnight missile strikes,
matched ATTACK. A foreign story is now judged by its headline
(`national_emergency(text, headline=)`). And the news section moved to the
LAST brief of the day (`briefing.news_in_briefs = "last"`, 20:00): the
midday briefs are markets only, the night brief reads five stories ranked
emergencies → near him → national weight → the wire (`rank_for_brief`,
using the full classifier, not the emergencies-only gate). `/debug/brief`
takes `final: true` to preview it. Gates extended in test_significance and
test_briefing.

### Also
* `tld` package data was not bundled: courlan tried to download the public
  suffix list into the bundle on every fetch and logged an ERROR each time.
  Added to the spec's collect_all list.
* The real sidecar log holds EVERY day. Filtering by time alone showed me
  the 09-01 retainer flood as though it were this morning — filter by date.
* Filler timing left alone on purpose: 0.35 s is a tuned choice.

Release 27 (`-Silent`, he was at the PC) carries all of it. It failed one
check - endpoint_e2e read "waited 1900 ms of silence, budget 1900 ms" as
giving up early (`>` for `>=`) - and installed anyway; the check is fixed.

## 2026-09-05 late morning — the ALT tap, the market as a story, three slots

### "Input reports success and does nothing" was an ALT tap
Release 27 installed, hands_e2e on the real install: dictation typed
"e uuanumbers kkkk rrrong." into Notepad, then with the clipboard path
NOTHING at all. A matrix from the live sidecar settled it in one run:
`press_keys h,e,l,l,o` landed "h"; `type_text` lost its first word; Ctrl+V
pasted nothing. Every focus helper (input_tools._focus, windows_tools
focus_window, the browser focus, exit_sleep_mode) pressed and released ALT
around SetForegroundWindow - "the documented trick, harmless". A lone ALT
tap puts the app that gets focus into MENU MODE, and the keys that follow
go to its menu bar. The test process never showed it because it focused
without the tap. `windows_tools.bring_to_front` (AttachThreadInput, no-op
when already foreground, SHIFT tap as last resort) replaces all four.
Dictation is back to the PASTE - one keystroke, atomic - with the clipboard
returned only if it still holds our text, and typing kept as the fallback
(`dictation.prefer_typing`). This closes the open item from the 4th; the
window-station and integrity-level theories were both innocent.

### The market as a story (his instruction)
*"Let him compile data from finnhub but also verified and trusted news
sources about the state of the market and what experts are saying... he
mentions stocks now but I need more intelligent info."* `market_intel.py`:
the gauges (S&P/Nasdaq/Dow, small caps via IWM, volatility via VIXY, open or
closed - Finnhub, all verified reachable on his free key), the STORY - two
spoken sentences written at temperature zero from the headlines of the
Journal, MarketWatch, CNBC and Reuters (via Finnhub's general feed), the
desk named - the EXPERTS (strategist and analyst calls off those desks,
attributed), and the WEEK AHEAD (earnings for his names and the ~50 that
move the market). Per company: 52-week position, YTD, P/E, beta, last
quarter's beat or miss, consensus, insider sentiment, next report.
Briefs: "The story" and "Experts" in every brief after the numbers,
"Ahead" in the morning one only, news still only at night. Voice: "what's
going on in the market", "why is the market down", "any earnings this
week", "when does Apple report", "tell me about Nvidia stock". Gate:
`test_market_intel.py`; routing cases in test_brain. Also: the Finnhub
warnings had his API key in the URL (1,200 log lines) - redacted.

### Three llama slots
Measured on release 27: a knowledge question right after a brief re-read
1,813 tokens (7.4 s to the first word) because the news summaries and the
market story share slot 1 with the no-tools conversation. `-np 3`, context
49152: slot 0 tools shape, slot 1 no-tools shape, slot 2 every side call
(`provider.stream` default). ~0.8 GB more KV on a 28 GB machine.

### The brain, from the real-world suite
`real_world_e2e` on release 27: "how many milliliters in a US cup" was
answered "Did you mean render that in 3D, sir?" - holo_make at 0.68 on the
near-miss path. Two fixes: `brain/units.py` makes conversions the math
reflex's (instant, exact: "1 cup is about 236.6 milliliters"), and
`skills.ask_allowed` never offers an ACTION as a near miss for a QUESTION
(`QUESTION_LEAD` × `QUERY_SKILLS`). Also "CPU is at 0 percent" now reads
"CPU is idle"; the suite itself crashed on a U+202F in the console (fixed).
The night brief's first live run was five Massachusetts-desk items (penguin
vests off WCVB) and nothing from the wire; `rank_for_brief` now buckets
alarm / national / local / wire / colour and interleaves country and home.

### Release 28 (09:07, RELEASE OK) — verified on the install
* hands_e2e PASS: the dictated sentence is in the Notepad document, through
  the fixed focus routine. The open item from the 4th is closed.
* `get_market_state` live: "the S&P 500 down 0.4 percent, ... small caps up
  0.3 percent. The market is closed. The U.S. stock market is mixed today as
  ... (MarketWatch)". `get_earnings_ahead`: "Adobe on Thursday after the
  close; Oracle on Thursday after the close". `get_stock_context nvidia`: the
  full picture in one breath, ending "That is the picture, not advice."
* Both briefs carry THE STORY; the night one mixes CBS/WCVB national with
  MassLive local. A 'Blue Bloods' actor's cancer got in as local news, so
  obituaries and illnesses (`significance._is_obituary`) are colour now.
* llama: "3 slot(s); side calls use slot 2", and the no-tools question after
  a brief cost 210 tokens (2.0 s). The tools-shape question still cost 2,185
  of 7,370 tokens (9 s) because the suites had grown the sticky tool block.
  So: `shortlist.block_version`, and `_rewarm_tools_shape` re-reads slot 0
  in the background three seconds after any turn that changed the block.
  Gated in test_shortlist_sticky; ships in release 29.

### 10:00 — his last conversation before leaving, and what it taught
Log: "Show me a three D render of Spider Man" scouted the web and asked
whether to start; he said "Render it." and was told *that was not an
answer*; the words then became a new request and three searches for a model
called "it". He closed the app. Three fixes, all gated: the VERB of the thing
is a yes to a cost question (`_install_cost_question` passes `yes_words`
per tool; `clarify.choose` allows a few more words for a yes/no), a pointer
description resolves to the subject and first picture of the last image
search (`render_tools.resolve_pointer`, "which one, sir?" when nothing is on
screen), and gpt-oss's follow-up repeat ("Lima. Santiago.", "Herman
Melville, 1851" - measured live, a prompt rule changed nothing) is held on
the way to the speaker (`skills.strip_repeat`, in `_llm_with_tools`).

Also: the tools prefix is warmed with EVERY tool at boot
(`shortlist.warm_block`), because the block only grows and each growth cost
the next tools-shape turn 9-10 s ("And Argentina?" on releases 27-29).

### 10:43 — power loss, resumed
The PC lost power during release 30's soak suite (everything before it had
passed; release 30 was installed at 10:29). JARVIS autostarted at boot, but
an autostart launch has no `JARVIS_DEBUG=1`, so no `session.token` and every
authenticated endpoint answers 401: after any reboot, stop the three
processes and relaunch through `.agent/scripts/jarvis_relaunch.cmd` before a
live test.

### 11:05 — a second power loss, and his own words as the test set
Release 31 died in its gate stage; release 30 stayed installed. Every real
utterance from the log (2026-09-03 to 09-05, test phrases excluded) was
routed through `brain.decide` offline. The misses were "Center it", "Stop
spinning", "Turn on hand view", "What are the systems?", "Set my volume to
fifty percent", "What's ten plus ten?" - seeds added, parser patterns for
centre/middle and keep-turning added, all in test_brain and
test_holo_control. And the transcript showed "Go for it." and "Yes, finish
the render." three times each in a row: neither was an approval, because
"for" and "finish" were not yes-words (`clarify.approval`). Release 32.

### The brief on screen (release 31, shipped in 32)
New HUD stage `brief`: a gauges row (index moves, coloured by direction) and
the same sections the phone gets. Opened by the scheduled brief
(`briefing._maybe_brief` emits `brief`) and by `get_market_state`. Held two
minutes; part of the "bring that back" snapshot.

### 12:00 — release 32 verified, and what the afternoon found
* The brief stage renders on the dev HUD at 1920x1080 (core shrunk left,
  "THE MARKET", five gauges coloured by direction, The story / Experts /
  Session). At the pane's 800x450 it sits ghosted behind the full orb - a
  viewport artefact, not his display.
* A HUD connecting to a RUNNING sidecar sat on the boot checklist until the
  state next changed: the socket carries only changes. `connectEvents` now
  asks `/health` on open (`sidecar.ts`); the dev page and any reconnect
  show the true state at once. Release 33.
* "go for it" live: "Starting now, sir - a few seconds." The duck rendered
  (60 x 30 x 43 mm, on the stage). Two things seen: after "yes" the build
  re-ran the scout's two searches because a scout that found NO model
  hands nothing back (queued: hand the "none" back too); and the finished
  render is announced through delivery, which routes to Telegram when he is
  away - so a test render while he is out reaches his phone. `/debug/silence`
  covers the speaker only. Queued: hold non-urgent deliveries while muted,
  and a `/debug/ledger` endpoint so a test can check what was sent.
* py-spy asleep: openWakeWord ~75% of the sidecar's 8% idle CPU, pycaw
  endpoint enumeration 14% - the latter now runs once a minute (release 33).
* Latency floor: brain 50-80 ms, memory 100-240 ms, prompt eval ~1 s, then
  35-60 hidden reasoning tokens at 23 tok/s (`reasoning_effort` is already
  "low"). Not much left to take without faster hardware.

### 13:20 — the Weather Service, and the morning brief (release 35)
`nws.py`: api.weather.gov active alerts for the home point (no key; verified
live - a Rip Current Statement for Southeast Middlesex that afternoon).
Tiered like the news: a WARNING for a deadly kind (tornado, flash flood,
severe thunderstorm, hurricane, blizzard, ice storm, extreme heat/cold...)
or anything Extreme+Immediate is URGENT; another warning, or a deadly-kind
WATCH, is ALERT; a watch/advisory/statement is NOTABLE and is held as
`kind: weather` for the next brief's Weather section. `briefing.scan` runs
it in the news lane; keys are the alert ids, remembered like headlines.
The morning brief (`first`) opens with Weather ("67 and clear skies in
Framingham, high of 78, low 55, 30 percent chance of rain") and Today
(reminders due today). Release 34 carried the test mute that holds the
phone, `/debug/ledger`, and the scout's "nothing" handed back.

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
