# The Evolution — implementation plan

**Status: awaiting approval. Nothing below has been started.**

Written after reading the current repo rather than the handoff alone. Six things
in the handoff turned out to be already built, already solved a different way, or
unnecessary — each is called out below with what I actually found, because they
change the shape of the work.

---

## What I verified first, and what it changes

**1. `tools/weather.py` already exists — 106 lines, already Open-Meteo, already
keyless.** It has `_geocode()`, `_home_location()`, `get_weather(location, when)`,
and is registered in `main.py`'s startup list. Phase 1's weather work is mostly
done. What is genuinely missing: preferring a fresher phone fix, and the
"as of N minutes ago" ageing the handoff asks for.

**2. `analyze_image(path, question)` already exists** in `tools/vision_tools.py`
and is registered, on the same Gemma 3 4B + mmproj pipeline the handoff names.
Phase 3 is not a new pipeline — it is a prompt template plus the Telegram photo
input path.

**3. Nominatim is probably unnecessary.** `weather._geocode()` already resolves a
place name to lat/lon through Open-Meteo's free geocoding API, with a cache. Using
it for "how far to X" avoids a second network dependency, its 1-req/sec policy and
its User-Agent requirement entirely. I would reuse it and make its cache
persistent rather than in-memory. **Nominatim stays available as a fallback if a
place fails to resolve** — I will not delete the option, just not reach for it
first.

