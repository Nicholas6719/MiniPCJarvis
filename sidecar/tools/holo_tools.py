"""The holographic stage: put a model up, and know what it is.

Phase A. This opens the stage and reports the mesh; the HUD fetches the geometry
itself from /holo/geometry because a few hundred kilobytes of float list has no
business travelling through a tool result and into the model's context.

The tool returns MILLIMETRES and a triangle count rather than a bare "ok",
because those are the numbers JARVIS speaks — "ninety-six millimetres across,
sir" — and the numbers the print checks in phase B build on. STL carries no
units; every slicer treats them as millimetres and so does this.
"""
from __future__ import annotations

import logging
from pathlib import Path

from events import bus
from tools.registry import Risk, Tool, registry

log = logging.getLogger("jarvis.tools.holo")

# The model currently on the stage. The HUD asks for it by name rather than
# being handed a path it could then read anything with.
_current: dict = {}

# The last print check per part. The overhang triangles are geometry, not a tool
# result: they go to the HUD through /holo/printcheck the same way the mesh does,
# because a few thousand floats have no business in the model's context.
#
# BOUNDED. A check on a badly-supported mesh is thousands of floats, and the
# sidecar is resident for days; keeping one per part he ever asks about is a slow
# leak in a process that must not acquire one.
_CHECK_CACHE = 8
_checks: dict = {}


def current() -> dict:
    return dict(_current)


def check_for(name: str) -> dict:
    return _checks.get(name) or {}


def _resolve(path: str) -> Path | None:
    """Only files we made, or a path he named outright. Never a traversal."""
    p = Path(str(path or "")).expanduser()
    try:
        return p.resolve(strict=True)
    except OSError:
        return None


def _pick(path: str = "", name: str = "") -> Path | None:
    """Which STL he means: one he named, one from the work folder, or the newest.

    Shared by projecting and inspecting, so "show me the bracket" and "will the
    bracket print" can never disagree about which file they are talking about.
    """
    from tools.fabrication import work_dir

    if path:
        return _resolve(path)
    if name:
        return _resolve(str(work_dir() / f"{Path(name).stem}.stl"))
    # Nothing named: the newest thing he made, which is almost always what
    # "show me the bracket" means right after making one.
    try:
        stls = sorted(work_dir().glob("*.stl"), key=lambda f: f.stat().st_mtime)
        return _resolve(str(stls[-1])) if stls else None
    except OSError:
        return None


async def show_hologram(path: str = "", name: str = "") -> dict:
    """Project a model. `path` is an STL; `name` picks one from the work folder."""
    target = _pick(path, name)
    if target is None or target.suffix.lower() != ".stl":
        return {"error": "I don't have a model to project, sir"}

    import meshio
    try:
        info = meshio.describe(str(target))
    except meshio.BadMesh as e:
        return {"error": str(e)}
    except Exception as e:
        log.exception("hologram describe failed")
        return {"error": f"I couldn't read that model: {e}"}
    info.pop("_tris", None)
    info.pop("_edges", None)

    _current.clear()
    _current.update(info)
    _current["name"] = target.stem

    # The stage opens because the model is ready, the same way the camera panel
    # appears when the device opens rather than waiting to be asked separately.
    await bus.emit("hologram", action="show", name=target.stem,
                   triangles=info["triangles"], size_mm=info["size_mm"])
    w, h, d = info["size_mm"]
    return {"name": target.stem, "triangles": info["triangles"],
            "size_mm": info["size_mm"],
            "spoken_size": f"{round(w)} by {round(h)} by {round(d)} millimetres",
            "on_stage": True}


