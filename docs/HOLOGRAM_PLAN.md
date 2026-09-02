# Holographic images, and hand controls

**Status: plan only. No code written.**

Written after reading the HUD and the vision stack rather than from the idea
alone. Three findings decide the shape of this, and one of them answers your
question about ordering.

---

## What I found first

**1. There is no 3D anywhere in this app, and the HUD's idiom is deliberate.**
Dependencies are react, react-dom, zustand and the Tauri plugins — no three.js,
no WebGL wrapper, no canvas library. `ArcReactor.tsx` animates by writing CSS
custom properties (`--a1..--a4`, `--amp`) from a `requestAnimationFrame` loop and
carries a comment saying why: *"never through React state, which would re-render
the tree 60×/sec."* Whatever gets built has to live inside that discipline or it
will cost the idle-CPU work already done here.

**2. `vision_hands` already computes 21 landmarks per hand — and throws them
away.** `read()` returns `{"hands": [{"hand", "fingers"}], "fingers": N}`. The
landmark array goes into `count_extended()` and is discarded. The landmarks are
the entire input to hand control, and today nothing outside that function ever
sees them.

**3. There is no continuous vision stream to the HUD at all.** No `bus.emit` in
camera.py, vision_presence.py or vision_hands.py. Hand data reaches the HUD only
by asking the `count_fingers` tool, which deliberately reads six frames and takes
a majority vote — about half a second, built for answering "how many fingers am I
holding up" and exactly the wrong shape for driving something interactively.

So: **the renderer and the hand controls share almost nothing.** The renderer
needs something to display and a way to be told what to do. The hand controls
need a landmark stream that does not exist yet. That is what makes your ordering
the right one.

---

## Recommendation on ordering: your instinct is right — do them separately

Not because they are equally hard, but because **the seam between them is clean
and cheap to define**. The renderer needs a small imperative control surface —
"rotate to this angle, scale to this factor, select this thing". In phase 1 that
surface is driven by voice and mouse. In phase 2 hands drive the *same* surface.
Nothing gets built twice, provided the surface is defined in phase 1 rather than
discovered in phase 2.

Doing both at once would mean a long stretch with nothing verifiable in it: a
renderer with no content and an input with nothing to move. Splitting gives a
working hologram you can look at and judge — and the look is the part most worth
your eye early, because it is a matter of taste rather than correctness.

The one thing I will do up front, in phase 1, is define that control surface and
write the gesture *names* into it (`grab`, `release`, `rotate`, `scale`) even
though only voice and mouse call them at first. That is the difference between
plugging hands in later and rebuilding for them.

---

## Decision 1 — what "holographic images" means (I need your answer)

Two readings, materially different work:

**(a) A 3D object floating in the HUD.** Wireframe or translucent, rotating,
Iron Man style. The obvious content source is already in the repo as of tonight:
`generate_part` produces STL files. "Make me a bracket" → it appears as a
hologram → you turn it with your hand → "slice it" → it prints. That is a real
loop, not a demo.

**(b) A 2D image presented holographically.** Photos, screenshots or search
results shown with depth, parallax, scanlines and a chromatic edge — the image
lifted off the panel rather than a model in space.

**My assumption unless you say otherwise: (a), with (b) falling out of it**, since
a renderer that can place a textured plane in space gets most of (b) for free.

## Decision 2 — Canvas 2D or WebGL

**My recommendation: Canvas 2D wireframe first, with WebGL as a defined
escalation.** Reasoning rather than preference:

- The films' holograms *are* line art — translucent, cyan, edges and vertices.
  Canvas 2D projecting an STL's edges hits that aesthetic natively rather than
  approximating it.
- No new dependency. three.js is ~600 KB and this app currently ships six
  runtime dependencies.
- **No GPU contention.** llama-server owns the 780M for a 20B model; WebGL in
  WebView2 would compete for the same integrated GPU. Canvas 2D on a projected
  edge list is CPU work measured in single-digit milliseconds for a simple part.

