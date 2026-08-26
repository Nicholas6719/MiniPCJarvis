# Handoff: JARVIS HUD — the complete redesign

You are implementing a finished, reviewed UI design for a local voice assistant that
runs full-screen on a Windows mini PC. Everything you need is in this folder. Read
this document top to bottom before writing code — the *reasons* behind the layout
matter as much as the measurements, because several obvious-looking "improvements"
were tried during design and explicitly rejected. Those are called out.

---

## 0. TL;DR for the impatient

- **One principle:** the core (an arc reactor) is the subject of the screen, and it
  only ever leaves the middle to make room for something the user has to read.
- **Two geometries**, chosen by state: **radial** (core centred) and **anchor**
  (core slides left, a "stage" opens beside it).
- **One stage** replaces eleven old navigation tabs. Its *content type* changes with
  the task. There is nothing to navigate to.
- **The room takes the machine's colour.** One CSS variable drives borders, labels,
  glows and blooms, so a fault turns the whole screen red before a word is read.
- Build order is in §12. Start with the core.

---

## 1. What this is replacing

The existing app (`Nicholas6719/MiniPCJarvis`, branch `main`) has:

- A 220px three-ring orb demoted to a 300px sidebar column beside a wall of chat.
- Eleven unlabelled glyph tabs (`◈ ◎ ▣ ◐ ▤ ▦ ◍ ❖ ◔ ⌁ ⚙`) revealed only on hover near
  the top edge — unlearnable, and wrong for a machine you barely touch.
- Two type sizes doing all the work, so nothing is obviously more important.
- A constant 44px background grid and constant glow, identical whether the machine is
  idle or on fire.
- State reported in three places at once (orb label, status-bar dot, a "doing…" line)
  that could disagree.
- No sense of transition — panels cross-fade in place and the orb never moves.

`Jarvis HUD - Current.dc.html` in this folder is a faithful recreation of that UI with
a written critique at the bottom. Read it if you want to understand what each change is
fixing. **Do not port anything from it.**

## 2. About the design files

**The `.dc.html` files in this bundle are design references.** They are prototypes
that show intended look and behaviour — not production code to copy.

Your task is to **rebuild these designs in the target codebase's own environment**
(React + TypeScript + Zustand — see §11), using its established patterns. These
prototypes should become real React components, not ported HTML.

Specific prototype-only mechanics that must **not** carry over:

| In the prototype | What it means | In your build |
| --- | --- | --- |
| Everything inline-styled | A streaming requirement of the prototyping tool | Use the codebase's existing approach (`src/styles.css` today) |
| `{{ hole }}` | Value interpolation | Props / state |
| `<sc-if value="…">` | Conditional render | `{cond && …}` |
| `<dc-import name="X">` | Child component mount | `<X />` |
| `KIND` / `SCEN` lookup tables | Hardcoded demo scenarios so the design could be reviewed | **Delete.** Wire to the real state machine |
| `--a1..--a4` written by rAF | Synthetic voice envelope | Real mic / TTS RMS (§4.3) |

The `support.js` file is prototype runtime. Ignore it.

## 3. Fidelity

**High fidelity.** Every colour, size, weight, tracking, easing and duration below is
final and exact. Recreate pixel-perfectly at 1920×1080, then apply the scaling rule
in §10.

The one deliberately loose area is **example copy** for states the screenshots don't
cover. The visual treatment for all thirteen core states is specified; only the
sample sentences are illustrative.

---

## 4. The core (arc reactor)

`ArcReactor.dc.html` · screenshot `screens/20-core-all-13-states.png`

The single most important component. It replaces `JarvisCore.tsx` + `.css` entirely.

### 4.1 Layer stack

All layers absolutely positioned inside a square box (default **380px**).
Variables: `--c` state colour, `--spin` state base duration, `--chg` charge 0–100,
`--a1..--a4` live voice bands, `--amp` peak of those bands, `--glow` halo opacity.

Outside in:

1. **Outer halo** — `inset:-22%`, `radial-gradient(circle, --c@42%, transparent 62%)`.
   Opacity `--glow * (.82 + --amp * .9)`; `scale(1 + --amp * .07)`.
2. **Housing well** — `inset:3%`, `radial-gradient(circle at 50% 42%, #0a1c28, #030c14 62%, #020709)`,
   `inset 0 0 30px rgba(0,0,0,.8)`.
3. **Fine tick ring** — `inset:0`, `repeating-conic-gradient(from 0deg, --c@80% 0 .6deg, transparent .6deg 5deg)`,
   masked to a hairline annulus (`radial-gradient(closest-side, transparent 94%, #000 95.5%)`),
   opacity `.7`, `arcSpin` at `--spin × 9`.
4. **Bezel ring** — `inset:3%`, `1px solid --c@34%`.
5. **Twelve outer plates** — `inset:4.5%`,
   `repeating-conic-gradient(from 7.5deg, --c@34% 0 20deg, transparent 20deg 30deg)`,
   masked to annulus 77–79%, `arcSpin` at `--spin × 3`.
6. **Inner ring** — `inset:22%`, `1px solid --c@26%`.
7. **Charge arc** — `inset:14%`,
   `conic-gradient(from -90deg, --c 0 calc(--chg * 1%), transparent …)`,
   masked to annulus 91–93%, `drop-shadow(0 0 7px --c)`.
   **This is literal progress.** Drive it from real task progress, never decoratively.
8. **Twelve voice coils** — `2.5% × 7%` bars,
   `transform: rotate(Ndeg) translateY(-460%) scaleY(var(--aN))` for
   N = 15°, 45°, 75° … 345°, cycling `--a1..--a4`.
   Opacity `.18 + var(--aN) * .82`, `box-shadow: 0 0 7px --c`.
