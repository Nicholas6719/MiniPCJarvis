"""Text to a printable part: OpenSCAD to STL, PrusaSlicer to G-code.

NEITHER BINARY IS INSTALLED ON THIS MACHINE and there is no printer, which is
stated up front because it decides how this file is written and how it is tested.
The offline gate SKIPS the generate-and-slice case with a clear message rather
than passing; a green tick for a tool that never ran is worse than an honest
skip, and he would rightly stop trusting the suite. Everything that does not need
a binary — the geometry the model is asked to write, the argument construction,
the output parsing, the refusals — is tested for real against captured slicer
output.

PrinterBackend exists with only NoPrinterBackend behind it, deliberately. The
abstraction is the point: adding OctoPrint or Moonraker later should be one new
file, not a redesign. Do not build one now — out of scope by instruction.

Safety shape: generate/slice are LOW risk because they only create files in a
work directory of ours. The model writes OpenSCAD source, which is a real
language, so it is written to a file and handed to the binary — never eval'd,
never shelled through a string. Paths are resolved and confined to the work
directory so a generated name cannot walk out of it.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess          # for CREATE_NO_WINDOW only; the running is async
from pathlib import Path

from config import APP_DIR, config
from tools.registry import Risk, Tool, registry

log = logging.getLogger("jarvis.tools.fabrication")

GEN_TIMEOUT_S = 120

# THE ONE OPENSCAD MISTAKE THIS MODEL KEEPS MAKING: writing it like Python.
#
#     arm1 = cube([40,20,4]);
#     base = union() { arm1; arm2; }
#
# OpenSCAD is declarative — a shape is not a value, so this is a parser error and
# the part never exists. It survived being told not to in the prompt and survived
# having OpenSCAD's own complaint fed back, because that complaint is "syntax
# error, line 6" and names no cause. Recognising it here is what lets the retry
# say the actual lesson. Anchored to an assignment whose right-hand side opens
# with a geometry call, so `r = 2;` and `w = 40 - 2*r;` are untouched.
_GEOMETRY_AS_VALUE = re.compile(
    r"^\s*\w+\s*=\s*(?:cube|cylinder|sphere|square|circle|polygon|polyhedron|"
    r"union|difference|intersection|hull|minkowski|translate|rotate|scale|"
    r"mirror|linear_extrude|rotate_extrude|offset)\s*\(", re.M)
SLICE_TIMEOUT_S = 300


class PrinterBackend:
    """The seam a real printer plugs into later."""

    async def get_status(self) -> dict:
        raise NotImplementedError

    async def start_print(self, gcode_path: str) -> dict:
        raise NotImplementedError

    async def cancel_print(self) -> dict:
        raise NotImplementedError


class NoPrinterBackend(PrinterBackend):
    """The only implementation, on purpose."""

    async def get_status(self) -> dict:
        return {"connected": False, "error": "no printer configured"}

    async def start_print(self, gcode_path: str) -> dict:
        return {"error": "no printer configured"}

    async def cancel_print(self) -> dict:
        return {"error": "no printer configured"}


backend: PrinterBackend = NoPrinterBackend()


# ------------------------------------------------------------------ plumbing
def work_dir() -> Path:
    d = config.get("fabrication", "work_dir", default="") or str(APP_DIR / "fabrication")
    p = Path(d)
    p.mkdir(parents=True, exist_ok=True)
    return p


# Where these actually live, in the order worth trying. A CONFIGURED path always
# wins — but only if it exists.
#
# It has to work that way because config.json persists whatever the defaults were
# on the day the app first ran, and a stored value then beats every later change
# to the default in this file. That caught three separate things on 2026-09-02:
# his quiet hours stayed at 08:00 after being set to 05:30, and both of these
# binaries stayed at C:\Program Files after being installed to C:\AI — so
# "OpenSCAD is not installed" was reported on a machine where it plainly was.
# A stale path in config must degrade to "look elsewhere", never to "unavailable".
_SCAD_CANDIDATES = (
    r"C:\AI\OpenSCAD\openscad.exe",
    r"C:\Program Files\OpenSCAD\openscad.exe",
    r"C:\Program Files (x86)\OpenSCAD\openscad.exe",
)
_SLICER_CANDIDATES = (
    r"C:\AI\PrusaSlicer\prusa-slicer-console.exe",
    r"C:\AI\PrusaSlicer\prusa-slicer.exe",
    r"C:\Program Files\Prusa3D\PrusaSlicer\prusa-slicer-console.exe",
)


def _first_existing(configured: str, candidates: tuple, *which: str) -> str | None:
    if configured and os.path.exists(configured):
        return configured
    for c in candidates:
        if os.path.exists(c):
            return c
    for w in which:
        found = shutil.which(w)
        if found:
            return found
    return None


def openscad_path() -> str | None:
    return _first_existing(config.get("fabrication", "openscad_binary", default=""),
                           _SCAD_CANDIDATES, "openscad")


def slicer_path() -> str | None:
    return _first_existing(config.get("fabrication", "prusaslicer_binary", default=""),
                           _SLICER_CANDIDATES,
                           "prusa-slicer-console", "prusa-slicer", "PrusaSlicer")


def safe_name(name: str, fallback: str = "part") -> str:
    """A filename from something he said. Confined by construction: separators
    and traversal are stripped rather than escaped, so a generated name can never
    walk out of the work directory."""
    base = re.sub(r"[^A-Za-z0-9_-]+", "-", (name or "").strip()).strip("-")
    base = base[:48] or fallback
    return base


async def _run(args: list[str], timeout: int) -> tuple[int, str, str]:
    """A binary, off the event loop, with a deadline. Never raises.

    CREATE_NO_WINDOW, because every one of these is a CONSOLE program and this
    is the only launcher in the sidecar that was missing it. A render put a
    command prompt on his screen: the tier-4 reconstructor is a python.exe, so
    Windows gave it its own conhost and a window to go with it — on 2026-09-03,
    in the middle of him testing, while a duck rebuilt for three minutes. The
    per-part renders make it worse rather than better, since four OpenSCADs at
    once are four windows. Everything else here already passes this flag
    (llama_server, vision_server, search_brave_web, builtin, system_panel);
    this one just never did.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except FileNotFoundError:
        return 127, "", "binary not found"
    except Exception as e:
        return 1, "", str(e)
    def stop() -> None:
        try:
            if proc.returncode is None:
                proc.kill()
        except Exception:
            log.debug("could not kill the child", exc_info=True)
        # Close the pipes too, or the transport is reaped by the garbage
        # collector and complains about an I/O operation on a closed pipe.
        for stream in (proc.stdout, proc.stderr):
            try:
                if stream is not None:
                    stream.feed_eof()
            except Exception:
                pass

    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        stop()
        return 1, "", f"timed out after {timeout}s"
    except asyncio.CancelledError:
        # "STOP THAT" MUST ACTUALLY STOP IT. Cancelling the awaiting task does
        # not touch the child — proven on 2026-09-02, the process was still
        # running afterwards — so a cancelled tier-3 render would have kept
        # 1.7 GB of TripoSR weights and a core busy for another half minute
        # after he had been told it had stopped. He would have heard the fans.
        stop()
        raise
    return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")


