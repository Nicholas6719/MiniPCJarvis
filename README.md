# J.A.R.V.I.S.

A speech-first AI operating layer for Windows. You say "Hey Jarvis", and a machine
that lives entirely on your own PC answers out loud — and then actually does the
thing.

`Windows 11` · `Tauri 2 (Rust)` · `React + TypeScript HUD` · `Python sidecar` ·
`100% local inference`

Nothing here is a wrapper around somebody else's API. The language model, the
speech recognition, the speech synthesis, the embeddings and the memory all run on
one mini PC with an AMD 780M integrated GPU and no CUDA anywhere. There is no
account, no cloud round-trip, and no transcript leaving the machine.

---

## What it feels like to use

You leave it running. It sits in the tray as a dark HUD with an arc-reactor orb in
the middle, and the orb *is* the status display — hue for the kind of state, ring
speed for urgency, a charge arc for progress, the coil deforming with your actual
voice. There are thirteen states and every one of them is a real state of the
backend, never an animation played for effect.

Say **"Hey Jarvis"** (or press `Ctrl+Shift+J`) and it listens. Ask something
ordinary — the weather, the time, open an app — and the answer starts in about
three tenths of a second, because it never reached the language model at all. Ask
something real and it thinks, calls tools while it streams, and speaks each
sentence as soon as that sentence is finished rather than waiting for the whole
answer. Talk over it and it stops mid-word.

When it wants to do something consequential — type into a window, click a button,
restart the PC — it stops and asks, and you can answer out loud.

---

## What it can do

**Talk.** Wake word (openWakeWord "hey jarvis"), push-to-talk, barge-in, stop
words, and a semantic end-of-turn detector that waits longer when your sentence is
obviously unfinished and cuts almost immediately when it isn't. A follow-up inside
8 seconds needs no wake word. There is a sleep mode, and it knows when *not* to
speak (quiet hours, cooldowns, an hourly cap on unprompted remarks).

**Answer with its own brain, not the LLM.** A learned intent router embeds every
known phrasing with bge-small, compares your utterance by cosine similarity, and
if the vote is confident it runs the tool and speaks a template — a reflex, ~0.3 s
to first word instead of 2–12 s. It teaches itself: when the LLM resolves a request
using exactly one known tool, that phrasing becomes a new example, stored in SQLite
so it survives restarts. Overnight, inside quiet hours, "night school" re-checks
stored facts against their original sources, researches questions that were
answered from model memory alone, and distils paraphrases the router can execute.

**Drive Windows.** Open and close apps, focus/minimise/maximise windows, list what
is open, volume and mute, media keys, screenshots, lock/sleep/restart, the Recycle
Bin (including restoring from it). Clicking works **by name** — UI Automation reads
the real control tree, so "click the Send button" is exact and survives the window
moving or the display rescaling; a screenshot grid plus the vision model is the
fallback for apps that publish nothing.

**Use the web.** Search and a visible research pipeline, page fetch and extraction,
and a real interactive browser — open, read, click, type, submit, go back — driven
over CDP against Brave. Image search renders into the HUD's media view.

**Files.** Find, list, preview, read, move, rename, delete (to the Recycle Bin —
everything file-related is deliberately reversible), open with the Windows default
handler.

**Markets and news.** Live quotes, analyst views, company news and market movers
via Finnhub; headlines from publisher RSS, which needs no key at all. Both are
treated as facts that are true for seconds — never cached, never answered from
memory, always spoken with how old the number is. You can also put a watch on a
metric.

**Remember.** Persistent semantic memory (SQLite plus ONNX embeddings) with
recall on every turn, and a separate fact store with a notion of how durable a
fact is: a birthday is permanent, a stock price is not.

**Reminders.** One-off and recurring ("every night at 9"), with the spoken
confirmation built from what was actually written to the database rather than from
what the model intended to write.

**Run from your phone.** Telegram remote control: the same turn pipeline, a
different mouth. Outbound long-polling only — no ports, no exposure. Exactly one
chat may command it (pairing binds the first chat to send `/pair <code>`; everyone
else gets silence). Risk-gated tools pause on inline DO IT / NO buttons. Voice
notes work — OGG/Opus in, transcribed and answered. Screenshots and files come back
as real Telegram media.