9. **Eight coil plates** — `inset:23%`,
   `repeating-conic-gradient(from 22.5deg, --c@95% 0 32deg, transparent 32deg 45deg)`,
   masked to annulus 70–72%, `drop-shadow(0 0 5px --c@50%)`,
   `arcSpin` at `--spin × 5` **reverse**.
10. **Three spokes** — `2px × 50%` bars at 0°, 120°, 240°, `transform-origin: 50% 100%`,
    gradient-masked so only the 4–40% span shows, `--c@75%`.
11. **Inner bezel** — `inset:36%`, `2px solid --c`,
    `inset 0 0 20px --c@40%`, `0 0 16px --c@28%`,
    filled `radial-gradient(circle, --c@16% mixed into #02080d, #01060a)`.
12. **Centre bloom** — `inset:40%`,
    `radial-gradient(circle at 50% 58%, --c@55%, transparent 72%)`,
    opacity `.55 + --amp * .75`.
13. **Triangle** — `inset:40.5%`,
    `clip-path: polygon(50% 4%, 97% 88%, 3% 88%)`,
    `linear-gradient(172deg, #fff 0%, mix(--c,white) 26%, --c 64%, mix(--c,#02121a) 100%)`,
    `drop-shadow(0 0 16px --c@85%) drop-shadow(0 0 40px --c@45%)`,
    `arcBreathe` at `--spin × 4`.

> **REJECTED — do not reintroduce.** An earlier version had a bright circular nucleus
> on top of the triangle. It fought the triangle and was explicitly cut. The centre is
> **triangle over soft bloom**. No disc.

Keyframes: `arcSpin` = 360° rotate · `arcBreathe` = scale 1 → 1.03 → 1 ·
`arcPulse` = opacity .62 → 1 → .62.

### 4.2 The thirteen states

`[colour, halo opacity, base spin duration, label, sublabel]`

| State | Colour | Halo | Spin | Label | Sublabel |
| --- | --- | --- | --- | --- | --- |
| `offline` | `#3a4656` | .06 | 9s | OFFLINE | sidecar not responding |
| `starting` | `#27c7ff` | .30 | 1.1s | SPINNING UP | loading model |
| `idle` | `#27c7ff` | .34 | 6s | STANDING BY | ctrl+shift+j to talk |
| `listening` | `#45ffc8` | .62 | 1.3s | LISTENING | go ahead |
| `processing` | `#45d7ff` | .46 | 0.8s | TRANSCRIBING | — |
| `thinking` | `#7a9bff` | .58 | 0.6s | THINKING | local model |
| `searching` | `#45d7ff` | .52 | 0.5s | SEARCHING | reading sources |
| `executing` | `#ffc94d` | .55 | 0.6s | EXECUTING | running tools |
| `waiting` | `#ffc94d` | .44 | 2.2s | NEEDS YOU | awaiting confirmation |
| `speaking` | `#27c7ff` | .76 | 0.9s | SPEAKING | interrupt any time |
| `interrupted` | `#ff8a5c` | .50 | 0.4s | STOPPED | — |
| `error` | `#ff5c6a` | .68 | 0.35s | FAULT | diagnostics has it |
| `sleeping` | `#22506b` | .10 | 12s | ASLEEP | say jarvis to wake |

**The mapping is deliberate and must be preserved:** hue = kind of state ·
ring speed = urgency · charge arc = progress · coil deformation = live voice ·
core brightness = speaking. Every moving part maps to one field. Nothing is decorative.

### 4.3 Voice reactivity

Coils deform to live audio. In the prototype this is a `requestAnimationFrame` loop
writing four smoothed CSS variables **directly onto the element** — never through
React state, which would re-render 60×/sec:

```
per frame:
  hot  = state is listening or speaking
  gate = speaking  ? 0.30 + 0.70 * max(0, sin(t*7.4)*0.7 + sin(t*11.3)*0.3)
       : listening ? 0.18 + 0.52 * wob(t, 3.1, 0.4)
       : 0
  bands = [wob(t,9.3,0.0)*1.00, wob(t,6.1,1.3)*0.86,
           wob(t,13.7,2.1)*0.68, wob(t,4.3,3.4)*0.92]
  target[i] = hot ? 0.10 + gate * bands[i] : 0.035
  smooth[i] += (target[i] - smooth[i]) * (hot ? 0.34 : 0.06)
  --a{i+1} = smooth[i];  --amp = max(smooth)

wob(t,f,p) = 0.5 + 0.5 * sin(t*f + p) * sin(t*f*0.41 + p*1.7)
```

**In production, replace the synthetic envelope with real data** — RMS from the mic
while listening, from the TTS output buffer while speaking. Keep the smoothing
coefficients (`0.34` attack, `0.06` release) and the `0.035` floor: they were tuned so
a quiet core goes almost flat and the "it's hearing me" read is instant across a room.

**Performance:** the prototype short-circuits the loop once a quiet core has settled
(`if (!hot && this.settled) return;`, reset on state change). Do the same — a page can
hold dozens of cores.

---

## 5. The geometry rule

Screenshots `01`–`04`, `16`.

Two geometries. The state picks one. There is no third.

| Geometry | When | Core |
| --- | --- | --- |
| **Radial** | `offline` `starting` `idle` `listening` `waiting` `error` `sleeping` | centred, large |
| **Anchor** | `processing` `thinking` `searching` `executing` `speaking` `interrupted` | `left: 380px`, small |

**Why faults are radial:** coming back to the middle reads as *the machine turning to
face you*. A failure isn't another panel appearing — it's the thing that was working
stopping and looking at you. This is load-bearing; don't "simplify" faults into the
anchor layout.

### 5.1 The handoff transition

Entering anchor from radial, simultaneously over **`.9s cubic-bezier(.65,0,.25,1)`**:

- Core `left: 50% → 380px`, `scale: 1.3 → 0.85`
- The ambient bloom's `left` follows the core
- `--rad: 1 → 0` — orbital rings, radial state label and rest hint fade out (.5–.6s)
- `--col: 0 → 1` — the column divider fades in; the stage fades and slides `44px → 0`
- `--rim` cross-fades to the new state colour over `.8s`

Reverse exactly on returning to radial. Because everything shares one easing and
duration, the core appears to **carry the layout with it** rather than the layout
being swapped. This is the single most important animation in the product.

### 5.2 Radial specifics

- Core centred (`left:50%; top:50%`), **scale 1.45** at rest, **1.3** listening,
  **1.0** for faults and the confirmation gate.
- **State block** at `top:128px`, centred: label then sublabel.
- **Rest hint** at `top:856px`, centred, `#3d5a6d`:
  `SAY "HEY JARVIS" OR PRESS CTRL+SHIFT+J`.
- **Spoken prose** (faults, gate) at `top:772px`, centred, 760px wide, 31px.
- Two orbital rings: 1300px `1px dashed --rim@12%` running `rail` (240s linear);
  980px `1px solid --rim@9%` running `railR` (180s reverse).
- Wedge panels: see §7.

> **CRITICAL — label clearance.** The state block and hint sit at **fixed** positions
> chosen to clear the core at its largest scale. An earlier version derived their
> offset from the core scale via `calc()` and the labels collided with the plate ring.
> Keep them fixed. If you expose core scale as a setting, **cap it at 165%**.

### 5.3 Anchor specifics

- Core `left:380px; top:50%`, scale **0.85** (0.9–0.95 while speaking).
- **Column divider** — 2px vertical bar at `left:722px`, `top:150px`, `bottom:120px`,
  `--rim@55%` with `0 0 24px --rim@40%`. This is the core casting light on the stage.
- **Stage box** — `left:760px; right:78px; top:150px; bottom:120px` → **1082 × 810**.
  Enters with `translateX(44px) → 0`.

---

## 6. The stage

`JarvisStage.dc.html` · screenshots `03`–`14`

**This is the idea that replaced the eleven tabs.** The right side is not a text
column — it is one box, always in the same place, whose *content type* changes with
the task. There is no Files view, no Browser view, no Research view to navigate to.
The utterance selects the renderer.

### 6.1 Utterance → stage type

| The user says | Stage becomes | Screenshot |
| --- | --- | --- |
| "how fast is this thing" | **prose** — the answer at 40px | `03`, `04` |
| "research the arc reactor" | **browser** — real embedded Brave | `05` |
| *(clicks in, opens own tab)* | **browser, shared** | `06` |
| *(hits a login wall)* | **browser, handoff** | `07` |
| "show me images of it" | **images** — grid, four across | `08` |
| "what did I write about X" | **file** — the file, matches lit | `09` |
| "put on Kid A" | **media** | `10` |
| "is it worth switching quant" | **table** | `11` |
| "what did we talk about earlier" | **settings, on History** | `12` |
| "put the editor on the left" | **apps** *(speculative)* | `13` |
| two requests in one breath | **split** — two panes | `14` |

**Maps need no stage type.** A map is a web page: "how far is Portland" is the browser
stage pointed at a map with the answer spoken over it. **Video needs no stage type
either** — local files play in the media frame with the art area becoming the picture;
anything on the web is already the browser stage. Don't build either.

### 6.2 Shared stage chrome

Every stage type has the same header and footer, so the eye always knows where to look.

- **Header** (`padding-bottom:18px`, `border-bottom: 1px solid --rim@22%`):
  left = eyebrow (10px mono, `.32em`, `#4d6b80`) over the state word
  (34px/600 Rajdhani, `.12em`, `--rim`, `0 0 24px --rim@45%`);
  right = a meta string (11px mono `.18em`) and a 200×2px progress track running
  `sweep` (1.6s linear infinite).
- **Footer** — prose stages get the full run timeline + machine panel (§6.3).
  Visual stages get a condensed one-line strip: three dots + labels separated by
  `1px solid --rim@14%` dividers, plus a right-aligned voice hint.

### 6.3 Prose stage (`03`, `04`)

- Body vertically centred, `gap:32px`, `padding:40px 0`.
- The question or answer at **40px/500 Rajdhani**, `letter-spacing:.002em`,
  `#eaf6fc`, `text-shadow: 0 0 40px --rim@16%`, `max-width:1000px`,
  `text-wrap: pretty`. **Deliberately the largest thing on screen.**
- **Source chips** — pill row, `radius:20px`, `padding:8px 14px`. Read = green border
  `rgba(89,224,165,.32)` + green index. In-flight = `--rim` border with a `flick`-ing
  index. A dashed chip closes the row with `N QUEUED` (hidden when nothing is queued).
- **Run timeline** — four equal columns, `gap:26px`. Each = dot + hairline rule, a
  15px title, a 10.5px mono sublabel. Dots: green done, amber (`#ffd166`) for
  reflex/brain, `--rim` flicking for in-progress, hollow at opacity .4 for pending.
  **Reads left to right as a strip, not a vertical log** — this replaces
  `ActivityLog.tsx`'s scrolling list.
- **Machine panel** — 300px, `border-left: 1px solid --rim@14%`, `padding-left:36px`.
  Three metric bars (CPU, memory, generation) + a mono block naming the model and what
  was remembered about the user.
- **Dismiss** — after speaking, the stage holds **5s** then collapses to the resting
  core. Signalled only by a 190×2px bar running `drain` (5s linear forwards) beside
  `HOLDING 5 s · SAY "KEEP IT" OR "BRING THAT BACK"`. Deliberately quiet.
  "Keep it" pins indefinitely; "keep it for ten minutes" sets a timer; "bring that
  back" restores the last stage whatever it was.