# ------------------------------------------------------------------- parsing
_TIME_RE = re.compile(r"estimated printing time.*?=\s*([0-9hms ]+)", re.I)
_FIL_G_RE = re.compile(r"filament used \[g\]\s*=\s*([0-9.]+)", re.I)
_FIL_MM_RE = re.compile(r"filament used \[mm\]\s*=\s*([0-9.]+)", re.I)


def parse_slicer_output(text: str, gcode: Path | None = None) -> dict:
    """Real numbers out of PrusaSlicer. It prints some to stdout and writes the
    rest as comments in the G-code footer, so both are read — an estimate that
    silently comes back empty is the thing worth catching here."""
    blob = text or ""
    if gcode and gcode.exists():
        try:
            # 8 KB was not enough and produced a WRONG answer rather than a
            # missing one: PrusaSlicer writes the estimates and then dumps its
            # entire configuration after them — 353 lines for a cube — so the
            # last 8 KB lands inside the alphabetical settings block and the
            # numbers sit just above it. Measured on a real slice: the estimates
            # were at line 6061 of 6414. Read the last 512 KB, which covers any
            # plausible config dump, and the whole file when it is smaller.
            size = gcode.stat().st_size
            with open(gcode, "r", encoding="utf-8", errors="replace") as fh:
                fh.seek(max(0, size - 512 * 1024))
                blob += "\n" + fh.read()
        except OSError:
            log.debug("could not read the gcode footer", exc_info=True)
    out: dict = {}
    m = _TIME_RE.search(blob)
    if m:
        out["print_time"] = m.group(1).strip()
    m = _FIL_G_RE.search(blob)
    if m:
        out["filament_g"] = float(m.group(1))
    m = _FIL_MM_RE.search(blob)
    if m:
        out["filament_mm"] = float(m.group(1))
    return out