**Dictate.** Hold `Ctrl+Shift+D`, speak, release, and the words land in whatever
app has focus, via the clipboard (with your previous clipboard restored). It is
deliberately *not* a conversation: nothing reaches the brain, the LLM, the fact
store or the transcript.

Over ninety tools are registered in all. Every one carries a risk tier —
SAFE / LOW / MEDIUM / HIGH — and anything MEDIUM or above cannot run without a
confirmation round-trip. Spoken approval is deliberately asymmetric: liberal about
what counts as "no", strict and English-only about what counts as "yes", so a
passing sentence from a video can never green-light a restart.

---

## How it is built

Three processes, one machine, nothing listening off the loopback interface.

```
┌──────────────────────────────────────────────────────────────┐
│ JARVIS.exe — Tauri 2 (Rust)                                  │
│ window · tray · global hotkeys · autostart                   │
│ Windows Credential Manager (the only place secrets live)     │
│ sidecar supervision: random port + token, health, tree-kill  │
└─────────────┬──────────────────────────────┬─────────────────┘
       Tauri IPC (invoke)          loopback HTTP + WS, X-Jarvis-Token
              │                               │
┌─────────────▼────────────┐   ┌──────────────▼──────────────────────┐
│ React + TS HUD (WebView2)│   │ jarvis-sidecar (Python, PyInstaller)│
│ arc-reactor orb          │◄──┤ FastAPI + WebSocket event bus       │
│ stage · wedges · panels  │WS │  Orchestrator — the turn engine      │
│ zustand store            │   │  13-state machine, mirrored to the UI│
└──────────────────────────┘   │  mic → VAD → STT → brain? → LLM     │
                               │  → tools → sentence split → TTS     │
                               │  Brain router · Tools · Memory/Facts │
                               └──────────────┬──────────────────────┘
                                              │ child in a kill-on-close Job Object
                               ┌──────────────▼──────────────────────┐
                               │ llama-server (llama.cpp, Vulkan)    │
                               │ gpt-oss-20b MXFP4 on the 780M iGPU  │
                               └─────────────────────────────────────┘
```

The Rust core owns everything that has to be a real Windows app: the window, the
tray, the global hotkeys, autostart, the NSIS installer, and Credential Manager.
It picks a free port and a random per-session token, spawns the sidecar with them,
health-checks it, and kills the entire process tree on exit. The user never
launches anything by hand.

The Python sidecar holds all of the AI. It exposes ~60 loopback endpoints and a
WebSocket event bus, and the rule is that **the UI never fabricates status** — every
visible fact is an event (`state`, `transcript`, `assistant_delta`, `tool_call`,
`confirmation_required`, `speaking`, `interrupted`, `turn_done`). The activity log
shows the tool calls that actually happened.

`docs/ARCHITECTURE.md` has the contracts in more detail; `docs/HANDOFF.md` is the
long-form engineering log and the single best place to find out *why* something is
the way it is.

---

## The local AI stack

| Job | What runs | Notes |
| --- | --- | --- |
| Language model | **gpt-oss-20b** (MXFP4) on **llama.cpp llama-server**, Vulkan | ~27 tok/s on a Radeon 780M iGPU. Dynamic port, per-session API key, job-object child so an 11 GB process can never be orphaned. Will adopt an existing server on :8080 if it serves the same model. |
| Speech to text | **Parakeet TDT 0.6B v3** (int8, `onnx-asr`, CPU) | 139 ms median / 0.6% WER in the bake-off, vs 450 ms / 5.1% for whisper base.en. faster-whisper is the fallback. |
| Speech to speech-out | **Kokoro** ONNX (Piper as the low-latency fallback) | Sentences flush to TTS as they complete, so speech starts before the answer is finished. |
| Voice activity | **Silero VAD** (ONNX) | Also drives barge-in — playback aborts ~200 ms into sustained speech. |
| Wake word | **openWakeWord** "hey jarvis" | ~1.8 ms per 80 ms frame, about 2% of one core. |
| Embeddings | **bge-small** via fastembed (ONNX) | Powers both the brain router (~10 ms) and semantic memory recall. |
| Vision | **Gemma 3 4B** + mmproj, on demand | Screen analysis, grounded with the real OS window list. |
| OCR | Windows built-in (`winocr`) | |

