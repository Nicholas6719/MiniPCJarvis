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

import asyncio
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
    from tools.fabrication import safe_name, work_dir

    if path:
        return _resolve(path)
    if name:
        # safe_name, not Path().stem — that is what generate_part writes files
        # with, so "bracket v2" is on disk as bracket-v2.stl. Using the raw stem
        # here made the routing slot (which does use safe_name to check the file
        # exists) and this lookup disagree about the same part.
        return _resolve(str(work_dir() / f"{safe_name(Path(name).stem)}.stl"))
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
        # He NAMED something and it isn't here. "I don't have a model to project"
        # is true and useless: the obvious next thing is to make one, and the
        # machinery to say how long that takes and ask already exists. Offered
        # only when he named it — with no name the newest part is meant, and a
        # missing one there means the work folder is simply empty.
        if name and not path:
            import create3d
            import render_estimates as est
            t = create3d.choose_tier(name, "")
            if not (t in (3, 4) and not create3d.available().get(t)):
                secs = est.estimate(t)
                return {"_ask": {
                    "subject": name,
                    "question": (f"I don't have {'a' if name[:1].lower() not in 'aeiou' else 'an'} "
                                 f"{name}, sir. I could make one — {est.spoken(secs)}"
                                 f"{est.confidence_note(t)}. Shall I?"),
                    "tool": "make_hologram",
                    "args": {"description": name, "tier": t, "name": name,
                             "confirmed": True},
                }}
        return {"error": "I don't have a model to project, sir"}

    import asyncio

    import meshio
    try:
        # OFF THE EVENT LOOP. Parsing an STL, welding its vertices and finding its
        # separate bodies is hundreds of milliseconds on a real part and seconds
        # on a photo-derived one — and the event loop is where he waits for
        # answers. This was running inline; it is the forty-minute-freeze lesson
        # in miniature, and cheaper to fix than to diagnose later.
        info = await asyncio.to_thread(meshio.describe, str(target))
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
        tris = meshio.load(str(target))
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


_ACTIONS = ("rotate", "flip", "scale", "section", "explode", "reset", "fit",
            "layers", "solid", "layer")


def _sliced(name: str) -> bool:
    """Is there real G-code for this part? The file on disk is the only truth."""
    if not name:
        return False
    try:
        from tools.fabrication import safe_name, work_dir
        return (work_dir() / f"{safe_name(name)}.gcode").exists()
    except Exception:
        return False

# What each control turns into, said aloud. Short on purpose: he is watching the
# thing move, so the sentence is an acknowledgement, not a description.
_AXIS_SAID = {"x": "forwards", "y": "sideways", "z": "round"}