# --------------------------------------------------------------------- tools
async def generate_part(description: str, name: str = "",
                        retry_note: str = "", brief: str = "") -> dict:
    """OpenSCAD source for a simple part, rendered to STL.

    `retry_note` is what was wrong with the last attempt, fed back into the
    prompt. Asking the model the same question twice and hoping is not a retry.
    A retry note also SKIPS the template, because a template is deterministic:
    if it produced the wrong thing once it will produce it again.
    """
    desc = (description or "").strip()
    if not desc:
        return {"error": "what should I make, sir?"}
    exe = openscad_path()
    if not exe:
        return {"error": "OpenSCAD is not installed — set fabrication.openscad_binary",
                "unavailable": True}

    # THE FAST PATH, first. Most of what anyone asks a 3D printer for is
    # parametric — a cube, a plate, a spacer, a washer, a tube — and those need
    # no language model at all. Measured: 0.2 s against 27.5 s, and exact,
    # because the numbers come from his own sentence. `match` returns None for
    # anything it is not certain about, which is most requests.
    import parts_library
    templated = None if retry_note else parts_library.match(desc)
    if templated:
        base = safe_name(name or desc)
        d = work_dir()
        scad, stl = d / f"{base}.scad", d / f"{base}.stl"
        scad.write_text(templated.source, encoding="utf-8")
        rc, out, err = await _run([exe, "-o", str(stl), str(scad)], GEN_TIMEOUT_S)
        if rc == 0 and stl.exists():
            return {"scad": str(scad), "stl": str(stl), "from": "template",
                    "size_kb": round(stl.stat().st_size / 1024, 1),
                    "source": templated.source,
                    # The numbers WE chose, so they can be said out loud rather
                    # than discovered at the printer.
                    "chose": templated.defaults, "shape": templated.shape}
        # A template that will not build is a bug in the template, not in his
        # request — fall through to the model rather than refusing him.
        log.warning("template for %r did not build: %s", desc,
                    (err or out or "").strip()[:200])

    # The model already running, collected to a full reply — the same pattern
    # reminder_voice uses. Temperature is LOW here on purpose: this is not
    # creative writing, it is source that has to compile, and an invented
    # OpenSCAD function is a build failure rather than a turn of phrase.
    from llm.provider import local_llm
    prompt = (
        "Write OpenSCAD source for this part. Output ONLY code, no prose and no "
        "markdown fences. Use millimetres. Keep it simple and printable: no "
        "supports needed, nothing thinner than 1.2 mm, and a flat face on the bed. "
        # Fed back from a verification that failed. TalkCAD's idea: check the
        # result against the stated spec and say what was wrong rather than
        # asking again in the same words and hoping.
        + (f"A previous attempt was wrong: {retry_note}. Fix exactly that. "
           if retry_note else "")
        # WHAT THE WEB SAYS THE THING ACTUALLY IS. Nobody can recall the
        # dimensions of a Nintendo 2DS XL, and a baseball is 73 mm whether or
        # not the model remembers it. Facts first, then the request.
        + (f"Here is what the object actually is, researched: {brief} "
           "Use these shapes and these millimetres. "
           if brief else "")
        +
        # Start the file with $fn=48. OpenSCAD's default curve resolution gave a
        # 5 mm hole about a dozen segments, and the first hologram rendered on
        # 2026-09-02 showed them as visibly faceted — which is not a cosmetic
        # problem: a bolt does not fit a hexagon, and the slicer will happily
        # print exactly the polygon it was given.
        "Begin the file with the line: $fn = 48; so holes and fillets are round "
        "rather than faceted. "
        # THE THREE MISTAKES THIS MODEL ACTUALLY MAKES, measured by asking it for
        # rounded and chamfered parts and building what came back. Each line here
        # is a real build failure, not a precaution.
        #
        # 1. It writes OpenSCAD as if it were Python. "a bracket with a fillet"
        #    came back as `arm1 = cube([40,4,4]); arm2 = translate(...) arm2;`
        #    — geometry assigned to a variable, which is a parser error, and the
        #    part never existed.
        "OpenSCAD is declarative, not imperative: geometry is NOT a value. "
        "`a = cube([10,10,10]);` is a syntax error. Build shapes in place inside "
        "difference(), union(), hull() and modules. "
        # 2. It reaches for libraries it has seen online. None are installed.
        "There are NO libraries available: do not include or use BOSL2, "
        "Round-Anything, MCAD or dotSCAD. Only built-in OpenSCAD. "
        # 3. Its instinct for rounding is minkowski() with a sphere, which rounds
        #    the BOTTOM too — so the part rocks on the bed, needs supports, and
        #    grows by the radius in every direction. "a plate 40x30x5 with rounded
        #    corners" came back 11 mm thick with a domed underside, and built
        #    cleanly, which is the dangerous kind of wrong.
        "For rounded vertical corners use minkowski() with a THIN CYLINDER, never "
        "a sphere: minkowski() { cube([w-2*r, d-2*r, h]); cylinder(r=r, h=0.01); } "
        "— a sphere rounds the bottom face as well and the part will not sit flat "
        "on the bed. Remember minkowski grows the shape by r on every side, so "
        "subtract 2*r from the cube first to keep the finished size correct. "
        "For a chamfer, subtract a rotated cube with difference(), or hull() two "
        "stacked shapes. "
        # 4. It wraps the whole object in ONE module. "Iron man's arc reactor"
        #    came back as a single `module arc_reactor()` holding the rim, the
        #    coils and the core — correct, printable, and impossible to work
        #    with, because there is no rim in it to point at. He wants to zoom
        #    in on the helmet and read the helmet's dimensions, and nothing
        #    downstream can invent a component the source does not contain.
        "If the object has PARTS a person would name separately — a rim, a core, "
        "a housing, a lid, a handle, a base — give each one its own module and "
        "call them one after another at the top level, so each can be shown and "
        "measured on its own: `module rim() {...} module core() {...} rim(); "
        "translate([0,0,2]) core();`. Name them for what they ARE. "
        # ...and the counter-instruction, which matters as much. A cube wrapped
        # in a module and announced as an assembly is a worse answer than a cube.
        "But do NOT invent divisions: if it is one simple shape — a cube, a "
        "washer, a plate — write it directly with no modules at all. Only split "
        "it where the pieces are really distinct. "
        f"Part: {desc}")
    async def write_source(extra: str = "") -> tuple[str, str]:
        """One call to the model. Returns (source, why_not)."""
        try:
            code = ""
            # 2000, not 700. This is a REASONING model and max_tokens covers the
            # thinking as well as the answer: at 700 the chamfer request spent
            # every token on analysis and returned an empty string, which
            # surfaced as "the model returned no source" — a message about the
            # symptom that gave no hint of the cause. The same prompt finishes in
            # about 560 tokens when it is allowed to think first.
            async for ch in local_llm.stream(
                    [{"role": "user", "content": prompt + extra}],
                    max_tokens=2000,
                    sampling={"temperature": 0.2, "top_p": 0.9}):
                code += ch.text
                if ch.done:
                    break
        except Exception as e:
            return "", f"I couldn't write the model: {e}"
        code = re.sub(r"^```[a-zA-Z]*\n|```$", "", (code or "").strip(),
                      flags=re.M).strip()
        if not code:
            return "", "the model thought about it and never answered"
        return code, ""

    base = safe_name(name or desc)
    d = work_dir()
    scad, stl = d / f"{base}.scad", d / f"{base}.stl"

    # ONE RETRY ON A BUILD FAILURE, with OpenSCAD's own words fed back.
    #
    # This was previously terminal: source that did not compile ended the
    # request, and he got "OpenSCAD could not build that: Parser error" for a
    # bracket that was one line from working. The compiler has already said
    # precisely what is wrong and where, so handing that back is the cheapest
    # correction available — the same feedback loop `_verify_and_retry` uses for
    # a part that builds but comes out the wrong size.
    #
    # NOT retried when we are already a retry: create3d calls back in here with a
    # `retry_note`, and a retry of a retry is four model calls and a minute of
    # his time for a request that is plainly not landing.
    extra, last = "", ""
    for attempt in range(1 if retry_note else 2):
        code, why = await write_source(extra)
        if not code:
            return {"error": why}
        scad.write_text(code, encoding="utf-8")
        rc, out, err = await _run([exe, "-o", str(stl), str(scad)], GEN_TIMEOUT_S)
        if rc == 0 and stl.exists():
            break
        last = (err or out or "").strip()
        log.warning("OpenSCAD rejected attempt %d for %r: %s",
                    attempt + 1, desc, last[:200])
        # OpenSCAD's own message is often just "syntax error in file ..., line 6",
        # which names a position and no cause — fed back verbatim it produced the
        # SAME mistake one line lower on the retry. When we can see what the
        # mistake actually is, say that instead: we know this language and the
        # compiler is not going to explain it.
        bad = _GEOMETRY_AS_VALUE.search(code)
        if bad:
            extra = (" Your previous source did not compile because it assigned "
                     f"geometry to a variable: `{bad.group(0).strip()}...`. In "
                     "OpenSCAD that is a syntax error — a shape is not a value "
                     "and cannot be stored, passed or reassigned. Write the "
                     "shapes directly inside union(), difference() and hull(), "
                     "or wrap each one in its own module and CALL it. Variables "
                     "may only hold numbers, strings and vectors.")
        else:
            extra = (" Your previous source did not compile. OpenSCAD said: "
                     f"{last[:400]} . Write it again and fix exactly that.")
    else:
        return {"error": f"OpenSCAD could not build that: {last[:200]}",
                "scad": str(scad)}
    r = {"scad": str(scad), "stl": str(stl), "from": "model",
         "size_kb": round(stl.stat().st_size / 1024, 1), "source": code}
    return await _split_into_parts(exe, scad, stl, code, r)


