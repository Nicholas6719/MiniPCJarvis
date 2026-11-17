# How JARVIS works

A guided tour of the code: what the pieces are, what happens when he speaks,
what JARVIS does without being asked, and where to look for each thing. For the
*why* behind any decision, `docs/HANDOFF.md` is the engineering log.

Related: [`BRAIN.md`](BRAIN.md) (the intent router, in depth) ·
[`SECURITY.md`](SECURITY.md) · [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md)

## Contents

1. [The shape of it](#the-shape-of-it)
2. [A spoken turn, step by step](#a-spoken-turn-step-by-step)
3. [What it does without being asked](#what-it-does-without-being-asked)
4. [The contracts everything relies on](#the-contracts-everything-relies-on)
5. [Module map](#module-map)
6. [Data and settings](#data-and-settings)
7. [Staying alive](#staying-alive)
8. [Rules the code is built around](#rules-the-code-is-built-around)

---

## The shape of it

Three processes on one Windows machine. Nothing listens off the loopback
interface.

```
┌─────────────────────────────────────────────────────────┐
│  JARVIS.exe  (Tauri 2, Rust)                            │
│  window · tray · global hotkeys · autostart             │
│  Windows Credential Manager (the only home for secrets) │
│  starts the sidecar on a random port with a random      │
│  token, health-checks it, kills the whole tree on exit  │
└──────────────┬──────────────────────────┬───────────────┘
       Tauri IPC (invoke)        loopback HTTP + WebSocket, X-Jarvis-Token
               │                          │
┌──────────────▼───────────┐   ┌──────────▼───────────────────────────┐
│  React + TS HUD          │   │  jarvis-sidecar (Python, PyInstaller)│
│  (WebView2)              │◄──┤  FastAPI, ~75 endpoints, /ws events  │
│  orb · stage · panels    │ WS│                                      │
│  renders events only     │   │  Orchestrator ── the turn engine     │
└──────────────────────────┘   │  Brain ── reflex router, no LLM      │
                               │  Tools ── ~140, risk-tiered          │
                               │  Memory ── SQLite + embeddings       │
                               │  Proactive ── briefs, news, markets, │
                               │               reminders, scanner     │
                               │  Delivery ── speak, or his phone     │
                               └──────────┬───────────────────────────┘
                                          │ child in a kill-on-close Job Object
                               ┌──────────▼──────────────────┐
                               │ llama-server (llama.cpp)    │
                               │ gpt-oss-20b, Vulkan iGPU    │
                               │ OpenAI-compatible API       │
                               └─────────────────────────────┘
```

Two other helper processes run beside the sidecar: a Pocket TTS worker (the
voice) and, when a scanner is attached, the separate BC125AT Console service
that owns the radio's serial port.

**Who owns what**

- **Rust** owns everything that must be a real Windows app, and the secrets.
- **React** draws. It never invents status: every visible fact arrived as an
  event from the sidecar.
- **Python** owns all the behaviour: listening, deciding, acting, speaking,
  remembering, and everything proactive.

## A spoken turn, step by step

`sidecar/orchestrator.py` is the turn engine.

**1. Hearing him**

```
microphone (audio/io.py)
 -> wake word, openWakeWord "hey jarvis"            audio/wake.py
 -> is his NAME really in the audio?                orchestrator._name_heard
      Parakeet transcribes the last 2.5 s; no name, no wake.
      A name used ABOUT him ("...then Jarvis can...") is rejected too.
 -> capture until he stops                          Silero VAD + a turn model (audio/endpoint.py, turn_model.py)
 -> speech to text, Parakeet TDT 0.6B               audio/stt.py
```

After a reply there is a short **conversation window** in which he can speak
again without the wake word.

**2. Deciding what it is** (`_converse`, first match wins)

```
 1. answer to an open "did you mean X?"
 2. answer to an open clarifying question           clarify.py
 3. two requests in one sentence                    -> one short question
 4. "say that again"                                -> repeat
 5. a phrase he taught                              brain.match_command
 6. THE BRAIN: a known request                      brain.decide  -> reflex, no LLM
 7. a stray word, or the television                 -> dismissed in silence
 8. the brain nearly knew                           -> "Did you mean ...?"
 9. a stored, verified, timeless fact               brain/facts.py
10. the LLM, with a shortlist of tools              _llm_with_tools
```

Most real turns end at step 6. See [`BRAIN.md`](BRAIN.md).

**3. The LLM path** (`_llm_with_tools`)

- The system prompt (`llm/prompts.py`) is static so llama-server can reuse its
  cache; the time, relevant memories and what is on screen ride in a bracketed
  note on the latest user message.
- Tools are shortlisted by embedding similarity (`tools/shortlist.py`) so the
  model sees the dozen it could plausibly need, not all of them.
- The reply streams. Each finished sentence goes to the voice immediately, so
  speech starts before the answer is complete.
- Tool calls execute between rounds (at most six). Anything risky asks him
  first (see the tool contract below).
- Every sentence passes through filters before it is spoken: a repeated first
  sentence is dropped (`strip_repeat`), and a promise nothing will keep ("I'll
  keep an eye on it", in a turn where no tool ran) is replaced with the truth
  (`honesty.py`).
- Afterwards the brain may learn from what the model did.

**4. Speaking**

`audio/tts.py` drives the Pocket TTS worker (`audio/pocket_worker.py`), with
Kokoro as the fallback. A watcher listens while he is being spoken to: his name
interrupts playback within about 200 ms and the turn flips straight back to
listening (**barge-in**).

**From his phone** the same turn runs with no audio: `remote_telegram.py`
receives the message, runs the text through the same pipeline, and sends the
reply back. A message from his phone never wakes the monitor or raises the
window.

## What it does without being asked

This half is as large as the conversational half, and it is what he actually
receives most days.

```
sources                          judgement                    reaching him
───────────────────────────────  ───────────────────────────  ─────────────────────
news feeds        briefing.py -> significance.py (rules)   ┐
                                 news_check.py (LLM confirm)│
markets           briefing.py -> classify_market           │
                  market_intel.py                          │
weather service   nws.py                                   ├─> delivery.py ─┬─> speak (at the PC)
reminders         tasks/scheduler.py                       │   notify_policy │
standing watches  standing.py  ("tell me when ...")        │                └─> Telegram (away)
system health     proactive.py                             │
police scanner    scanner_desk.py + scanner/               ┘
```

- **`delivery.py`** is the single door. It decides whether he is at the PC
  (speak) or away (Telegram), refuses to repeat itself, and enforces hard
  hourly and daily ceilings. Nothing proactive bypasses it.
- **`notify_policy.py`** decides, per kind of message, whether it makes a
  sound, whether it may interrupt him, and which budget it counts against.
- **Tiers**: `URGENT` (sounds, and is repeated until he acknowledges),
  `ALERT`, `NOTABLE` (held for the next brief), `BRIEF`.
- **Briefs** go out four times a day (`briefing.py`): weather, markets, his
  holdings, and in the evening the day's news, scored by `news_score` and never
  repeating a story already told.
- **Quiet hours** hold almost everything overnight; a morning summary covers
  the night.

### The scanner desk

`scanner_desk.py` and `sidecar/scanner/` turn a radio scanner into
notifications. It is the most safety-sensitive part of the system.

```
BC125AT Console service (separate repo, owns the radio)
 -> feed of transmissions + recorded clips          scanner/client.py
 -> two-tone page detection                         scanner/tones.py
 -> speech to text (Parakeet, behind his own turns)
 -> rules read the words: kind of call, address,    scanner/rules.py
    units, facts, word for word
 -> transmissions merge into incidents              scanner_desk._merge
 -> decide: stored / quiet line / alert / urgent    scanner_desk.decide
 -> one Telegram message per call, edited as it     scanner_desk.written
    develops: heading, where, what is going on
 -> the summary is the LLM's, fact-checked          scanner/factcheck.py
```

Its rules, each from a specific incident:

- nothing makes a sound on one transmission (a second must agree), unless he
  has turned **emergency mode** on;
- a model's sentence reaches him only if every content word in it was actually
  heard (`factcheck.py`);
- a daily cap may quieten a real call but never drop one;
- if JARVIS cannot hear the scanner it says so, and never calls deafness quiet.

## The contracts everything relies on

**State machine** (`state_machine.py`, mirrored in `src/state/store.ts`)
`OFFLINE STARTING IDLE LISTENING PROCESSING THINKING SEARCHING EXECUTING WAITING
SPEAKING INTERRUPTED ERROR SLEEPING`. Transitions are validated; every change is
broadcast as a `state` event and the orb renders from it directly.

**Event bus** (`events.py` -> WebSocket `/ws`)
Every user-visible fact is an event: `state`, `transcript`, `assistant_delta`,
`tool_call` (pending / success / error / denied), `confirmation_required`,
`speaking`, `interrupted`, `turn_done`, `brief`, `proactive`, `error`.
Background work is started with `events.spawn()`, never a bare
`asyncio.create_task` (a bare task can be garbage-collected mid-await).

**Tool contract** (`tools/registry.py`)
A tool is a name, a JSON-schema for its parameters, a risk tier, a timeout and
an async handler. `SAFE` and `LOW` run; `MEDIUM` and `HIGH` require a
`confirmation_required` round trip, and a denial or timeout is reported to the
model as a refusal. Adding a tool is one `Tool(...)` registration. Tools whose
names start with `_` are internal: reflexes use them, the model never sees them.

**Skill contract** (`brain/skills.py`)
A skill maps a kind of sentence to a tool, its arguments and a spoken template.
See [`BRAIN.md`](BRAIN.md).

**Provider interfaces**
`llm/provider.py` (streams OpenAI-style chunks from llama-server; side calls
use their own slot so they cannot evict the conversation's cache),
`audio/stt.py`, `audio/tts.py`. The orchestrator sees only the interface.

## Module map

Everything below is under `sidecar/`.

**The turn**

| File | Role |
| --- | --- |
| `main.py` | FastAPI app: endpoints, WebSocket, start-up, debug endpoints |
| `orchestrator.py` | the turn engine: capture, route, LLM loop, speech, barge-in |
| `state_machine.py`, `events.py` | states and the event bus |
| `clarify.py` | one short question when a request is ambiguous |
| `honesty.py` | replaces promises nothing will keep |
| `dictation.py` | typing what he says into another app |
| `awareness.py`, `day_memory.py` | what is on his screen, what happened earlier today |

**Understanding**

| File | Role |
| --- | --- |
| `brain/` | reflex router, skills, fact store, night school ([`BRAIN.md`](BRAIN.md)) |
| `llm/prompts.py` | the system prompt and the per-turn note |
| `llm/provider.py`, `llm/llama_server.py` | the model server and its supervision |
| `llm/vision_server.py` | on-demand vision model for screen questions |
| `memory/store.py`, `memory/vault.py` | SQLite: memories, transcript, deliveries, turn stats; semantic recall |

**Senses and voice**

| File | Role |
| --- | --- |
| `audio/io.py`, `vad.py`, `endpoint.py`, `turn_model.py` | microphone, voice activity, when he has finished |
| `audio/wake.py` | wake word, name verification |
| `audio/stt.py` | Parakeet speech to text |
| `audio/tts.py`, `pocket_worker.py`, `speech_text.py` | the voice, and making text speakable |
| `camera.py`, `vision_*.py`, `hand_*.py` | camera, presence, identity, hand gestures |

**Acting**

| File | Role |
| --- | --- |
| `tools/registry.py`, `tools/shortlist.py` | tool contract; choosing which tools the model sees |
| `tools/*.py` | the tools: apps and windows, files, web, markets, news, reminders, phone, office, 3D |
| `browser/session.py`, `search_brave_web.py`, `linkguard.py` | reading and searching the web safely |
| `create3d.py`, `meshio.py`, `render_queue.py`, `holo_angles.py` | 3D models, the hologram stage, printing |

**Proactive**

| File | Role |
| --- | --- |
| `delivery.py`, `notify_policy.py` | the single door out, and the sound / interrupt / budget rules |
| `remote_telegram.py` | the phone bridge, both directions |
| `briefing.py`, `significance.py`, `news_check.py`, `newsroom.py` | news watch, classification, briefs |
| `market_intel.py`, `analyst.py`, `market_calendar.py` | markets, his holdings, earnings |
| `tasks/scheduler.py` | reminders and their follow-ups |
| `standing.py` | "tell me when ..." watches, including news stories |
| `proactive.py` | system health and the standing-watch loop |
| `scanner_desk.py`, `scanner/` | the police and fire scanner |
| `geo.py`, `nws.py`, `icloud.py` | his towns and distances, weather alerts, calendar |

**Front end and shell**

| Path | Role |
| --- | --- |
| `src/` | React HUD: `state/store.ts` mirrors the state machine; components render events |
| `src-tauri/` | Rust: window, tray, hotkeys, Credential Manager, sidecar supervisor, installer |
| `scripts/` | dev, the gated build (`build_sidecar.cmd`), release |

## Data and settings

- `%APPDATA%\JARVIS\` - `config.json`, `jarvis.db`, `logs\sidecar.log`,
  `session.token`.
- **`jarvis.db`** (SQLite, WAL): `memories`, `facts`, `transcript`,
  `turn_stats`, `deliveries`, `tasks`, `reminder_log`, `standing_watches`,
  `volatile_facts`, `news_judged`, `brain_examples`, `brain_rejections`,
  `brain_commands`, `scanner_hits`, `scanner_incidents`, `scanner_sent`.
- **`config.py`** holds every default; `config.json` overrides. Read with
  `config.get("section", "key", default=...)`.
- **Secrets** live only in Windows Credential Manager, service `JARVIS`.
- **Models** under `C:\AI\` on the development machine.
- **`volatile.py`** is a small key-value store for facts that expire (his
  location, a claim that a message was already sent). It is also how anything
  that must not be said twice is claimed before it is said.

## Staying alive

- The Rust supervisor spawns the sidecar, health-checks it and kills the whole
  tree on exit.
- llama-server is a Job Object child: an 11 GB process can never be orphaned.
- "Alive" means *answering*, not *existing*: the supervisor checks that the
  sidecar responds, after a 40-minute freeze in which the process was up and
  deaf.
- A slow model is not a dead one: under memory pressure the model server is
  given patience rather than a restart that would freeze everything for
  minutes.
- Bookkeeping never kills a turn: a failed database write is rolled back and
  logged, and the turn continues.

## Rules the code is built around

These recur across the code and are worth knowing before changing anything.

1. **The UI never fabricates status.** If it is on screen, an event said so.
2. **Never claim an action happened unless the tool result confirms it.**
3. **Never invent a fact about him.** Unknown is said as unknown.
4. **Live questions get live answers.** Prices, news and "the latest" are never
   answered from the model's memory.
5. **Unprompted messages have a ceiling.** Claim before speaking, de-duplicate
   everything, hard hourly cap. (A missed commit once sent 2,600 messages in a
   night.)
6. **A guard is not an outcome.** Test where a sentence ends up, not that
   something refused it.
7. **Quiet is not silent.** A real local event still reaches him, quietly;
   "never" is not an acceptable setting.
8. **Tests are silent and hermetic.** Gates never touch his database, his
   files, his phone or the network. Live checks mute the speaker and stop the
   microphone first.
9. **Every fix carries its reason.** Comments name the date and the sentence
   that went wrong, so nobody "simplifies" the fix away.