### 6.4 Browser stage — **the important one** (`05`, `06`, `07`)

**This is a real embedded Brave with real tabs.** The user can click straight into it,
open their own tab, and carry on working while JARVIS finishes in its tab. Nothing is
disabled — back, forward, reload, address bar, tabs and `+` all work.

> **REJECTED — do not build.** The first design made this read-only: no tabs, no
> controls, watch only. That was wrong. The user wants to grab the browser mid-task.

Because both parties can drive, **one question is load-bearing: who has the pointer
right now.** It is answered three ways simultaneously so it's readable from anywhere:

1. A **badge** top-right of the tab strip — dot + label in the owner's colour.
2. A **coloured top border** on JARVIS-owned tabs (2px), which it keeps even when
   the tab is inactive.
3. The **ring around the whole browser** — `box-shadow: 0 0 0 3px owner@14%` plus a
   `1px solid owner` border.

| Owner | Colour | Badge |
| --- | --- | --- |
| JARVIS | `#45d7ff` | `JARVIS DRIVING` |
| The user | `#e6e6ee` | `YOU HAVE THE POINTER` |
| Waiting on the user | `#ffc94d` | `OVER TO YOU` |

Structure: shell (`#17171b`, `radius:10px`) → tab strip (`#0b0b0e`, tabs 34px tall,
`radius:7px 7px 0 0`, active tab background `#17171b`) → URL bar (`#0f0f13`, nav
glyphs, `#1d1d22` address field with a green `TLS` prefix) → page area.

- **Page content is dimmed and desaturated** so a bright site can't blow out the room.
  Search results render at opacity .5 (unread) / .32 / .22 (further down).
- **The action marker** — the result JARVIS is about to open gets a `--rim@10%`
  highlight box with `inset 0 0 0 1px --rim@34%`, a 26px pinging ring
  (`ping` 1.5s ease-out infinite) + 8px dot, and a caption chip reading
  `OPENING RESULT 3`. **You see the decision, not just the outcome.**

  > The marker and caption are rendered **inside the highlighted result element**, and
  > the result's title carries `padding-left:154px` to clear them. Two earlier bugs
  > came from positioning this marker with hardcoded frame coordinates — it drifted
  > onto the wrong result. Anchor it to its target.

- **Shared** (`06`) — the user's tab is active; JARVIS's tab keeps its cyan edge and
  its counter climbs (`3/6 → 5/6`). Header reads `STILL WORKING` and names the tab.
  The user's tab shows a live caret rendered **inline at the end of the text**, not at
  absolute coordinates. **The core on the left keeps charging** — the progress
  indicator must never disappear because the user looked away.
- **Handoff** (`07`) — room goes amber, core drops to `waiting`/NEEDS YOU, tab edge and
  ring turn amber, the password field is pre-focused with an amber ring. The reason is
  spoken *and* written across the bottom of the page over a
  `linear-gradient(transparent, rgba(4,8,12,.95) 55%)` scrim. Escape hatch in the
  footer: *"or say 'skip it' and I'll answer without it."*

### 6.5 Images (`08`)

Four across, two rows, `gap:14px`, `radius:8px`. Source domain in a bottom scrim
under each. The best match gets a `--rim@45%` border, a `--rim` glow ring and a
`BEST MATCH` chip. The last tile is still arriving and says `FETCHING 8 / 8` with a
`shimmer` bar. No titles, no snippets, no page furniture — the user asked for images.

Tiles in the mock are placeholders; **real thumbnails come from the search.**

### 6.6 File (`09`)

Not a file browser and not a hit list — **the file, open**. Header carries the
filename (19px/500), the full path + size + edit time (10.5px mono), and a
`3 OF 41 LINES MATCHED` chip. Body shows matched lines lit
(`--rim@11%` background, `inset 0 0 0 1px --rim@32%`, 17px/500 text with the matched
figures in `--rim`) with surrounding context at opacity .4. Line numbers so the user
can find it later. Room is **amber**, because touching files is a tool run.

### 6.7 Media (`10`)

288px cover art placeholder + info column. Track title at **40px** — the same slot
the answer occupies, because here the track *is* the answer. Progress bar with a
handle, transport row (42px / 54px / 42px circles), volume. Up-next queue below with
seven rows. Room goes **green** (`#59e0a5`): a reflex handled it and the model never
woke. Transport is visible and clickable but the footer names the words that work
instead: `SAY "NEXT", "LOUDER", "PAUSE"`.

### 6.8 Table (`11`)

For when the answer *is* a comparison and prose would make the user hold four numbers
in their head. Column header row (9.5px mono `.2em`), then data rows (17px Rajdhani
label + right-aligned 15px mono figures at widths 120/130/130 + a plain-English last
column). The active row is lit like a file match and tagged `RUNNING`; its winning
figure is the only one at 20px.

**A table alone is not an answer** — the verdict sits directly beneath as a sentence
with a `--rim` dot: *"Stay where you are — the 1.8 gigabytes at the bottom of this
table aren't worth the arithmetic mistakes."* Same pattern carries "what's eating my
disk" or "what did I run this week".

### 6.9 Settings, on History (`12`)

**Conversation history lives inside settings** — it's the same question as "what have
you got on me", so it sits beside memory and permissions. 216px rail
(`border-right: 1px solid --rim@14%`) with six sections; the active one gets a
`--rim@13%` background, `inset 0 0 0 1px --rim@30%` and a 3px `--rim` marker bar.

Pane: a search field showing a live query and match count, then turns grouped by day.
Each turn = timestamp, what the user said (17px/500), what JARVIS answered (14.5px),
and **which tools ran** as chips — including a `DENIED AT THE GATE` chip, kept on the
record on purpose.