async def _split_into_parts(exe, scad, stl, code: str, r: dict) -> dict:
    """Render each named component on its own, when the source has any.

    A part is a module CALLED AT THE TOP LEVEL of the file — see `assembly` for
    why that is parsed rather than asked for. Fewer than two means there is
    nothing to take apart, and the part behaves exactly as it always has.
    """
    import assembly

    parts = assembly.parts_in(code)
    if len(parts) < 2:
        return r
    if len(parts) > assembly.MAX_PARTS:
        log.info("%d parts is more than anyone can work with by voice; "
                 "keeping it whole", len(parts))
        return r

    dispatched = assembly.with_dispatcher(code, parts)
    scad.write_text(dispatched, encoding="utf-8")
    # Re-render the WHOLE thing from the file that is now on disk, so what he
    # sees and what `holo_edit` reads back cannot drift apart.
    rc, out, err = await _run(
        [exe, "-D", f'{assembly.PART_VAR}="all"', "-o", str(stl), str(scad)],
        GEN_TIMEOUT_S)
    if rc != 0 or not stl.exists():
        # The dispatcher did not build, so put the original back rather than
        # leaving him with a source that does not compile. He still gets his
        # part — just without pieces.
        log.warning("the part dispatcher would not build for %s: %s",
                    stl.name, (err or out or "").strip()[:200])
        scad.write_text(code, encoding="utf-8")
        await _run([exe, "-o", str(stl), str(scad)], GEN_TIMEOUT_S)
        return r

    # THESE ARE INDEPENDENT PROCESSES: a distinct output file each, from one
    # source nobody writes to while they run. In serial that was up to
    # twenty-four half-second renders back to back — twelve seconds of him
    # waiting for work the machine can do several at a time on sixteen cores.
    #
    # Bounded rather than unbounded, and that is the whole judgement here:
    # llama-server is on this same box holding the GPU and most of a working
    # set, and turning twenty-four OpenSCADs loose would take the machine away
    # from the thing that answers him. Four lanes is the compromise, and it is
    # config so it can be turned down without a build.
    lanes = asyncio.Semaphore(max(1, int(
        config.get("fabrication", "part_render_lanes", default=4) or 4)))

    async def render_one(p: dict) -> dict | None:
        out_stl = stl.with_suffix("")
        out_stl = out_stl.with_name(f"{out_stl.name}.{p['name']}.stl")
        # A STALE FILE WOULD PASS THE CHECK BELOW. OpenSCAD exits 0 and writes
        # nothing at all when the top-level object is empty, leaving whatever
        # was there before untouched — so last run's part gets accepted as this
        # one's. Removing it first makes "it exists" mean "this render made it".
        try:
            out_stl.unlink()
        except OSError:
            pass
        async with lanes:
            rc, out, err = await _run(
                [exe, "-D", f'{assembly.PART_VAR}="{p["name"]}"',
                 "-o", str(out_stl), str(scad)], GEN_TIMEOUT_S)
        if rc != 0 or not out_stl.exists():
            # "Current top level object is empty" comes back with exit 0, so
            # this is the ONLY signal that a named component built nothing.
            log.warning("part %s did not build: %s", p["name"],
                        (err or out or "").strip()[:160])
            return None
        return {"name": p["name"], "stl": str(out_stl)}

    # gather PRESERVES INPUT ORDER regardless of what finishes first, so the
    # parts stay in the order the source declares them — which is the order he
    # hears them read back.
    rendered = await asyncio.gather(*(render_one(p) for p in parts))

    made, empty = [], []
    for p, got in zip(parts, rendered):
        if got is None:
            empty.append(p["name"])
        else:
            made.append(got)

    # A COMPONENT THAT RENDERED NOTHING MEANS THE SOURCE IS WRONG ABOUT ITSELF,
    # and shipping the rest as an assembly hands him a model with a piece
    # missing and nothing to indicate it. This is how `base` and `ring` both
    # vanished while the whole render still looked correct.
    if empty:
        log.warning("%s built nothing; keeping %s whole",
                    ", ".join(empty), stl.name)
        for m in made:
            try:
                os.remove(m["stl"])
            except OSError:
                pass
        r["parts_incomplete"] = empty
        return r

    # One part that built is not an assembly; it is the whole thing with a
    # label, and calling it an assembly would put a "1 of 1" in front of him.
    if len(made) < 2:
        return r
    assembly.write_manifest(str(stl), made)
    r["parts"] = [m["name"] for m in made]
    r["part_count"] = len(made)
    return r