async def inspect_part(path: str = "", name: str = "") -> dict:
    """Will it print? Overhangs, bed fit, mesh integrity, and a wall estimate.

    Everything expensive runs OFF the event loop. A mesh of any size is a couple
    of hundred milliseconds of numpy, and a couple of hundred milliseconds is
    long enough to be heard as a stutter in the middle of him speaking — the
    lesson from the forty-minute freeze, applied before it can cost anything.
    """
    import asyncio

    target = _pick(path, name)
    if target is None or target.suffix.lower() != ".stl":
        return {"error": "I don't have a model to check, sir"}

    import meshio
    import printcheck

    def work() -> dict:
        tris = meshio.load_stl(str(target))
        flat = tris.reshape(-1, 3)
        lo, hi = flat.min(axis=0), flat.max(axis=0)
        rep = printcheck.report(tris, (hi - lo).tolist())
        # The renderer is handed geometry already centred on the model's middle
        # (meshio.to_payload does the same), so the overhang triangles are
        # centred HERE rather than leaving the HUD to work out where in space the
        # exporter happened to put the part. Two places deriving the same origin
        # is two places to get it wrong.
        centre = (lo + hi) / 2.0
        rep["_overhang_positions"] = (
            (tris[printcheck.overhang_mask(tris)].reshape(-1, 3) - centre)
            .astype("float32").round(3).ravel().tolist())
        return rep

    try:
        rep = await asyncio.to_thread(work)
    except meshio.BadMesh as e:
        return {"error": str(e)}
    except Exception as e:
        log.exception("inspect_part failed")
        return {"error": f"I couldn't check that model: {e}"}

    # A slice beside it means real layers rather than an estimate of them.
    gpath = target.with_suffix(".gcode")
    layers = None
    if gpath.exists():
        try:
            import gcode
            layers = await asyncio.to_thread(gcode.summary, str(gpath))
        except Exception:
            log.debug("gcode summary failed", exc_info=True)

    _checks.pop(target.stem, None)          # re-inserted below, so it is newest
    _checks[target.stem] = {"report": rep, "gcode": str(gpath) if layers else ""}
    while len(_checks) > _CHECK_CACHE:
        _checks.pop(next(iter(_checks)))

    # The HUD paints the overhang faces red on whatever is already on the stage;
    # it does not open the stage. Inspecting a part he is not looking at should
    # answer him, not seize the screen.
    await bus.emit("hologram", action="inspect", name=target.stem,
                   overhang_faces=rep["overhangs"]["faces"],
                   layers=(layers or {}).get("count"))

    out = {"name": target.stem,
           "size_mm": rep["size_mm"],
           "fits_bed": rep["bed"]["fits"],
           "overhang_faces": rep["overhangs"]["faces"],
           "worst_overhang_deg": rep["overhangs"]["worst_deg"],
           "thinnest_wall_mm_estimate": rep["wall"].get("estimate_mm"),
           "sliceable": rep["integrity"].get("sliceable"),
           "spoken": printcheck.spoken(rep)}
    if layers:
        out["layers"] = layers["count"]
        out["layer_height_mm"] = layers["layer_height"]
    return out


async def hide_hologram() -> dict:
    _current.clear()
    await bus.emit("hologram", action="hide")
    return {"on_stage": False}


def register_all() -> None:
    registry.register(Tool(
        name="show_hologram",
        description="Project a 3D model as a hologram in the HUD. Use ONLY when he asks "
                    "for a hologram, or to see a part he has made in 3D — never for "
                    "'show me X' meaning pictures, which is show_images.",
        parameters={"type": "object", "properties": {
            "path": {"type": "string", "description": "an STL file path"},
            "name": {"type": "string", "description": "a part name from the work folder"}},
            "required": []},
        risk=Risk.SAFE, handler=show_hologram, timeout=60))
    registry.register(Tool(
        name="inspect_part",
        description="Check whether a 3D model will print: bed fit, overhangs needing "
                    "supports, mesh integrity, and an ESTIMATE of the thinnest wall. "
                    "The wall figure is sampled rather than measured and can miss a "
                    "thin feature, so report it as an estimate. Use for 'will this "
                    "print', 'does it fit', 'check that part'.",
        parameters={"type": "object", "properties": {
            "path": {"type": "string", "description": "an STL file path"},
            "name": {"type": "string", "description": "a part name from the work folder"}},
            "required": []},
        risk=Risk.SAFE, handler=inspect_part, timeout=120))
    registry.register(Tool(
        name="hide_hologram",
        description="Take the hologram down.",
        parameters={"type": "object", "properties": {}, "required": []},
        risk=Risk.SAFE, handler=hide_hologram, timeout=20))
