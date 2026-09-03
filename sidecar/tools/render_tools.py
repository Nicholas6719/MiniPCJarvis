"""Turning something into a hologram: the estimate, the question, the queue.

This is the tool surface for phase D. The interesting decision is where the
QUESTION lives.

His correction, verbatim: an estimate on its own is not enough — "he should ask
me if I'm okay with that time and if he should proceed, because maybe I don't
want to do it if it's going to take over an hour". So above a threshold JARVIS
says how long and asks, and the render does not start until he answers.

IT IS A COST QUESTION, NOT A RISK ONE, and it deliberately does not use the risk
gate. `generate_part` writes a file and is honestly LOW; promoting it to MEDIUM
to force a confirmation would corrupt what the tier means — the same mistake as
`face_confirm` sitting at SAFE while able to switch the webcam on. So the tool
returns `_ask` and the orchestrator arms a conversational yes/no, which he
answers in his own words.

BELOW the threshold there is no question at all. Asking permission to spend a
fifth of a second is friction, not courtesy — which is why tier 0 (a parametric
template) and tier 2 (a traced contour or a relief) sit under it, and tier 1 —
which wakes llama-server and measured 27 s — sits above it.

DECLINING IS A REAL ANSWER: the "leave it" branch runs nothing, so there is no
half-written file in the work folder to tidy up afterwards.
"""
from __future__ import annotations

import logging

import create3d
import render_estimates as est
from render_queue import queue
from tools.registry import Risk, Tool, registry

log = logging.getLogger("jarvis.tools.render")


def _label(description: str, image_path: str, tier: int) -> str:
    """What it gets called in "the dragon is ready, sir"."""
    d = (description or "").strip().strip(".")
    if d:
        d = d[:48]
        return d if d.lower().startswith(("a ", "an ", "the ")) else d
    if image_path:
        return "model from that picture"
    return f"tier {tier} model"


# The last thing he asked to be made, so "find another design" has something to
# re-roll. The DESIGN comes from the reference picture — a web image search —
# rather than from the model, so another design is simply the next usable picture
# down the list.
_last_make: dict = {}


async def another_design() -> dict:
    """Make the same thing again from a DIFFERENT reference picture."""
    if not _last_make.get("description"):
        return {"error": "I haven't made anything to redo, sir"}
    if _last_make.get("image_path"):
        return {"error": "that one was made from a picture you gave me, sir — "
                         "give me a different picture and I'll trace that"}
    nxt = int(_last_make.get("skip", 0)) + 1
    if nxt > 6:
        return {"error": "I've been through the pictures I can find of that, sir"}
    return await make_hologram(description=_last_make["description"],
                               tier=int(_last_make.get("tier", -1)),
                               name=_last_make.get("name", ""),
                               confirmed=True, skip=nxt)


