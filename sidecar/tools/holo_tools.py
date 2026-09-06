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
import os
import re
from pathlib import Path

from events import bus
import meshio
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


# `<part>.stage192.stl` — a rung of a progressive carve, not a part of his.
_ROUGH = re.compile(r"\.stage\d+\.[A-Za-z0-9]+$")


def _resolve(path: str) -> Path | None:
    """Only files we made, or a path he named outright. Never a traversal."""
    p = Path(str(path or "")).expanduser()
    try:
        return p.resolve(strict=True)
    except OSError:
        return None


def _pick(path: str = "", name: str = "") -> Path | None:
    """Which model he means: one he named, one from the work folder, or the newest.

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
        #
        # ...and EVERY readable extension, not just .stl. Tier 5 fetches
        # whatever the repo published, so looking only for `<name>.stl` would
        # miss a model we had just downloaded and saved ourselves.
        stem = safe_name(Path(name).stem)
        for ext in meshio.READABLE:
            cand = _resolve(str(work_dir() / f"{stem}{ext}"))
            if cand is not None:
                return cand
        return None
    # Nothing named: the newest thing he made, which is almost always what
    # "show me the bracket" means right after making one.
    try:
        # NOT THE ROUGH RUNGS. A progressive render writes `<part>.stage96.stl`
        # and friends on the way to the real file, and each one is NEWER than
        # the part it previews — so "show me that again" a minute later would
        # correctly, by this rule, project a 96-grid blob. They are cleaned up
        # when the render ends; this is the second guard, because a preview
        # being mistaken for his part is not a failure worth risking once.
        found = [f for ext in meshio.READABLE
                 for f in work_dir().glob(f"*{ext}")
                 if not _ROUGH.search(f.name)]
        # NOR A SUB-PART OF A WHOLE THAT IS SITTING RIGHT NEXT TO IT. Tier 1
        # renders `<base>.<part>.stl` AFTER `<base>.stl`, and tier 2 writes its
        # colour parts after the body, so the newest file was reliably one
        # eye or one rim — and "will it print" gave a bed-fit verdict about a
        # fragment, "show me the hologram" projected a lone rim as the part.
        # A file whose stem has a dot, where the stem before the dot exists as
        # its own model beside it, is a piece of that model, not a model.
        names = {f.name for f in found}
        found = [f for f in found
                 if not ("." in f.stem and
                         any(f"{f.stem.split('.', 1)[0]}{ext}" in names
                             for ext in meshio.READABLE))]
        found.sort(key=lambda f: f.stat().st_mtime)
        return _resolve(str(found[-1])) if found else None
    except OSError:
        return None


async def show_stage(path: str, name: str, res: int) -> None:
    """One rung of a progressive carve, on the stage the moment it lands.

    His idea, and the measurement agreed with it: at grid 384 a reconstruction
    is fifteen seconds of thinking and fifty-four of carving, so most of the
    wait is a phase where real geometry exists. The reference picture covers the
    thinking; this covers the rest, and what he watches resolve IS the model
    rather than an animation standing in for one.

    It deliberately mirrors `show_hologram` rather than inventing a second path:
    same `_current`, same event, so the HUD reloads geometry the way it already
    knows how. `rough` is what distinguishes them, and it is there so the panel
    can say "resolving" instead of quietly implying this is the finished part.

    Never raises. A preview that fails must cost him nothing.
    """
    try:
        info = await asyncio.to_thread(meshio.describe, str(path))
    except Exception:
        log.debug("a rough stage could not be read", exc_info=True)
        return
    info.pop("_tris", None)
    info.pop("_edges", None)
    _current.clear()
    _current.update(info)
    _current["name"] = name
    _current["rough"] = int(res)
    await bus.emit("hologram", action="show", name=name,
                   triangles=info["triangles"], size_mm=info["size_mm"],
                   rough=int(res))


async def show_hologram(path: str = "", name: str = "") -> dict:
    """Project a model. `path` is an STL; `name` picks one from the work folder."""
    target = _pick(path, name)
    if target is None or target.suffix.lower() not in meshio.READABLE:
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

    _current.clear()          # `rough` goes with it: this one is the real part
    _current.update(info)
    _current["name"] = target.stem

    # The stage opens because the model is ready, the same way the camera panel
    # appears when the device opens rather than waiting to be asked separately.
    await bus.emit("hologram", action="show", name=target.stem,
                   triangles=info["triangles"], size_mm=info["size_mm"])
    w, h, d = info["size_mm"]
    out = {"name": target.stem, "triangles": info["triangles"],
           "size_mm": info["size_mm"],
           "spoken_size": f"{round(w)} by {round(h)} by {round(d)} millimetres",
           "on_stage": True}
    # WHAT IT IS MADE OF, if it is made of anything. This is the answer to "zoom
    # in on the helmet to see the helmet specs" — the names have to come back
    # with the model or there is nothing for him to name.
    import assembly
    named = assembly.read_manifest(str(target))
    if len(named) >= 2:
        out["parts"] = [n for n, _ in named]
        out["part_count"] = len(named)
        out["spoken_parts"] = _spoken_list([n.replace("_", " ") for n, _ in named])
    return out


def remember_geometry(payload: dict) -> None:
    """What `/holo/geometry` worked out, kept on the tool side.

    `_current` came from `meshio.describe`, which never sets `has_colour` or
    per-part sizes; the assembly payload does both. Until this, the stage
    painted the model in its real colours while the reply said "I don't have
    colours for that one" in the same turn (2026-09-06), and "how big is the
    gauntlet" had nothing to answer from.
    """
    if not isinstance(payload, dict) or payload.get("error"):
        return
    if not _current.get("path") or payload.get("path") and \
            os.path.normcase(str(payload.get("path"))) != os.path.normcase(str(_current.get("path"))) \
            and not payload.get("assembly"):
        return
    if "has_colour" in payload:
        _current["has_colour"] = bool(payload.get("has_colour"))
    parts = payload.get("parts")
    if isinstance(parts, list) and parts and isinstance(parts[0], dict):
        _current["parts"] = [{"name": str(p.get("name", "")),
                              "size_mm": list(p.get("size_mm") or []),
                              "colour": p.get("colour")} for p in parts]
    if payload.get("body_count"):
        _current["body_count"] = int(payload["body_count"])


def _find_part(phrase: str) -> tuple[str, dict | None]:
    """(the part name he said, its meta) or ("", None).

    Matches on the manifest's own names, spoken with underscores as spaces,
    longest first, so "the left gauntlet" beats "gauntlet"."""
    parts = _current.get("parts") or []
    t = " " + re.sub(r"[_\-]+", " ", (phrase or "").lower()) + " "
    best = ("", None)
    for p in sorted(parts, key=lambda p: -len(p.get("name", ""))):
        name = re.sub(r"[_\-]+", " ", p.get("name", "").lower()).strip()
        if not name:
            continue
        # a numbered part ("power core 2") is matched with or without its number
        bare = re.sub(r"\s+\d+$", "", name)
        if f" {name} " in t or (bare != name and f" {bare} " in t and not best[0]):
            best = (p["name"], p)
            if f" {name} " in t:
                return best
    return best


_FOCUS = re.compile(
    r"\b(?:focus on|zoom in on|zoom into|highlight|isolate|just the|only the|"
    r"on its own|by itself|single out|pick out|look at the|let'?s see the|"
    r"show me the|closer on)\b", re.I)
_PART_HIDE = re.compile(r"\b(?:hide|lose|remove|get rid of|drop|without|take away|"
                        r"take off|dismiss)\b", re.I)
_UNFOCUS = re.compile(
    r"\b(?:everything back|all (?:the )?parts|the whole (?:thing|model|assembly)|"
    r"whole (?:thing|model)|all of it|put it (?:all )?back together|"
    r"back together|show everything|unhide|bring (?:it|them|everything) back|"
    r"all the pieces)\b", re.I)


def _spoken_list(names: list) -> str:
    """"a, b and c" — said, not printed."""
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " and " + names[-1]


async def inspect_part(path: str = "", name: str = "") -> dict:
    """Will it print? Overhangs, bed fit, mesh integrity, and a wall estimate.

    Everything expensive runs OFF the event loop. A mesh of any size is a couple
    of hundred milliseconds of numpy, and a couple of hundred milliseconds is
    long enough to be heard as a stutter in the middle of him speaking — the
    lesson from the forty-minute freeze, applied before it can cost anything.
    """
    import asyncio

    target = _pick(path, name)
    if target is None or target.suffix.lower() not in meshio.READABLE:
        return {"error": "I don't have a model to check, sir"}

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


_ACTIONS = ("rotate", "flip", "scale", "section", "explode", "colour", "hologram",
            "reset", "fit",
            # PUTTING IT AWAY IS A CONTROL LIKE ANY OTHER. It lived only
            # as its own tool, so "remove render" - his own words - was
            # parsed correctly and then refused here.
            "hide",
            # Holding still, and drifting again. Without these "stop spinning"
            # reached the skill router and cancelled a render.
            "still", "spin",
            # a named view: top, bottom, front, back, left, right, side
            "view",
            "layers", "solid", "layer",
            # one named part on its own, or put out of view; "" is all of it
            "part")


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
                       phrase: str = "", view: str = "", part: str = "") -> dict:
    """Move the model that is already on the stage.

    NOTHING HERE CHANGES THE MODEL. Rotation, scale and the section cut are all
    view state: the STL on disk is untouched, and the millimetres `inspect_part`
    reports do not move because he turned it. Changing the real part is
    `edit_part`, which rewrites the source and re-renders and says so. Keeping
    that line sharp matters more here than anywhere else in the app — this is a
    part he is about to spend an hour printing.
    """
    if not _current:
        # "Show me the top" two seconds after "go for it": the model is still
        # building. Say that, rather than "nothing on the stage".
        try:
            from render_queue import queue as _q
            st = _q.status() or {}
            if st.get("busy"):
                what = str(st.get("label") or st.get("current") or "the model")
                return {"error": f"{what} is still building, sir — a few seconds more"}
        except Exception:
            pass
        return {"error": "there's nothing on the stage to move, sir"}

    act = (action or "").strip().lower()
    said = phrase or ""

    # ONE PART OF IT. "Zoom in on the helmet to see the helmet specs" is his
    # own sentence for what this stage is for, and until 2026-09-06 there was
    # no action that could act on a named part - `explode` was the nearest.
    # Checked BEFORE the sentence parser: "hide the gauntlet" is a part put
    # out of view, not the hologram closed. Parts come from the manifest,
    # remembered when the stage fetched its geometry.
    if _current.get("parts") and (act == "part" or (act not in _ACTIONS and said)):
        found, meta = _find_part(part or said)
        wants_all = bool(_UNFOCUS.search(said or "")) and not found
        if act == "part" or found or wants_all:
            if wants_all or (act == "part" and not found and not (part or "").strip()):
                await bus.emit("holo_control", action="part", part="", mode="all")
                return {"ok": True, "action": "part", "part": "",
                        "spoken": "All of it, sir.", "note": "view only"}
            if not found:
                names = [p["name"].replace("_", " ") for p in _current["parts"]]
                return {"error": f"I don't have a part called {part or 'that'}, sir — "
                                 f"it has {_spoken_list(names)}"}
            mode = ("hide" if _PART_HIDE.search(said or "") and not _FOCUS.search(said or "")
                    else "focus")
            await bus.emit("holo_control", action="part", part=found, mode=mode)
            size = list((meta or {}).get("size_mm") or [])
            said_size = (f"{round(size[0])} by {round(size[1])} by {round(size[2])} millimetres"
                         if len(size) == 3 else "")
            nice = found.replace("_", " ")
            return {"ok": True, "action": "part", "part": found, "mode": mode,
                    "size_mm": size, "spoken_size": said_size,
                    "spoken": (f"Without the {nice}, sir." if mode == "hide" else
                               f"The {nice}, sir{' — ' + said_size if said_size else ''}."),
                    "note": "view only — the model on disk is unchanged"}

    if act == "part" and not _current.get("parts"):
        return {"error": "it's a single piece, sir — there are no parts to pick out"}

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
        if act == "part":
            # the sentence had the SHAPE of a part request; the block above
            # matches the name, and it only runs when parts are remembered
            if not _current.get("parts"):
                return {"error": "it's a single piece, sir — there are no parts to pick out"}
            return await holo_control(action="part", part=part, phrase=said)

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
    elif act == "view":
        import holo_angles
        view = str(view or holo_angles.parse_view(said) or "front")
        await bus.emit("holo_control", action="view", view=view)
        names = {"top": "From the top", "bottom": "From underneath", "front": "Face on",
                 "back": "From behind", "left": "The left side", "right": "The right side",
                 "side": "Side on"}
        return {"ok": True, "action": "view", "view": view,
                "spoken": f"{names.get(view, 'There')}, sir."}
    elif act in ("still", "spin"):
        on = (act == "spin")
        await bus.emit("holo_control", action="spin", on=on)
        return {"ok": True, "action": act,
                "spoken": "Holding it steady, sir." if not on
                          else "Turning it slowly, sir."}
    elif act == "hide":
        # Straight to the tool that already does this, so the stage closing and
        # the hand tracker standing down stay in one place.
        r = await hide_hologram()
        return {**r, "action": "hide", "spoken": "Taken it down, sir."}
    elif act in ("colour", "hologram"):
        # One action, two directions. The stage ignores it on a model that has
        # no colours: cyan IS the answer there, and flickering to a white blob
        # would be worse than doing nothing.
        await bus.emit("holo_control", action="colour", on=(act == "colour"))
        got = current()
        if act == "colour" and not got.get("has_colour"):
            return {"ok": True, "no_colour": True,
                    "spoken": "I don't have colours for that one, sir — "
                              "it came without any."}
        return {"ok": True, "action": act,
                "spoken": ("In colour, sir." if act == "colour"
                           else "Back to the hologram, sir.")}
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
    # `seeing` is a hand in frame within the last two seconds - not "the
    # detector ran", which is what this used to read and would have claimed
    # hands in an empty room (soak receipt: frames 383, detects 383).
    return {**st, "spoken": ("I'm watching, sir — I can see your hands."
                             if st.get("seeing") else
                             "I'm watching, sir, but I can't see your hands "
                             "at the moment.")}


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
            "view": {"type": "string", "enum": ["top", "bottom", "front", "back", "left", "right", "side"],
                     "description": "for view: which side of it he wants to see"},
            "factor": {"type": "number", "description": "for scale; 1.5 is closer"},
            "at": {"type": "number", "description": "for section; 0-1 along the axis"},
            "part": {"type": "string",
                     "description": "for part: the named part to show on its own "
                                    "('helmet'); empty puts everything back"},
            "layer": {"type": "integer", "description": "for layer: which layer to show "
                                                        "(-1 is the top, 0 the first)"},
            "delta": {"type": "integer", "description": "for layer: +1 / -1 from the "
                                                        "layer shown"},
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