Sampling is greedy by default (temperature 0.0). That was measured, not guessed:
across 20 verifiable questions × 4 runs, temp 0.8 → 0.0 moved accuracy 99% → 100%
but run-to-run *consistency* 5% → 85%, and inconsistency was the actual complaint.
Anything that should vary — jokes, poems, brainstorms — raises it back up.

---

## Building and running

Everything is a script, and each script exists because a slower path was being run
by hand too often.

| Script | What it is for | Roughly |
| --- | --- | --- |
| `scripts/dev.ps1` | Run the sidecar **from source** on a fixed port/token for the edit→test loop. Closes the installed app first — two llama-servers do not fit on a 780M. | ~40 s |
| `scripts/build_sidecar.cmd` | The gate. Compiles everything, imports the risky modules, runs 17 offline test scripts, *then* runs PyInstaller. | ~4 min |
| `scripts/quick.ps1` | Sidecar-only changes: gated build → hot-swap the installed sidecar folder in the real user session → smoke suites. | ~5 min |
| `scripts/release.ps1` | Full release: gated build → `tauri build` → install the NSIS package in the real session → wait for the app → run the e2e suites against it. | ~15 min |
| `scripts/suites.ps1` | Every e2e suite against the running install, waiting for quiet between suites and keeping each suite's full output in `.agent/logs/`. | |
| `scripts/model_trial.ps1` | Hot-swap the running app to a different configured model and re-run the suites against it. | |
| `scripts/selftest.cmd` | Registers/runs the nightly 03:30 self-test; results surface in Diagnostics. | |

From a clean checkout:

```powershell
npm install
cd sidecar
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
cd ..
npm run tauri dev          # the Rust core spawns the sidecar itself
```