async def edit_part(change: str, name: str = "") -> dict:
    """Change a part he already has: "make the hole bigger", "twice as tall".

    THIS IS THE ONE THING ON THE STAGE THAT CHANGES THE REAL MODEL. Rotating,
    scaling and sectioning are all view state — the STL is untouched and its
    millimetres do not move. This rewrites the source and re-renders, so what
    comes back is a different part, and it says so.

    It works at all only because `generate_part` keeps the `.scad` beside the
    `.stl`. Editing a MESH — moving vertices to widen a hole — is a research
    problem with unreliable results; editing the source that produced it is a
    parameter change and a re-render, which either compiles or does not.

    So a part with no source cannot be edited, and is told so plainly rather
    than being silently approximated. That is every tier-3 and tier-4 part: a
    mesh from a photo has no parameters to change.

    THE PREVIOUS VERSION IS KEPT (`<name>.prev.scad`) so "no, put it back" is a
    file copy rather than an apology.
    """
    import assembly

    want = (change or "").strip()
    if not want:
        return {"error": "what should I change, sir?"}
    exe = openscad_path()
    if not exe:
        return {"error": "OpenSCAD is not installed — set fabrication.openscad_binary",
                "unavailable": True}

    d = work_dir()
    base = safe_name(name) if name else ""
    if not base:
        try:
            scads = sorted(d.glob("*.scad"), key=lambda f: f.stat().st_mtime)
            scads = [s for s in scads if not s.name.endswith(".prev.scad")]
            base = scads[-1].stem if scads else ""
        except OSError:
            base = ""
    if not base:
        return {"error": "I don't have a part to change, sir"}

    # A TRACED DESIGN IS EDITED BY ITS FEATURES. Decided by what the model is,
    # not by how he said it — "make the hole bigger" and "make his eyes smaller"
    # are one sentence to him.
    import create3d
    traced_stl = d / f"{base}.stl"
    kept = create3d.load_shapes(str(traced_stl)) if traced_stl.exists() else {}
    if kept.get("shapes"):
        import features
        pieces = features.label(kept["shapes"])
        targets = features.find(pieces, want)
        if not targets:
            # A WRONG GUESS CHANGES THE WRONG PART OF HIS DESIGN and he finds
            # out later, so say what there is instead.
            return {"error": (f"I can change the {features.describe(pieces)}, "
                              f"sir — which did you mean?"),
                    "can_change": sorted({p["name"] for p in pieces}),
                    "unchanged": True}
        factor = features.factor_from(want)
        if factor == 1.0:
            return {"error": "bigger or smaller, sir?", "unchanged": True}
        try:
            (d / f"{base}.prev.shapes.json").write_text(
                json.dumps(kept), encoding="utf-8")
        except OSError:
            log.debug("could not keep the previous design", exc_info=True)
        r = await create3d.rebuild_shapes(
            str(traced_stl), features.scaled(kept["shapes"], targets, factor),
            kept.get("thickness_mm", 3.0), kept.get("width_mm", 60.0))
        if r.get("error"):
            return {**r, "unchanged": True}
        changed = sorted({t["name"] for t in targets})
        out_d = {"name": base, "stl": str(traced_stl), "scad": r.get("scad", ""),
                 "changed": True, "feature": changed, "factor": round(factor, 2),
                 "spoken": (f"{' and '.join(changed)} "
                            f"{'smaller' if factor < 1 else 'bigger'}, sir.")}
        try:
            import meshio
            info = await asyncio.to_thread(meshio.describe, str(traced_stl))
            out_d["size_mm"] = info["size_mm"]
        except Exception:
            log.debug("could not measure the edited design", exc_info=True)
        await _reproject(base)
        return out_d

    scad = d / f"{base}.scad"
    if not scad.exists():
        return {"error": f"I don't have the source for {base}, sir — "
                         "it wasn't one I wrote, so there are no dimensions to change"}
    try:
        source = scad.read_text(encoding="utf-8")
    except OSError as e:
        return {"error": f"I couldn't read that part's source: {e}"}

    from llm.provider import local_llm
    prompt = (
        "Here is OpenSCAD source for a part:\n\n"
        f"{source}\n\n"
        f"Change it so that: {want}\n\n"
        "Output ONLY the complete revised OpenSCAD source — no prose, no markdown "
        "fences, no explanation. Change as little as possible: keep the same "
        "structure, the same variable names and everything he did not ask about. "
        "Keep the $fn line. Millimetres throughout.")
    try:
        code = ""
        async for ch in local_llm.stream([{"role": "user", "content": prompt}],
                                         max_tokens=900,
                                         sampling={"temperature": 0.15, "top_p": 0.9}):
            code += ch.text
            if ch.done:
                break
    except Exception as e:
        return {"error": f"I couldn't work out that change: {e}"}
    code = re.sub(r"^```[a-zA-Z]*\n|```$", "", (code or "").strip(), flags=re.M).strip()
    if not code:
        return {"error": "the model returned no source"}
    if code == source.strip():
        return {"error": "that would leave it exactly as it is, sir"}

    # Keep the old one BEFORE overwriting, and render to a scratch file: a
    # failed edit must not leave him with neither the new part nor the old.
    prev = d / f"{base}.prev.scad"
    tmp_scad, tmp_stl = d / f"{base}.next.scad", d / f"{base}.next.stl"
    tmp_scad.write_text(code, encoding="utf-8")
    rc, out, err = await _run([exe, "-o", str(tmp_stl), str(tmp_scad)], GEN_TIMEOUT_S)
    if rc != 0 or not tmp_stl.exists():
        for f in (tmp_scad, tmp_stl):
            try:
                f.unlink()
            except OSError:
                pass
        return {"error": f"that change wouldn't build: {(err or out or '').strip()[:200]}",
                "unchanged": True}

    stl = d / f"{base}.stl"

    # Measure the OLD part before it is replaced. Being told "it was six
    # millimetres thick, it's twelve now" is the useful half of an A/B, and it is
    # the half that works when he is not looking at the screen.
    import meshio
    was = None
    if stl.exists():
        try:
            was = (await asyncio.to_thread(meshio.describe, str(stl)))["size_mm"]
        except Exception:
            log.debug("could not measure the part before editing", exc_info=True)

    try:
        prev.write_text(source, encoding="utf-8")
        tmp_scad.replace(scad)
        tmp_stl.replace(stl)
    except OSError as e:
        return {"error": f"I couldn't save that change: {e}", "unchanged": True}

    out_d = {"name": base, "scad": str(scad), "stl": str(stl), "source": code,
             "previous": str(prev), "changed": True}
    if was:
        out_d["was_size_mm"] = was
    try:
        info = await asyncio.to_thread(meshio.describe, str(stl))
        w, h, dp = info["size_mm"]
        out_d["size_mm"] = info["size_mm"]
        out_d["spoken_size"] = f"{round(w)} by {round(h)} by {round(dp)} millimetres"
    except Exception:
        log.debug("could not measure the edited part", exc_info=True)

    # THE PARTS HAVE TO BE REBUILT, or the old ones keep being served. They are
    # still on disk and still named correctly, so nothing downstream can tell
    # that the ring he just enlarged is the ring from before the edit.
    out_d = await _split_into_parts(exe, scad, stl, code, out_d)
    if not out_d.get("parts"):
        assembly.clear_manifest(str(stl))

    # WHAT CHANGED, WRITTEN DOWN. "Made the outer ring 90 mm" is the half of
    # this he cannot reconstruct a week later, and it is gone the moment the
    # conversation ends unless it is recorded as it happens.
    try:
        from tools.workspace_tools import active, file_in_project, project_note
        if active():
            was = out_d.get("was_size_mm")
            now = out_d.get("size_mm")
            said = f"{base}: {want}"
            if was and now and was != now:
                said += (f" (was {was[0]:.0f}x{was[1]:.0f}x{was[2]:.0f}, "
                         f"now {now[0]:.0f}x{now[1]:.0f}x{now[2]:.0f} mm)")
            await project_note(text=said, heading="")
            await file_in_project(stl_path=str(stl))
    except Exception:
        log.warning("could not record the edit in the project", exc_info=True)

    await _reproject(base)
    return out_d


