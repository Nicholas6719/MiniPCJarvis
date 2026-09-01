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


def openscad_path() -> str | None:
    p = config.get("fabrication", "openscad_binary", default="")
    if p and os.path.exists(p):
        return p
    return shutil.which("openscad")


def slicer_path() -> str | None:
    p = config.get("fabrication", "prusaslicer_binary", default="")
    if p and os.path.exists(p):
        return p
    return (shutil.which("prusa-slicer-console") or shutil.which("prusa-slicer")
            or shutil.which("PrusaSlicer"))


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
            with open(gcode, "r", encoding="utf-8", errors="replace") as fh:
                fh.seek(max(0, gcode.stat().st_size - 8192))
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

    # The model already running, collected to a full reply — the same pattern
    # reminder_voice uses. Temperature is LOW here on purpose: this is not
    # creative writing, it is source that has to compile, and an invented
    # OpenSCAD function is a build failure rather than a turn of phrase.
    from llm.provider import local_llm
    prompt = (
        "Write OpenSCAD source for this part. Output ONLY code, no prose and no "
        "markdown fences. Use millimetres. Keep it simple and printable: no "
        "supports needed, nothing thinner than 1.2 mm, and a flat face on the bed. "
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
    return {"scad": str(scad), "stl": str(stl),
            "size_kb": round(stl.stat().st_size / 1024, 1), "source": code}


async def slice_part(stl_path: str) -> dict:
    """PrusaSlicer, headless, with the real time and filament estimates."""
    stl = Path(str(stl_path or "")).expanduser()
    if not stl.exists() or stl.suffix.lower() != ".stl":
        return {"error": f"no STL at {stl_path}"}
    exe = slicer_path()
    if not exe:
        return {"error": "PrusaSlicer is not installed — set fabrication.prusaslicer_binary",
                "unavailable": True}
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
        return {"gcode": str(gcode), "warning": "sliced, but no estimate was reported"}
    return {"gcode": str(gcode), **est}


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