Note the core is at **`idle`** here, not working: it's showing something, not doing
anything, and the core must tell the truth about that.

### 6.10 Apps (`13`) — speculative

Marked `SPECULATIVE · NOT NEEDED YET` in the design. The only surface that shows the
*machine's layout* rather than content, which is why it can't borrow another type.
A proportional tile map of the screen with the just-moved window outlined in `--rim`,
plus an "also running" list. Build only if asked.

### 6.11 Split (`14`)

**Split is a consequence, never a layout option.** It appears only because two tasks
are genuinely running, and collapses the moment one finishes.

Two panes, `gap:26px`, separated by a 1px vertical `--rim@32%` gradient. Each pane:
a numbered badge (20px square, task colour, dark text), a title, a right-aligned
progress label, a 2px progress bar, then its own content type at reduced density.
A finished pane says `DONE` and **stays put** so it can be read while the other works.

**Hard cap of two.** A third task queues rather than shrinking these — the footer
says so: `MAX TWO AT A TIME · A THIRD WOULD QUEUE`.

---

## 7. Wedge panels (radial only)

Used by faults and the confirmation gate. Four positions:

| Position | Size | Radius | Light spill origin | Stagger |
| --- | --- | --- | --- | --- |
| Top-left | `left:96px; top:150px`, 376px | `14 14 96 14` | `100% 100%` | .05s |
| Top-right | `right:96px; top:140px`, 412px | `14 14 14 96` | `0% 100%` | .12s |
| Bottom-left | `left:96px; bottom:150px`, 376px | `14 96 14 14` | `100% 0%` | .19s |
| Bottom-right | `right:96px; bottom:140px`, 412px | `96 14 14 14` | `0% 0%` | .26s |

**The 96px corner always faces the core** — that's what makes rectangles read as
radial. That same corner carries a `radial-gradient(120% 120% at <origin>, --rim@13%,
transparent 58%)` light spill layered over the panel gradient, so the core reads as
the actual light source.

Panels **deploy outward from the core** — `translate(±26px, ±14px) → 0` over
`.7s cubic-bezier(.65,0,.25,1)` on the staggered delays, with opacity over `.45s`.
They do not fade in place.

Shell: `background: <spill>, linear-gradient(<angle>, rgba(9,22,32,.9), rgba(5,13,19,.9))`;
`border: 1px solid --rim@30%`;
`box-shadow: 0 24px 60px rgba(0,0,0,.5), inset 0 1px 0 --rim@20%, inset 0 0 60px --rim@7%`.

> Panel padding is asymmetric to clear the curved corner (e.g. top-left is
> `20px 36px 32px 22px`). Text was clipping by the corner ellipse before this.

---

## 8. Faults

Both use the same move — core back to centre, room goes red — but carry different
content, because the useful thing to say differs.

### 8.1 Subsystem down (`16`)

Four wedges: **TELEMETRY** (note it shows *idle* numbers — the machine isn't busy,
it's broken), **FAILING SUBSYSTEM** (red-tinted panel, the port, the exit code, and
`RESTART IT` / `LEAVE IT`), **HAPPENED BEFORE** (two prior occurrences plus a `NOTE`
row: *"Third time today — worth looking at the driver rather than restarting again"*),
and **EVERYTHING ELSE** (`5 OF 6 HEALTHY` + a dot per subsystem).

The pattern matters more than the single error. Reassurance that one failure didn't
take the assistant down matters as much as the failure.

### 8.2 The user closed JARVIS's tab (`17`)

A real failure, and a cheap one to undo. **Two** wedges, not four:

- Left, **equal weight** — `WHAT I STILL HAVE`: five of six sources kept, notes
  intact, only the sixth lost, and the line that makes the decision easy:
  *"Trying again only re-fetches the one, so it's about two seconds."*
- Right — `WHAT HAPPENED`: *"Brave was closed / my tab went with it, mid-fetch /
  **closed by you, not a crash**"* so the user isn't left wondering whether the
  machine is broken. `TRY AGAIN` / `ANSWER ANYWAY`.

---

## 9. The confirmation gate (`15`)

This already exists in the codebase as `ConfirmationModal.tsx` — the thing that stops
a high-risk tool running until the user says yes. Today it prints raw JSON arguments
and offers ALLOW / DENY, putting the burden of reading a code fragment on the user at
exactly the wrong moment.

Redesigned it is a **radial** state: core back to centre, amber, `waiting`/NEEDS YOU,
sublabel *"nothing has happened yet"*. It answers three questions in plain language:

- **Spoken + on screen at 31px:** the consequence, not the syntax — *"You asked me to
  delete the three-bit quant. That's 3.2 gigabytes and it does not go to the recycle
  bin — say the word and it's gone."*
- **Left wedge, `EXACTLY WHAT GOES`:** the literal path in mono, plus size, last-used
  date, and the line that prevents most accidents — **`Other files touched: none`** in
  green. Footer: *"One file. Not the folder, not the other three quants."*
- **Right wedge, `CANNOT BE UNDONE`:** amber-tinted. `Permanent delete`, the tool name
  and risk level, `nothing has run yet`, then `DO IT` / `NO`, and below them a quiet
  dashed third option: **`ALWAYS ALLOW DELETES IN D:\MODELS`** — scoped to a folder,
  not a blanket permission forever. Dashed and low-contrast so it isn't the easy path.

**It never times out into yes.** It waits indefinitely. Walk away, come back an hour
later, the file is still there. Amber holds until answered. Non-negotiable.

---

## 10. Scaling

One rule, two clauses, no breakpoints.

```
scale = clamp(0.85, min(vw / 1920, vh / 1080), 2.0)
```