async def _reproject(base: str) -> None:
    """If that part is on the stage, put the new one up in its place.

    An edit he cannot see is an edit he has to ask to see. Only when it is
    ALREADY up: re-rendering a part he is not looking at should not seize the
    screen, the same rule `inspect_part` follows.
    """
    try:
        from tools.holo_tools import current, show_hologram
        if (current().get("name") or "") == base:
            await show_hologram(name=base)
    except Exception:
        log.debug("could not re-project the edited part", exc_info=True)


async def revert_part(name: str = "") -> dict:
    """Put the last edit back. A file copy, because the previous source was kept."""
    d = work_dir()
    base = safe_name(name) if name else ""
    if not base:
        try:
            prevs = sorted(d.glob("*.prev.scad"), key=lambda f: f.stat().st_mtime)
            base = prevs[-1].name[:-len(".prev.scad")] if prevs else ""
        except OSError:
            base = ""
    prev = d / f"{base}.prev.scad" if base else None
    if not prev or not prev.exists():
        return {"error": "I don't have an earlier version of that, sir"}
    exe = openscad_path()
    if not exe:
        return {"error": "OpenSCAD is not installed", "unavailable": True}
    scad, stl = d / f"{base}.scad", d / f"{base}.stl"
    # Swap, rather than overwrite: undoing an undo is the next thing he asks for.
    current_src = scad.read_text(encoding="utf-8") if scad.exists() else ""
    scad.write_text(prev.read_text(encoding="utf-8"), encoding="utf-8")
    rc, out, err = await _run([exe, "-o", str(stl), str(scad)], GEN_TIMEOUT_S)
    if rc != 0:
        if current_src:
            scad.write_text(current_src, encoding="utf-8")
        return {"error": f"the earlier version wouldn't build: {(err or out or '').strip()[:200]}"}
    if current_src:
        prev.write_text(current_src, encoding="utf-8")
    await _reproject(base)
    return {"name": base, "stl": str(stl), "reverted": True}