**4. Face recognition already exists, built today.** `sidecar/vision_identity.py`
is SFace (OpenCV's own ONNX model, 38 MB, bundled in the spec), stores
**embeddings only and never images** under `%APPDATA%\JARVIS\face_profile.json`,
matches at cosine ≥ 0.363 (OpenCV's documented 99.80% decision point), and has 22
gated offline tests. It already carries the "this is NOT authentication, a
photograph would pass" warning in its own docstring.

`insightface` would duplicate this and bring three real costs: it builds Cython
extensions (MSVC toolchain risk on Windows), it downloads a ~300 MB model pack to
`~/.insightface` **at runtime** — which breaks both the offline guarantee and the
"bundle models in the spec" pattern every other model here follows — and it
declares an opencv dependency that risks the exact fight mediapipe already caused
(that one had to be installed `--no-deps`). **This is the one place I want to
deviate from the handoff, and I need your call — see Decision 1.**

**5. A real gap in the current repo, unrelated to this handoff but blocking Phase
0: `requirements.txt` is missing `opencv-python` and `mediapipe`.** Both are
installed in the venv and both are load-bearing for the camera work shipped today.
A fresh clone could not rebuild this project. Fixing that is the first thing in
Phase 0.

**6. Table-name collision waiting to happen.** `jarvis.db` already has a `tasks`
table — that is the reminders/errands store. The projects feature must not use
that name. Planned names: `projects` and `project_steps`.

**7. Counting.** The handoff says "six new capabilities" and Phase 0 lists seven
stub modules. That resolves cleanly once weather is recognised as existing:
six genuinely new modules, one already there.

---

## Decisions I need before starting

**Decision 1 — face embeddings for Phase 4: SFace (already built) or insightface
(as written)?**
My recommendation is SFace: it is already bundled, already gated, already stores
embeddings only, and adds nothing to the dependency surface. insightface's
accuracy edge is real but only matters at scale — this is a one-person, one-face
problem. If you want insightface anyway I will do it, but Phase 0 must then prove
it installs, imports under PyInstaller, and can be made to load models from the
bundle rather than downloading them.

**Decision 2 — geocoding: reuse Open-Meteo (recommended) or add Nominatim as
written?** Reusing removes a network dependency and a usage policy.

**Decision 3 — do you have OpenSCAD and PrusaSlicer installed?** Phase 5's exit
gate requires actually generating and slicing a cube. If neither is installed, I
will build the tool, ship the config paths, and make the offline test **skip with
a clear message** rather than fail — and tell you plainly that the gate was not
truly exercised. I will not fake a pass.

**Decision 4 — Phase 4's manual verification.** Its exit gate needs you in the
chair to confirm a HIGH-risk tool still demands both signals. I will stop and hand
it to you rather than sign it off myself.

---

## Phase 0 — Scaffolding

- Add the **missing existing** deps first: `opencv-python`, `mediapipe` (with a
  comment recording the `--no-deps` reason), so the environment is reproducible
  before anything is added to it.
- Add config defaults in the existing `server_binary` style: `openscad.binary`,
  `prusaslicer.binary`, `weather.home_lat`/`home_lon`, `biometric.profile_path`.
- Create the six genuinely new modules as stubs with correct
  `registry.register(Tool(...))` risk tiers and `NotImplementedError` bodies:
  `projects.py`, `location.py`, `health.py`, `vision_analyze.py`, `biometric.py`,
  `fabrication.py`. `weather.py` is edited in Phase 1, not stubbed.
- Wire all six into `main.py`'s `lifespan()` registration block.

**Exit gate:** `scripts/build_sidecar.cmd` passes with all six registered; every
skill still resolves to a registered tool (the audit check from today); no
behaviour change to any existing tool.

## Phase 1 — Independent tools

**weather.py (extend, not create):** add `as_of_minutes` to the result and speak
it; prefer a phone fix from the fact store when one is fresher than the home
default. Keep the never-cached rule.

**projects.py (new):** `projects` + `project_steps` tables in `jarvis.db`, each
write committed immediately (the write-lock rule that caused two outages here).
Tools: `list_projects` (SAFE), `log_progress` (LOW), `estimate_completion` (SAFE)
— the estimate reasons from elapsed-vs-remaining via the existing LLM, no new ML.

**Exit gate:** gated build + `tests/test_projects.py` and an extension to the
weather test. `general_e2e` and `facts_e2e` unchanged and still passing.

## Phase 2 — Telegram channel (bundled, as instructed)

One edit point: `remote_telegram.py` around line 257, where `photo`/`document`
currently answers "I can't read attachments yet" and `location` is not handled at
all. Everything inbound in Phases 2 and 3 lands here, which is exactly why the
handoff bundles them — I agree, and Phase 3's photo path will be added in the same
function to avoid touching the poller twice.

**location.py:** store `message.location` / `edited_message.location` from the
paired chat as a volatile timestamped fact. Tools: `where_am_i` (SAFE),
`distance_to(place)` (SAFE) via the existing `_geocode` + haversine. Straight-line
only. Persistent geocode cache so repeated places cost nothing.

**health.py:** parse the Shortcuts JSON payload; store each metric as a volatile
timestamped fact. Defensive parsing — size cap, type checks, unknown keys ignored,
never `eval`, malformed payload answered with a plain message rather than an
exception. Rides the existing poller and the existing allowed-chat check; **no new
endpoint, no second entry point.**

**Exit gate:** gated build + offline tests, plus `telegram_e2e` with
`JARVIS_TELEGRAM_E2E=1` — mandatory for this phase, as instructed.

## Phase 3 — Vision analysis

`vision_analyze.py`: a new prompt template aimed at object description (material,
likely composition, notable features) over the existing `analyze_image`
plumbing, plus the Telegram photo path added in the same poller function edited in
Phase 2. Risk SAFE.

**Exit gate:** gated build + offline test covering both input paths, using two
sample images committed under `sidecar/tests/`.

## Phase 4 — Biometric confirmation (most sensitive)

`biometric.py`: `face_confirm()` returning match / no-match / camera-unavailable,
built on whichever embedder Decision 1 picks.

The security property, implemented rather than documented: `face_confirm()` is
**additive only**. The spoken yes/no gate on HIGH-risk tools stays mandatory and
there is no config flag that can turn it off — I will write the test that proves a
HIGH-risk call still blocks when the face matches but the voice has not confirmed.
That is the test that matters, and it is the one that would catch a future change
quietly making face a substitute.

Scope held exactly where the handoff puts it: HIGH-risk tools only. Nothing wired
into wake or presence.

**Exit gate:** gated build + offline tests for match and no-match, **plus your
manual confirmation** on the real install that both signals are still required.

## Phase 5 — Fabrication scaffold

`fabrication.py`: `generate_part()` (OpenSCAD CLI → `.scad` → `.stl`),
`slice_part()` (PrusaSlicer `-g --load config.ini`, parsing real time/filament
estimates from stdout), and a `PrinterBackend` interface with only
`NoPrinterBackend`. One generic 0.4 mm FDM profile shipped as a swappable file.
Risk LOW for generate/slice.

**Exit gate:** gated build + offline test generating and slicing a cube and
asserting a real estimate came back — subject to Decision 3.

## Definition of done

All six phase gates in order; `docs/HANDOFF.md` gains a dated section per phase in
the existing voice including dead ends; `docs/CHANGELOG.md` updated; `README.md`
tool count/table updated; full `scripts/suites.ps1` clean, plus `telegram_e2e` and
`soak_e2e` specifically.

## Risks I will be watching

- **Always-resident memory.** Every new resident model or cache competes with a
  20B model on 32 GB. `soak_e2e` is the check; if RSS climbs across a soak run I
  will make the face embedder lazy-load and release rather than stay resident.
- **GPU contention.** All current vision models run CPU-only through ONNX
  Runtime's CPU provider and do not touch the 780M that llama-server owns. I will
  assert the provider explicitly rather than assume the default holds.
- **Telegram poller regressions.** It carries reminders, alerts and remote turns.
  Phases 2 and 3 edit one function in it, once, and `telegram_e2e` runs before
  either phase is called done.
- **Scope creep into the out-of-scope list.** Photogrammetry, routing, real
  printer backends, and face-as-wake-gate stay unbuilt.