async def holo_control(action: str = "", axis: str = "", degrees: float = 0.0,
                       factor: float = 0.0, at: float = 0.5,
                       layer: int = -1, delta: int = 0,
                       phrase: str = "") -> dict:
    """Move the model that is already on the stage.

    NOTHING HERE CHANGES THE MODEL. Rotation, scale and the section cut are all
    view state: the STL on disk is untouched, and the millimetres `inspect_part`
    reports do not move because he turned it. Changing the real part is
    `edit_part`, which rewrites the source and re-renders and says so. Keeping
    that line sharp matters more here than anywhere else in the app — this is a
    part he is about to spend an hour printing.
    """
    if not _current:
        return {"error": "there's nothing on the stage to move, sir"}

    act = (action or "").strip().lower()
    said = phrase or ""
    if act not in _ACTIONS:
        # The model may hand us the sentence instead of an action; the skills
        # parse it too, and both paths land on the same parser rather than on
        # two that can drift apart. An unrecognised sentence is an admitted miss,
        # never a guess — falling through to "rotate" made "reset it" spin the
        # model, which he then has to undo.
        import holo_angles
        act = holo_angles.parse_action(said) or ""
        if act not in _ACTIONS:
            # `!r` in an f-string applies to the whole conditional, not the last
            # branch, so this said: I'm not sure what to do with 'that', sir.
            what = action.strip() if action else "that"
            return {"error": f"I'm not sure what to do with {what}, sir"}

    payload: dict = {"action": act}
    spoken = "Done, sir."

    if act in ("rotate", "flip"):
        import holo_angles
        ax = (axis or "").lower() or (holo_angles.parse_axis(said) if said else "z")
        deg = degrees or (holo_angles.parse_degrees(said) if said else
                          (180.0 if act == "flip" else holo_angles.DEFAULT_DEGREES))
        if act == "flip":
            ax = ax or "x"
        payload.update({"action": "rotate", "axis": ax, "degrees": float(deg)})
        spoken = (f"Turning it {abs(deg):.0f} degrees {_AXIS_SAID.get(ax, 'round')}, sir."
                  if abs(deg) != 360 else "Right the way round, sir.")
    elif act == "scale":
        import holo_angles
        f = factor or (holo_angles.parse_scale(said) or 1.5)
        payload["factor"] = float(f)
        spoken = "Closer, sir." if f > 1 else "Backing off, sir."
    elif act == "section":
        import holo_angles
        sec = ({"axis": axis, "at": at} if axis else None) or \
            holo_angles.parse_section(said) or {"axis": "z", "at": 0.5}
        payload.update(sec)
        spoken = "Cutting it open, sir."
    elif act == "explode":
        # An exploded view of one solid body is one solid body, moved. Saying
        # "separating it" and then showing him nothing move is worse than saying
        # there is nothing to separate.
        n = int(_current.get("body_count") or 1)
        if n < 2:
            return {"error": "it's a single body, sir — there's nothing to separate"}
        spoken = f"Separating the {n} parts, sir."
    elif act == "reset":
        spoken = "Back as it was, sir."
    elif act == "fit":
        spoken = "Framing it, sir."
    elif act == "layer":
        # Scrubbing through the sliced toolpath, the way every slicer does it.
        # Drawing all hundred layers at once is why a cube looked like a solid
        # green block; going up through them is how a toolpath is actually read.
        import holo_angles
        if not _sliced(_current.get("name", "")):
            return {"error": "that part hasn't been sliced yet, sir"}
        # The slots parse the sentence and pass the answer down; re-parsing here
        # is the fallback for when the MODEL calls the tool with an action and no
        # numbers. Both roads lead to the same parser, which is the whole reason
        # holo_angles exists as its own module.
        if delta:
            want = {"delta": int(delta)}
        elif layer != -1:
            want = {"layer": int(layer)}
        else:
            want = holo_angles.parse_layer(said) or {"layer": -1}
        payload.update(want)
        spoken = ("Layer by layer, sir." if "delta" in want
                  else "The top, sir." if want.get("layer") == -1
                  else "The first layer, sir." if want.get("layer") == 0
                  else f"Layer {want['layer']}, sir.")
    elif act in ("layers", "solid"):
        payload["action"] = "layers"
        payload["on"] = act == "layers"
        # Ask the DISK whether it has been sliced, not the in-memory check cache.
        # The cache is only populated by `inspect_part`, so a part sliced
        # yesterday would have been told it was never sliced at all after a
        # restart — and the cache is bounded, so it forgets anyway.
        if act == "layers" and not _sliced(_current.get("name", "")):
            return {"error": "that part hasn't been sliced yet, sir — "
                             "I'd need to slice it before I can show you the layers"}
        spoken = "The toolpath, sir." if act == "layers" else "Back to the model, sir."

    await bus.emit("holo_control", name=_current.get("name", ""), **payload)
    return {"on_stage": True, "applied": payload, "spoken": spoken,
            "note": "view only — the model on disk is unchanged"}


async def hand_control(on: bool = True) -> dict:
    """Watch his hands and let them move the model. Off is the resting state."""
    from hand_control import control
    if not on:
        r = control.disarm("he asked")
        await bus.emit("hands", action="off")
        return {**r, "spoken": "Hands off, sir." if r.get("was")
                else "They weren't on, sir."}
    # TURN THE CAMERA ON OURSELVES. It used to refuse and tell him to say "turn
    # the camera on" as a separate sentence, so that the decision stayed his —
    # but the decision IS his: "control it with my hands" is an explicit request
    # for a camera-driven feature, and making him ask twice was not consent, it
    # was friction.
    #
    # It was also actively broken. Saying "turn the camera on" runs `set_camera`,
    # and the camera panel takes the stage — so the model he was about to grab
    # vanished behind a webcam feed. A screenshot of the armed state found that;
    # every other check passed, because the gestures worked perfectly on a
    # hologram nobody could see.
    #
    # Off the loop, like every other camera start: `camera.start` blocks.
    turned_on = False
    from camera import camera
    if not camera.is_on:
        res = await asyncio.to_thread(camera.start)
        if not res.get("ok"):
            return {"error": res.get("error") or "the camera would not open"}
        turned_on = True
    r = control.arm()
    if r.get("error"):
        if turned_on:                  # don't leave it running for nothing
            await asyncio.to_thread(camera.stop)
        return r
    # The HUD says so from the moment it is armed, not from the first grab. A
    # camera reading continuously has to be visible while it is doing it.
    await bus.emit("hands", action="armed")
    line = ("Camera on. Pinch to take hold of it, sir — open your hand to let go."
            if turned_on else
            "Pinch to take hold of it, sir — open your hand to let go.")
    return {**r, "camera_started": turned_on, "spoken": line}