def mesh_warning_for(stl_path: str) -> str | None:
    """What is wrong with this mesh, in a sentence, or None if nothing is.

    Looked at BEFORE the file is handed to the slicer. Non-manifold geometry is
    the commonest reason a slicer refuses a file, but the failure mode that
    actually costs something is the other one: PrusaSlicer accepts a leaky mesh,
    repairs it by its own rules, and prints a part that is not quite the part he
    asked for. So this WARNS rather than refuses — the slicer's repair is usually
    right, and refusing his file outright would be worse than telling him. It
    reports only; nothing is written back over his model.

    Its own function so the sentence can be gated without a slicer installed.
    """
    try:
        import meshio
        import printcheck
        integ = printcheck.integrity(meshio.load_stl(str(stl_path)))
    except Exception:
        log.debug("pre-slice integrity check failed", exc_info=True)
        return None
    if integ.get("sliceable") is not False:
        return None
    bits = []
    if integ.get("watertight") is False:
        n = integ.get("open_edges")
        bits.append(f"{n} open edges" if n else "it isn't closed")
    if integ.get("winding_consistent") is False:
        bits.append("inconsistent normals")
    return ("the mesh isn't watertight — " + ", ".join(bits) +
            " — so the slicer will repair it its own way")


async def slice_part(stl_path: str) -> dict:
    """PrusaSlicer, headless, with the real time and filament estimates."""
    stl = Path(str(stl_path or "")).expanduser()
    if not stl.exists() or stl.suffix.lower() != ".stl":
        return {"error": f"no STL at {stl_path}"}
    exe = slicer_path()
    if not exe:
        return {"error": "PrusaSlicer is not installed — set fabrication.prusaslicer_binary",
                "unavailable": True}

    mesh_warning = await asyncio.to_thread(mesh_warning_for, str(stl))
    gcode = work_dir() / (stl.stem + ".gcode")
    args = [exe, "-g", str(stl), "--output", str(gcode)]
    profile = config.get("fabrication", "slicer_profile", default="")
    if not profile:
        # Frozen, the profile lives under sys._MEIPASS/profiles — the same shape
        # the models use. Checking only the source tree would silently fall back
        # to PrusaSlicer's own defaults in the shipped build, and the estimates
        # would quietly be for a different printer than the one described.
        import sys as _sys
        here = getattr(_sys, "_MEIPASS", None)
        for cand in ([Path(here) / "profiles" / "generic_fdm_0.4.ini"] if here else []) + \
                    [Path(__file__).resolve().parent.parent / "profiles" / "generic_fdm_0.4.ini"]:
            if cand.exists():
                profile = str(cand)
                break
    if profile and os.path.exists(profile):
        args[1:1] = ["--load", profile]      # before -g, as PrusaSlicer expects
    rc, out, err = await _run(args, SLICE_TIMEOUT_S)
    if rc != 0 or not gcode.exists():
        return {"error": f"the slicer refused that: {(err or out or '').strip()[:200]}"}
    est = parse_slicer_output(out + "\n" + err, gcode)
    if not est:
        # The file sliced but no numbers came back — say so rather than implying
        # a successful estimate that does not exist.
        return {"gcode": str(gcode),
                "warning": "sliced, but no estimate was reported",
                **({"mesh_warning": mesh_warning} if mesh_warning else {})}
    return {"gcode": str(gcode), **est,
            **({"mesh_warning": mesh_warning} if mesh_warning else {})}