**Clause one** — apply that once as a `transform: scale()` on the frame root. Contain,
not cover, so nothing is ever cropped. The core, the stage, every panel and every type
size comes along for free. **Nothing reflows, ever.** The hierarchy approved at 1080p
is the hierarchy at 4K.

**Clause two** — after scaling, **seven things re-anchor to the real viewport** rather
than the scaled box: the four corner ticks, the wordmark, the clock, and (the important
one) the **left and right wedge panels**. So on a 21:9 the wedges slide outward and the
stage gets genuinely wider instead of the composition sitting in a letterboxed island.

| Screen | Scale | Result |
| --- | --- | --- |
| 1920×1080 | 1.00 | Reference. Every measurement here is a 1.00 measurement. |
| 3840×2160 | 2.00 | Identical composition, twice as sharp. Nothing to decide. |
| 3440×1440 | 1.33 | Wedges take the real edges; stage becomes ~2320px — 880px wider than contain would give. |
| 1366×768 | floor 0.85 | **Compact:** hide mono sublabels, halve wedge padding, resting core to 1.15. |

The 0.85 floor exists because 10px mono × 0.85 = 8.5px, below which sublabels stop
being readable at desk distance. Under 1600×900, **stop scaling and start removing** —
that compact flag is the only layout variant in the entire system.

**Distance, not resolution.** A 4K monitor at 60cm and a 4K TV at 3m have identical
pixels and completely different needs. One setting, two values: `desk` (default, as
above) and `room` (×1.25 on all type, mono sublabels off, wedges drop to two). Add it
as a third first-run question only if the thing ever goes on a TV.

**Do not:** use media queries with per-breakpoint layouts · stretch non-uniformly (the
core must stay circular and its plates concentric) · letterbox with black bars on
ultrawide · use rem-based type with a fluid root (the transform already handles it and
doubling up drifts the ratios).

---

## 11. Design tokens

### Colour

| Token | Hex | Use |
| --- | --- | --- |
| `bg` | `#04080c` | Frame background |
| `panel-a` / `panel-b` | `#091620` / `#050d13` | Panel gradient (at 90% alpha) |
| `text-primary` | `#eaf6fc` | Answer prose |
| `text-body` | `#d7ecf7` | Panel body |
| `text-secondary` | `#a8c8da` | Secondary rows, clock |
| `text-muted` | `#8fb6c9` | Metric labels |
| `text-dim` | `#4d6b80` | Mono sublabels, timestamps |
| `text-faint` | `#3d5a6d` | Rest hint |
| **State hues** | | drive `--rim` |
| cyan | `#27c7ff` | idle, speaking, wordmark, default |
| mint | `#45ffc8` | listening |
| ice | `#45d7ff` | searching, transcribing |
| indigo | `#7a9bff` | thinking |
| amber | `#ffc94d` | executing, waiting, gate |
| amber-2 | `#ffd166` | reflex/brain events |
| green | `#59e0a5` | healthy, done, media |
| red | `#ff5c6a` | fault |
| red-text / red-dim | `#ffd8dc` / `#a8808a` | inside fault panels |
| orange | `#ff8a5c` | interrupted |
| slate / deep | `#3a4656` / `#22506b` | offline / sleeping |
| white | `#e6e6ee` | user owns the pointer |

**`--rim` is the whole system.** One variable per state drives panel borders, panel
eyebrow labels, inner glow, ambient bloom, the column divider, and the answer's
text-shadow. Do **not** hardcode cyan into panels — that removes the single most
valuable behaviour in the redesign.

Alpha via `color-mix(in srgb, var(--rim) N%, transparent)`:
border **30%** · top inner highlight **20%** · inner glow **7%** (`inset 0 0 60px`) ·
corner light spill **13%** · ambient bloom **12%** · column divider **55%** ·
divider glow **40%** · section rules **14–24%** · answer shadow **16%** (`0 0 40px`).

### Type

Two families. **Rajdhani says things, Share Tech Mono measures things.**

| Role | Font | Size | Weight | Line | Tracking |
| --- | --- | --- | --- | --- | --- |
| Answer / track title | Rajdhani | 40 | 500 | 1.38 | .002em |
| Fault + gate prose | Rajdhani | 31 | 500 | 1.4 | .004em |
| Column state word | Rajdhani | 34 | 600 | 1 | .12em |
| Panel headline | Rajdhani | 22 | 500 | 1.2 | — |
| Wordmark | Mono | 13 | 400 | 1 | **.62em** |
| Clock | Mono | 26 | 400 | 1 | .06em |
| Radial state word | Mono | 15 | 600 | 1 | **.5em** |
| Panel eyebrow | Mono | 10 | 400 | 1 | **.32em** |
| Radial sublabel | Mono | 11 | 400 | 1 | .24em |
| Rest hint | Mono | 11 | 400 | 1 | .28em |
| Run step title | Rajdhani | 15 | 400 | 1.25 | — |
| Run step sublabel | Mono | 10.5 | 400 | 1.5 | — |
| Panel row label | Rajdhani | 13.5 | 400 | 1 | — |
| Metric label/value | Mono | 11 | 400 | 1 | — |
| Chip host / index | Rajdhani 14 / Mono 10 | | 400 | 1 | — |
| Button label | Rajdhani | 12 | 400 | 1 | .22em |
| Boot wordmark | Mono | 58 | 400 | 1 | .52em |

**Floor: 10px mono / 13.5px Rajdhani.** Nothing smaller — this is read from a desk,
and the original's 9px labels were unreadable.

Wide-tracked centred runs need a matching `text-indent` equal to the letter-spacing,
or the trailing space makes them read visually off-centre.

### Spacing, radius, shadow