async def make_hologram(description: str = "", image_path: str = "", tier: int = -1,
                        name: str = "", confirmed: bool = False,
                        skip: int = 0) -> dict:
    """Make a 3D model and put it up, in the background, with an estimate."""
    desc = (description or "").strip()
    if not desc and not image_path:
        return {"error": "what should I make, sir?"}

    # -1, not 0, for "he did not name a tier". 0 became a real tier when the
    # parametric templates landed, and the old sentinel silently made every
    # request tier 0 — "a dragon" came back as a template in a fifth of a second,
    # which is exactly as wrong as it sounds.
    t = int(tier) if int(tier) in create3d.TIERS else create3d.choose_tier(desc, image_path)

    # An unavailable tier is refused BEFORE he is asked to wait for it. Being
    # asked "about three minutes, shall I?" and then told the model is not
    # installed is the worst possible order for those two sentences.
    avail = create3d.available()
    if t in (3, 4) and not avail.get(t):
        return create3d._missing(t)

    seconds = est.estimate(t)
    label = _label(desc, image_path, t)

    if not confirmed and seconds > est.ask_threshold():
        return {"_ask": {
            "subject": label,
            "question": (f"That's {est.spoken(seconds)}{est.confidence_note(t)}, sir. "
                         f"Shall I?"),
            "tool": "make_hologram",
            # confirmed=True, so answering "go ahead" runs this same tool and
            # takes the other path rather than asking again.
            "args": {"description": desc, "image_path": image_path,
                     "tier": t, "name": name, "confirmed": True},
        }}

    _last_make.update({"description": desc, "image_path": image_path,
                       "tier": t, "name": name, "skip": skip, "label": label})

    async def job():
        r = await create3d.build(t, desc, image_path, name, skip=skip)
        if r.get("stl") and not r.get("error"):
            # Put it up the moment it exists. "Anything becomes a hologram" is
            # the phase; a finished mesh he has to ask to see is half of it.
            try:
                from tools.holo_tools import show_hologram
                await show_hologram(path=r["stl"])
            except Exception:
                log.debug("could not project the finished model", exc_info=True)
            # ...and into the project, if one is open. Filing by hand is filing
            # that does not happen: he opens a project, asks for six things, and
            # a week later the folder has notes and no models.
            try:
                from tools.workspace_tools import active, file_in_project
                if active():
                    got = await file_in_project(stl_path=r["stl"])
                    if got.get("count"):
                        r["filed_under"] = got.get("project")
                        r["filed_count"] = got["count"]
            except Exception:
                # A workspace that is full, read-only or missing must not turn a
                # finished model into a failed one.
                log.warning("could not file the model into the project",
                            exc_info=True)
        return r

    sub = queue.submit(t, label, job)
    if sub.get("error"):
        return sub
    behind = sub.get("queued_behind") or 0
    spoken = (f"Starting now, sir — {sub['estimate_spoken']}."
              if not behind else
              f"It's queued behind {behind} other{'s' if behind > 1 else ''}, sir.")
    return {"started": True, "tier": t, "label": label, **sub,
            "note": create3d.note_for(t, desc, image_path), "spoken": spoken}


async def render_status() -> dict:
    """What is being made, and how much longer. Answers instantly, always."""
    s = queue.status()
    if not s.get("busy"):
        return {**s, "spoken": "Nothing's rendering, sir."}
    if s.get("starting"):
        return {**s, "spoken": f"The {s['label']} is just about to start, sir — "
                               f"{s['remaining_spoken']}."}
    return {**s, "spoken": f"The {s['label']} has {s['remaining_spoken']} to go, sir."}


async def cancel_render() -> dict:
    """Stop it. A render that cannot be stopped is a machine holding him hostage."""
    r = queue.cancel()
    if not r.get("cancelled"):
        return {**r, "spoken": "Nothing was running, sir."}
    return {**r, "spoken": f"Stopped the {r['label']}, sir."}


def register_all() -> None:
    registry.register(Tool(
        name="another_design",
        description="Make the last thing again from a DIFFERENT reference "
                    "picture. Use for 'find another design', 'try a different "
                    "one', 'that's not quite right, find another'.",
        parameters={"type": "object", "properties": {}, "required": []},
        risk=Risk.LOW, handler=another_design, timeout=20))
    registry.register(Tool(
        name="make_hologram",
        description="Make a 3D model from a description or a picture and project it. "
                    "Runs in the BACKGROUND — it returns immediately and JARVIS keeps "
                    "answering. Picks the technique itself: OpenSCAD for a part with "
                    "dimensions, a traced extrusion for a logo, a mesh model for a photo "
                    "or an arbitrary object. If it will take a while, it reports the "
                    "estimate and asks first rather than starting.",
        parameters={"type": "object", "properties": {
            "description": {"type": "string", "description": "what to make, in his words"},
            "image_path": {"type": "string", "description": "a picture to build from"},
            "tier": {"type": "integer",
                     "description": "0 parametric template, 1 OpenSCAD written by the "
                                    "model, 2 traced extrusion or photo relief, "
                                    "3 photo to mesh, 4 text to mesh; OMIT to choose "
                                    "automatically, which is almost always right"},
            "name": {"type": "string"}},
            "required": []},
        risk=Risk.LOW, handler=make_hologram, timeout=60))
    registry.register(Tool(
        name="render_status",
        description="How the model being made is coming along, and how much longer.",
        parameters={"type": "object", "properties": {}, "required": []},
        risk=Risk.SAFE, handler=render_status, timeout=10))
    registry.register(Tool(
        name="cancel_render",
        description="Stop the model currently being made and drop anything queued.",
        parameters={"type": "object", "properties": {}, "required": []},
        risk=Risk.SAFE, handler=cancel_render, timeout=10))
