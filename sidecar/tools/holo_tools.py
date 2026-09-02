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
import os
from pathlib import Path

from events import bus
from tools.registry import Risk, Tool, registry

log = logging.getLogger("jarvis.tools.holo")

# The model currently on the stage. The HUD asks for it by name rather than
# being handed a path it could then read anything with.
_current: dict = {}


def current() -> dict:
    return dict(_current)


def _resolve(path: str) -> Path | None:
    """Only files we made, or a path he named outright. Never a traversal."""
    p = Path(str(path or "")).expanduser()
    try:
        return p.resolve(strict=True)
    except OSError:
        return None


async def show_hologram(path: str = "", name: str = "") -> dict:
    """Project a model. `path` is an STL; `name` picks one from the work folder."""
    from tools.fabrication import work_dir

    target: Path | None = None
    if path:
        target = _resolve(path)
    elif name:
        cand = work_dir() / f"{Path(name).stem}.stl"
        target = _resolve(str(cand))
    else:
        # Nothing named: the newest thing he made, which is almost always what
        # "show me the bracket" means right after making one.
        try:
            stls = sorted(work_dir().glob("*.stl"), key=lambda f: f.stat().st_mtime)
            target = _resolve(str(stls[-1])) if stls else None
        except OSError:
            target = None
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
        name="hide_hologram",
        description="Take the hologram down.",
        parameters={"type": "object", "properties": {}, "required": []},
        risk=Risk.SAFE, handler=hide_hologram, timeout=20))