Frame chrome inset `78px` · wedge inset `96px` · corner ticks `26×1px` at `44px`,
`rgba(39,199,255,.4)` · panel radius `14px` except the core-facing corner at `96px` ·
chip radius `20px` · button radius `7–8px` · metric bars `3px` tall on
`rgba(255,255,255,.07)` with a `0 0 8px` glow on the fill · status dots `7px` in
panels, `9px` in the run strip, `11px` in vertical timelines, each with a matching glow.

### Motion

| Transition | Duration | Easing |
| --- | --- | --- |
| Core translate + scale | `.9s` | `cubic-bezier(.65,0,.25,1)` |
| Panel deploy | `.7s` | `cubic-bezier(.65,0,.25,1)` |
| Panel fade | `.45s` | `ease` |
| Column fade | `.5s` | `ease` |
| Rim colour, bloom | `.8s` | default |
| Chrome fade | `.6s` | `ease` |

Keyframes: `rail` 240s · `railR` 180s reverse · `flick` opacity .5↔1 at 1s `steps(1)` ·
`sweep` translateX -100%→300% 1.6s · `ping` scale .6→1.9 fade 1.5s ease-out ·
`shimmer` 1.4s · `drain` width 100%→0 5s forwards.

### Atmosphere

Three full-bleed layers, `pointer-events:none`, **`z-index: 0`** — they must paint
*behind* content (content sits at z-index 2 core / 3 divider / 4 panels and text).
An earlier version had these on top at z-index 20–23 and it washed out every panel.

1. Vignette — `radial-gradient(ellipse 86% 78% at 50% 46%, transparent 52%,
   rgba(2,5,8,.30) 86%, rgba(1,3,5,.52) 100%)`
2. Grain — a tiling 160×160 SVG `feTurbulence` (`fractalNoise`, `baseFrequency 0.85`,
   `numOctaves 3`), opacity `.06 + --air * .04`. Prefer a small PNG/WebP tile in
   production.
3. Scanline — `repeating-linear-gradient(180deg, rgba(0,0,0,.13) 0 1px,
   transparent 1px 3px)`, opacity `.42`

`--air` is a per-state intensity multiplier that also scales the ambient bloom, so the
room visibly gains energy as the machine works: rest `0.5` · listening `0.8` ·
working `1` · answering `1.15` · fault `1.1`.

### Chrome

Deliberately minimal: **wordmark top-left, clock top-right, nothing else.** Both fade
with `--chr`: **0.32 at rest**, 0.6 listening, 1 otherwise.

> **REJECTED — do not reintroduce.** Earlier versions had bottom status strips
> (`MIC WAKE+PTT`, `71% OF TURNS BY REFLEX`, uptime,
> `LOCAL · NOTHING LEAVES THIS MACHINE`). All cut as noise.

---

## 12. State management

The codebase already has the state machine. Wire the visuals to it; don't rebuild it.

**Core state** — one enum, thirteen values (§4.2).

**Derived per state** (a pure lookup, no extra state): `coreColour`, `haloOpacity`,
`spinDuration`, `label`, `subLabel`, `geometry`, `rim`, `air`, `chromeOpacity`,
`coreScale`, `coreX`, `stageKind`.

**Live values — write straight to the DOM via ref, never into the store:**
`--a1..--a4`, `--amp`, and the charge arc if progress updates faster than ~4Hz.

**Store-backed data the views need**

- Current turn: transcript, streaming answer, elapsed ms
- Run timeline: `{ label, detail, status: done|active|pending, kind: stt|reflex|tool|tts }[]`
- Sources: `{ index, host, title, status: read|reading|queued }[]`
- Browser: tabs `{ id, title, url, owner: jarvis|user, active, progress }[]`,
  plus `pointerOwner: jarvis|user|awaitingUser`
- Subsystem health: `{ name, status: ok|warn|down, detail }[]`
- Fault: `{ kind: subsystem|tabClosed, subsystem, detail, priorOccurrences[], advisory, retained }`
- Gate: `{ tool, risk, plainSentence, target, size, lastUsed, othersTouched, reversible, scopeOffer }`
- Telemetry: CPU %, RAM used/total, tok/s, model name, warm flags
- Media: track, artist, album, format, position, duration, volume, queue[]
- Table: `{ columns[], rows[], highlightRow, verdict }`
- History: turns `{ time, utterance, answer, tools[], durationMs }[]`
- Tasks: up to two concurrent; a third queues
- Settings: listen mode, voice, distance mode

**Transitions:** wake word / hotkey → `listening`; silence → `processing` → `thinking`;
tool call → `executing`, or `waiting` if high-risk (→ the gate); search → `searching`;
response ready → `speaking`; barge-in → `interrupted` → `listening`; TTS end →
`idle` (after the 5s stage hold); subsystem failure → `error` from any state;
JARVIS's tab closed → `error` with `kind: tabClosed`; idle timeout → `sleeping`;
sidecar loss → `offline`.

---

## 13. Mapping to the existing codebase

Repo `Nicholas6719/MiniPCJarvis`, branch `main`. React + TypeScript, Zustand store in
`src/state/store.ts`, plain `src/styles.css`.

| New | Replaces / touches |
| --- | --- |
| Arc reactor core | `src/components/JarvisCore/JarvisCore.tsx` + `.css` — **rewrite** |
| Frame + geometry + atmosphere | `src/App.tsx` — the three-column grid becomes one positioned frame |
| Radial states | `src/components/AmbientView.tsx` |
| Prose stage | `src/components/ConversationView.tsx` |
| Browser stage | `src/components/WebPanel.tsx`, `ResearchView.tsx` |
| Run timeline | `src/components/ActivityLog.tsx` — vertical log → horizontal strip |
| Machine panel + telemetry | `src/components/SystemView.tsx` |
| Fault wedges | `src/components/DiagnosticsView.tsx` |
| Confirmation gate | `src/components/ConfirmationModal.tsx` |
| Settings + History | `src/components/SettingsView.tsx`, `MemoryView.tsx` |
| Boot | boot overlay in `src/App.tsx` |
| First run | `src/components/FirstRun.tsx` |
| **Delete** | the 11-tab `<nav>` + hover strip in `src/App.tsx`; the 44px grid; bottom status strips |
| No redesigned surface yet | `TasksView.tsx` — leave as is |