The honest limit: Canvas 2D falls over on a heavy mesh. So the escalation trigger
is measured, not guessed — **parse the STLs `generate_part` actually produces and
count triangles.** My generate_part prompt asks the model for simple printable
parts, so I expect hundreds to low thousands of triangles, which Canvas handles
comfortably. If real parts come back at tens of thousands, WebGL is the answer
and the renderer swaps behind the same component boundary.

## Decision 3 — where it lives

A new stage kind, `holo`, alongside the seven that exist (`prose`, `browser`,
`images`, `file`, `folder`, `camera`, `settings`). It inherits the camera stage's
hard-won behaviour: stages are **fragments**, never a nested `.stage` div — that
mistake put the camera panel 646 px off-screen and cost an afternoon.

---

## Phase 1 — the hologram (no hands)

1. **STL parsing in the sidecar**, not the browser: binary and ASCII STL to a
   compact edge list, deduplicated, with the triangle count reported so decision
   2's trigger can be evaluated on real data. Sent once when the stage opens, not
   streamed.
2. **`HoloStage`** — a canvas rendering projected edges, animated by rAF, writing
   to the canvas directly and never through React state. Idle cost must be near
   zero when nothing is moving, the way ArcReactor settles.
3. **The control surface**: a small imperative handle (`rotate`, `scale`,
   `reset`, `select`) that phase 2 will drive. Voice reflexes ("turn it", "bigger",
   "stop") and mouse drag call it in phase 1.
4. **Voice routing** for opening it — and this is where the `_CANON` gate earns
   its keep: "show me the bracket" will canonicalize toward the images skill
   unless `hologram`/`holo` joins the subsystem-noun exclusion list. That is the
   fifth time that rewrite would have eaten an intent, and now there is a test
   that fails when it does.

**Exit gate:** offline tests for the STL parser (binary, ASCII, malformed,
enormous) and for the projection maths; a real generated part rendered on the
installed build; idle CPU measured with the stage open and nothing moving.

## Phase 2 — hand controls

1. **Expose the landmarks.** `vision_hands.read()` returns them alongside the
   counts. Nothing else changes shape.
2. **A continuous mode**, separate from `read_many`'s six-frame majority vote —
   that vote exists to stop a blurred frame becoming "seven" and is right for
   counting and wrong for tracking. Tracking wants the newest frame, smoothed.
3. **A landmark stream to the HUD.** ~21 points × 2 hands × 30 fps is small as
   JSON over the existing `/ws`. It runs **only while the holo stage is open** —
   mediapipe is ~8 ms a frame, which at 30 fps is a quarter of one core, and this
   machine is already running a 20B model.
4. **Gesture layer**: pinch to grab, drag to rotate, two hands to scale, open
   palm to release. Debounced, with a deliberate "a hand crossing the frame must
   not fling the model" rule.
5. **The mirror.** The webcam is mirrored, and `vision_hands` already refuses to
   name left or right for exactly this reason. Screen-space x must be flipped or
   every control will feel backwards — and it will feel *subtly* wrong rather
   than obviously wrong, which is worse.

**Exit gate:** offline tests for gesture recognition against synthetic landmark
sequences (as `test_hands.py` already does for counting); measured CPU with the
stream running; and a live check that closing the stage stops the stream — an
always-on hand tracker is exactly the kind of resident cost `soak_e2e` exists to
catch.

---

## Risks I will be watching

- **Idle cost.** The HUD's 41.7% → 0.8% idle-CPU work is in the changelog. A
  canvas that repaints when nothing moves would undo it. It settles, like the orb.
- **The event loop.** A 30 fps landmark path must never block it. The 40-minute
  freeze came from exactly this shape, and the supervisor only checked the
  process was alive.
- **Scope.** No photogrammetry, no scene reconstruction, no real 3D scanning. The
  Evolution handoff ruled those out and nothing here needs them.