async def hand_status() -> dict:
    """Is it watching, and is the loop actually turning?

    `armed` on its own is a flag, and a flag is exactly what survives a loop
    that has died — the badge stays lit, his hands stop working, and nothing is
    logged. `frames` is the honest witness: it only advances if the camera is
    genuinely being read, so a frozen counter is a stopped tracker.
    """
    from hand_control import control
    st = control.status()
    if not st.get("armed"):
        return {**st, "spoken": "I'm not watching your hands, sir."}
    seen = st.get("detects", 0)
    return {**st, "spoken": ("I'm watching, sir — I can see your hands."
                             if seen else "I'm watching, sir, but I can't see "
                                          "your hands at the moment.")}


async def hide_hologram() -> dict:
    _current.clear()
    # The hand tracker has nothing to move now. It checks this itself every
    # frame, but stopping it here means the camera work ends with the stage
    # rather than up to a frame later.
    try:
        from hand_control import control
        control.disarm("the stage closed")
    except Exception:
        log.debug("could not stop hand tracking", exc_info=True)
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
        name="holo_control",
        description="Move the hologram already on the stage: rotate, flip, scale, "
                    "section (cut it open), explode, reset, fit, layers (show the "
                    "sliced toolpath) or solid. This is the VIEW only — the model "
                    "on disk is not changed and its millimetres do not move, so "
                    "never say the part got bigger or smaller. To change the real "
                    "part, use edit_part.",
        parameters={"type": "object", "properties": {
            "action": {"type": "string", "enum": list(_ACTIONS)},
            "axis": {"type": "string", "enum": ["x", "y", "z"],
                     "description": "z is vertical, the axis it stands on the bed on"},
            "degrees": {"type": "number", "description": "for rotate; negative turns back"},
            "factor": {"type": "number", "description": "for scale; 1.5 is closer"},
            "at": {"type": "number", "description": "for section; 0-1 along the axis"},
            "phrase": {"type": "string",
                       "description": "what he said, if the action is not obvious"}},
            "required": []},
        risk=Risk.SAFE, handler=holo_control, timeout=20))
    registry.register(Tool(
        name="hand_control",
        description="Let his hands move the hologram: pinch to take hold, drag to "
                    "turn it, two pinched hands to zoom, open palm to let go. Reads "
                    "the camera continuously while armed, so it is off by default "
                    "and turns itself off when the model comes down. Every gesture "
                    "has a spoken equivalent — hands are never required.",
        parameters={"type": "object", "properties": {
            "on": {"type": "boolean", "description": "true to watch, false to stop"}},
            "required": []},
        # LOW, not SAFE: it reads the webcam continuously for as long as it is
        # armed. The tier describes what the handler DOES.
        risk=Risk.LOW, handler=hand_control, timeout=20))
    registry.register(Tool(
        name="hand_status",
        description="Whether his hands are being watched right now, and how many "
                    "frames have been read since it armed.",
        parameters={"type": "object", "properties": {}, "required": []},
        # SAFE: it reads a counter. It opens nothing and turns nothing on — the
        # tier describes what the handler DOES, and this one does arithmetic.
        risk=Risk.SAFE, handler=hand_status, timeout=10))
    registry.register(Tool(
        name="hide_hologram",
        description="Take the hologram down.",
        parameters={"type": "object", "properties": {}, "required": []},
        risk=Risk.SAFE, handler=hide_hologram, timeout=20))
