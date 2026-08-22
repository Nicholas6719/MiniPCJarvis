# JARVIS — Continuation Handoff (living document)

Read this first after any context reset. Everything below was learned the hard way.
Updated: 2026-08-22 ~10:05.

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
- Everything stays inside JARVIS: `open_url`/`browser_*` use a second hidden Brave
  profile (`session-browser`) and push screenshots to the BROWSER view; WebPanel result
  clicks open in-app; `open_application` resolves alias -> Start Menu -> PATH -> Store
  apps and never spawns `start ""` (that popped a cmd window).
- Window list excludes Brave processes running JARVIS profiles.

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