---

## 14. Assets

**Fonts** — `fonts/`, from the repo's `src/assets/fonts/`:
`rajdhani-400.ttf`, `rajdhani-500.ttf`, `rajdhani-600.ttf`, `sharetechmono-400.ttf`.
Open-licence Google Fonts. **Keep them self-hosted** — the machine runs offline and
must not fetch fonts at boot.

**No raster or vector assets.** Every graphic — core, rings, plates, coils, triangle,
gauges, dots, grain — is CSS. Keep the core as CSS (or canvas/WebGL if profiling
demands it), never an image, so it can recolour per state.

**No icons, no emoji.** The redesign deliberately removes the glyph set.

Placeholders in the mocks that need real data at runtime: image-grid tiles, media
cover art.

---

## 15. What's in this folder

```
README.md                        this document
screens/                         25 PNGs — 19 full frames at 1920×1080, the core anatomy
                                 and state plates, 3 component close-ups at 2×
Jarvis HUD - Final.dc.html       the consolidated design doc — 10 sections, all frames
ArcReactor.dc.html               the core in isolation, all 13 states
JarvisFrame.dc.html              geometry, chrome, atmosphere, wedges (fault + gate)
JarvisStage.dc.html              all 12 stage content types
JarvisBoot.dc.html               boot
JarvisFirstRun.dc.html           first run
Jarvis HUD - Redesign.dc.html    the turn-by-turn exploration trail, incl. rejected options
Jarvis HUD - Current.dc.html     the pre-redesign app recreated + written critique
support.js                       prototype runtime — ignore
fonts/                           the four TTFs
```

Open `Jarvis HUD - Final.dc.html` in a browser — keep the folder intact, the files
reference `support.js`, `fonts/` and each other by relative path.

### Screenshot index

| File | Frame |
| --- | --- |
| `01-rest.png` | Radial, resting — the 99% screen |
| `02-listening.png` | Radial, listening — coils live |
| `03-working-prose.png` | Anchor, question at 40px |
| `04-answering-prose.png` | Anchor, answer + 5s dismiss bar |
| `05-browser-jarvis-driving.png` | Browser, JARVIS owns the pointer |
| `06-browser-shared.png` | Browser, user's tab active, JARVIS still working |
| `07-browser-handoff.png` | Browser, login wall, pointer handed over |
| `08-images.png` | Image grid |
| `09-file.png` | File with matches lit |
| `10-media.png` | Now playing |
| `11-table.png` | Eight-row comparison + verdict |
| `12-settings-history.png` | Settings on History |
| `13-apps-speculative.png` | Window layout (speculative) |
| `14-split-two-tasks.png` | Two panes, one done |
| `15-confirmation-gate.png` | High-risk tool gate |
| `16-fault-subsystem.png` | Vision server down, four wedges |
| `17-fault-tab-closed.png` | User closed JARVIS's tab, two wedges |
| `18-boot.png` | Boot with real sidecar events |
| `19-first-run.png` | First run, two questions |
| `20-core-all-13-states.png` | The core in all thirteen states |
| `21-core-anatomy.png` | **The core dissected** — nine numbered layers with insets, masks and what each does. Read alongside §4.1 |
| `22-core-large-four-states.png` | The core at 330px in idle, listening, speaking and fault — spin, halo and coil behaviour side by side |
| `23-component-wedge-panel.png` | A wedge panel at 2× — the 96px core-facing corner, light spill, asymmetric padding |
| `24-component-gate-actions.png` | The gate action wedge at 2× — DO IT / NO plus the dashed scoped-permission option |
| `25-component-tabstrip.png` | The browser tab strip at 2× — ownership border, live counter, badge |

---

## 16. Build order

1. **The arc reactor** with the thirteen-state table and voice reactivity. It's the
   whole identity and everything composes around it. Build it from `21-core-anatomy.png`
   (layer by layer, with insets), check motion against `22-core-large-four-states.png`,
   and all thirteen states against `20-core-all-13-states.png`. Source: `ArcReactor.dc.html`.
2. **The frame shell** — atmosphere layers at z-index 0, corner ticks, chrome, and the
   `--rim` / `--air` / `--chr` variables driven from state.
3. **Radial geometry** — rest and listening. Shippable on its own. (`01`, `02`)
4. **Anchor geometry and the handoff transition** — get the `.9s` move right before
   adding stage types. (`03`, `04`)
5. **The browser stage**, including shared and handoff. The biggest single piece and
   the most valuable. (`05`, `06`, `07`)
6. **The other stage types** — file, images, table, media, settings/history.
   (`08`–`12`)
7. **Faults and the gate.** (`15`, `16`, `17`)
8. **Split.** (`14`)
9. **Boot and first run.** (`18`, `19`)
10. **Scaling** (§10) once the 1080p layout is locked.
11. Apps, only if asked. (`13`)

## 17. Open questions for the designer

- **Conversation history, files, media, apps, browser, memory, tasks and settings**
  beyond the History pane have not been designed as full surfaces. Ask before
  inventing them.
- `TasksView` has no redesigned surface at all.
- The embedded-Brave decision (real embedded browser, not a screenshot capture) was
  made deliberately. If it proves impractical, come back rather than silently
  switching to captures — the shared-pointer behaviour in §6.4 is the point.
- Multi-monitor behaviour is unspecified.