A production build needs the VS 2022 build tools environment (`vcvars64.bat`) on
PATH, plus the llama.cpp Vulkan build at `C:\AI\llama.cpp` and GGUFs under
`C:\AI\models\` — see the defaults in `sidecar/config.py`. `npm run tauri build`
produces `src-tauri/target/release/bundle/nsis/JARVIS_0.1.0_x64-setup.exe`.

Note that `quick.ps1` and `release.ps1` drive the install through a Windows
scheduled task rather than running it directly. That is not ceremony: an agent or
sandboxed shell sees a *virtualized* `%APPDATA%`, so an install that "succeeds"
there can leave the real user with no app at all. The helper `.cmd` files they
launch live in a gitignored `.agent/` directory. `docs/HANDOFF.md` opens with the
full story.

Data lives in `%APPDATA%\JARVIS` (config.json, jarvis.db, logs, voices,
screenshots) — deliberately *not* alongside the app in `%LOCALAPPDATA%\JARVIS`, so
uninstalling can never take your memories with it. Secrets live in Windows
Credential Manager under the service name `JARVIS`, and nowhere else.

---

## How it is tested

Two layers, and the second one is the interesting half.

**Gates in the build.** `build_sidecar.cmd` refuses to produce a bundle unless
`compileall` passes, the risky modules actually import, and seventeen offline test
scripts pass — the brain router against held-out phrasings, seed collisions, the
persona, the fact store, the remote path, input, endpointing, dictation,
clarification, the audio watcher, voice notes, reminders, sleep coverage, speech
symbol handling and more. This exists because PyInstaller will happily bundle a
module that does not compile and exit 0, and you find out at runtime.

**End-to-end suites against the installed app.** `scripts/suites.ps1` runs
seventeen suites over loopback against the real, running, installed JARVIS — not a
mock, not a source checkout: `brain`, `general`, `teach`, `files`, `research`,
`facts`, `filler`, `voice_ux`, `endpoint`, `wake_guard`, `hands`, `clarify`,
`market`, `telegram`, `hud`, `sleep` and `soak`. Suite order matters and is
deliberate — `sleep_e2e` runs last because waking him churns state, and each suite
waits for the app to fall quiet first so it doesn't read the previous answer.

A few suites are **opt-in by environment variable** so a normal run stays honest:
`telegram_e2e` needs `JARVIS_TELEGRAM_E2E=1` (it messages a real chat) and
`wake_guard_e2e` needs `JARVIS_WAKE_GUARD=1` (the feature it tests ships disabled).
`voice_ux_e2e` needs the app started with `JARVIS_DEBUG=1` for audio injection.

**`soak_e2e` is the one that matters most, and it asks a different question.**
Every other suite asks whether an answer was right. This one hammers the paths that
touch native code — COM for audio, COM for UI Automation, PortAudio, the browser,
the recogniser — and then compares the sidecar's PID at the end against the start.
It exists because a use-after-free once crashed the sidecar nine times in an
afternoon while every feature test stayed green: a dead sidecar restarts in forty
seconds and answers the next question perfectly. It also runs at a *realistic*
pace on purpose (30 turns and 4 diagnostics over 4 minutes). The first version ran
about 60× faster than any human, and half of what it reported was the supervisor
restarting a merely-busy process. A test that can only fail by being unfair teaches
you to ignore it.

---

## Current limitations

**Web search is the weak spot.** There is no keyless general web search left: the
DuckDuckGo HTML endpoint answers with a CAPTCHA, and Brave Search challenges
automated queries even from a warm profile. JARVIS drives a hidden Brave of its own
and falls back through Wikipedia, Hacker News and Stack Exchange — real sources,
but they cannot tell you today's price or what is in stock. Queries that plainly
need the live web return an explicit *blocked* error rather than near-miss context,
because handing the model half-relevant pages is exactly what produced confident
fabrications. **The real fix is a Brave Search API key in Settings** (free tier);
the code path already exists and takes it.

**The room-audio guard ships OFF.** It works — a television really does stop being
able to wake him — but with it enabled the packaged sidecar corrupts its own heap
and dies. One genuine bug was found and fixed on the way (a `cast()` around a
`QueryInterface` result, creating a second pointer owning no reference); the
minimal rewrite still crashes, and it has never reproduced outside the PyInstaller
bundle. Default `wake.ignore_while_audio_plays: false`. The top of
`sidecar/audio/output_watch.py` lists what has already been ruled out, which is
most of the obvious things.

**Secrets can be lost across a restart storm.** The Rust core pushes secrets from
Credential Manager at sidecar startup, but the supervisor gives up after three
restarts in ten minutes; the sidecar then behaves like it never had a key.
Diagnostics now says so explicitly instead of degrading silently, and
`GET /secrets` returns the names a session holds so the core can reconcile —
but the supervisor does not yet re-push them on its own.

**This is a one-machine project.** Paths, folders and hardware assumptions are
specific to the machine it was built on, and are visible in `sidecar/config.py`.

---

## Repository layout

```
src-tauri/   Rust core — window, tray, hotkeys, Credential Manager, sidecar supervisor
src/         React + TypeScript HUD — arc reactor, stage, wedges, panels, zustand store
sidecar/     Python AI backend
  orchestrator.py    the turn engine
  brain/             learned intent router, fact store, night school
  audio/             STT, TTS, VAD, wake word, endpointing, dictation plumbing
  llm/               llama-server supervision, prompts, provider interface
  tools/             ~70 registered tools, risk-tiered
  memory/            SQLite + embeddings
  tests/             offline gates and the e2e suites
scripts/     dev, build, release, suites, model trials
docs/        ARCHITECTURE · HANDOFF · SECURITY · TROUBLESHOOTING · CHANGELOG · roadmaps
```

**Start with `docs/HANDOFF.md`.** It is a living engineering log, newest sections
last, and it records the reasoning and the dead ends behind almost every decision
above.