async def printer_status() -> dict:
    return await backend.get_status()


def register_all() -> None:
    registry.register(Tool(
        name="generate_part",
        description="Turn a description of a simple part into a 3D model (OpenSCAD -> STL). "
                    "Local file creation only; nothing is printed.",
        parameters={"type": "object", "properties": {
            "description": {"type": "string"},
            "name": {"type": "string"}}, "required": ["description"]},
        risk=Risk.LOW, handler=generate_part, timeout=GEN_TIMEOUT_S + 30))
    registry.register(Tool(
        name="edit_part",
        description="Change a part he already has, by rewriting its OpenSCAD source and "
                    "re-rendering: 'make the hole bigger', 'twice as tall', 'round the "
                    "corners'. This CHANGES THE REAL MODEL on disk, unlike holo_control "
                    "which only moves the view. Only works on parts JARVIS generated — a "
                    "mesh from a photo has no source to edit, and it says so.",
        parameters={"type": "object", "properties": {
            "change": {"type": "string", "description": "what to change, in his words"},
            "name": {"type": "string", "description": "which part; the newest if omitted"}},
            "required": ["change"]},
        risk=Risk.LOW, handler=edit_part, timeout=GEN_TIMEOUT_S + 30))
    registry.register(Tool(
        name="revert_part",
        description="Undo the last edit to a part and put the previous version back.",
        parameters={"type": "object", "properties": {
            "name": {"type": "string"}}, "required": []},
        risk=Risk.LOW, handler=revert_part, timeout=GEN_TIMEOUT_S + 30))
    registry.register(Tool(
        name="slice_part",
        description="Slice an STL into G-code and report the real print time and filament "
                    "estimate from the slicer.",
        parameters={"type": "object", "properties": {
            "stl_path": {"type": "string"}}, "required": ["stl_path"]},
        risk=Risk.LOW, handler=slice_part, timeout=SLICE_TIMEOUT_S + 30))
    registry.register(Tool(
        name="printer_status",
        description="Whether a 3D printer is connected and what it is doing.",
        parameters={"type": "object", "properties": {}, "required": []},
        risk=Risk.SAFE, handler=printer_status, timeout=20))
