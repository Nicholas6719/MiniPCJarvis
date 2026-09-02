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
import logging
import os
import re
import shutil
from pathlib import Path

from config import APP_DIR, config
from tools.registry import Risk, Tool, registry

log = logging.getLogger("jarvis.tools.fabrication")

GEN_TIMEOUT_S = 120
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
    """A binary, off the event loop, with a deadline. Never raises."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    except FileNotFoundError:
        return 127, "", "binary not found"
    except Exception as e:
        return 1, "", str(e)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            log.debug("could not kill a timed-out slicer", exc_info=True)
        return 1, "", f"timed out after {timeout}s"
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
async def generate_part(description: str, name: str = "") -> dict:
    """OpenSCAD source for a simple part, rendered to STL."""
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
    templated = parts_library.match(desc)
    if templated:
        base = safe_name(name or desc)
        d = work_dir()
        scad, stl = d / f"{base}.scad", d / f"{base}.stl"
        scad.write_text(templated, encoding="utf-8")
        rc, out, err = await _run([exe, "-o", str(stl), str(scad)], GEN_TIMEOUT_S)
        if rc == 0 and stl.exists():
            return {"scad": str(scad), "stl": str(stl), "from": "template",
                    "size_kb": round(stl.stat().st_size / 1024, 1),
                    "source": templated}
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
        # Start the file with $fn=48. OpenSCAD's default curve resolution gave a
        # 5 mm hole about a dozen segments, and the first hologram rendered on
        # 2026-09-02 showed them as visibly faceted — which is not a cosmetic
        # problem: a bolt does not fit a hexagon, and the slicer will happily
        # print exactly the polygon it was given.
        "Begin the file with the line: $fn = 48; so holes and fillets are round "
        "rather than faceted. "
        f"Part: {desc}")
    try:
        code = ""
        async for ch in local_llm.stream([{"role": "user", "content": prompt}],
                                         max_tokens=700,
                                         sampling={"temperature": 0.2, "top_p": 0.9}):
            code += ch.text
            if ch.done:
                break
    except Exception as e:
        return {"error": f"I couldn't write the model: {e}"}
    code = re.sub(r"^```[a-zA-Z]*\n|```$", "", (code or "").strip(), flags=re.M).strip()
    if not code:
        return {"error": "the model returned no source"}

    base = safe_name(name or desc)
    d = work_dir()
    scad, stl = d / f"{base}.scad", d / f"{base}.stl"
    scad.write_text(code, encoding="utf-8")
    rc, out, err = await _run([exe, "-o", str(stl), str(scad)], GEN_TIMEOUT_S)
    if rc != 0 or not stl.exists():
        return {"error": f"OpenSCAD could not build that: {(err or out or '').strip()[:200]}",
                "scad": str(scad)}
    return {"scad": str(scad), "stl": str(stl), "from": "model",
            "size_kb": round(stl.stat().st_size / 1024, 1), "source": code}


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
