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

import asyncio
import logging
import os
import re
import time

import create3d
import render_estimates as est
from render_queue import queue
from tools.registry import Risk, Tool, registry

log = logging.getLogger("jarvis.tools.render")

# a description that only points at something already on screen
_POINTER = re.compile(
    r"(?:it|that|this|these|those|them|him|her|one|the same|that one|this one|"
    r"the same one|the same thing|the one|the first one|the second one|the last one|"
    r"the picture|that picture|this picture|the image|that image)")


def resolve_pointer(description: str, reference: str = "", *, panel=None,
                    now: float | None = None) -> dict:
    """"Render it" / "make that one 3D": a pointer means the pictures on screen.

    On 2026-09-05 "Render it." became three web searches for a model called
    "it". The thing he means is the subject of the last picture search, and
    the first picture is the reference - the image-first, hologram-second
    path the stage was designed around. With no pictures fresh on screen it
    asks rather than guesses. Anything that is not a pointer passes through.
    `panel` and `now` exist for the gate; the live call reads web_tools.
    """
    want = (description or "").strip().lower()
    if not _POINTER.fullmatch(want):
        return {"description": description, "reference": reference}
    try:
        if panel is None:
            from tools import web_tools as panel
        fresh = (now or time.time()) - float(getattr(panel, "last_images_at", 0.0) or 0.0)
        subject = str((getattr(panel, "_last_subject", None) or {}).get("q") or "")
        if subject and fresh < 1800:
            if not reference:
                imgs = list(getattr(panel, "_last_images", []) or [])
                if imgs and isinstance(imgs[0], dict):
                    reference = str(imgs[0].get("url") or imgs[0].get("src")
                                    or imgs[0].get("image") or "")
            log.info("hologram of %r -> %r (from the picture panel)", want, subject)
            return {"description": subject, "reference": reference}
    except Exception:
        log.debug("could not resolve the pointer from the picture panel", exc_info=True)
    return {"error": "which one, sir? Show me a picture first, or tell me what to render"}


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
# The scout's reference picture from the last question, so at most one of them
# sits in his work folder waiting for a "yes" that may never come.
_scout_pic: str = ""


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
                        skip: int = 0, pieces: list | None = None,
                        reference: str = "",
                        scouted_model: dict | None = None,
                        detailed: bool = False) -> dict:
    """Make a 3D model and put it up, in the background, with an estimate.

    `reference` and `scouted_model` are not for the model to fill in: they are
    what the scout found, handed straight back from the question on "yes" so
    the thing he agreed to is the thing that gets made.
    """
    # "RENDER IT" / "MAKE THAT ONE 3D": a pointer means the pictures on screen.
    # On 2026-09-05 "Render it." became three web searches for a model called
    # "it". The thing he means is the subject of the last picture search, and
    # the picture itself is the reference - which is the image-first,
    # hologram-second path the stage was designed around.
    if not image_path:
        resolved = resolve_pointer(description, reference)
        if "error" in resolved:
            return resolved
        description, reference = resolved["description"], resolved["reference"]
    # A RENDER NEEDS THE WEB. Tier 5 searches for a published model and tier 4
    # downloads a reference picture, so with no connection this cannot work at
    # all — and failing slowly is the worst way to say so. His duck took three
    # minutes and told him it was almost done the whole way; spending that to
    # arrive at "I couldn't" would be the same lesson unlearned.
    #
    # Only when nothing is on disk to work from: an image he handed over, or a
    # shape simple enough to write out as code, still works perfectly offline.
    if not image_path:
        import netcheck
        if not await asyncio.to_thread(netcheck.online):     # sync connect, off the loop
            return {"error": "I can't reach the internet at the moment, sir",
                    "offline": True,
                    "spoken": ("I can't reach the internet at the moment, sir — "
                               "I'd need it to find or build that. Give me a "
                               "picture to work from and I can still do it.")}

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

    # A DETAILED reconstruction is the same tier with a different engine and a
    # different clock: Hunyuan3D-2mini, minutes rather than a minute, so it is
    # estimated (and recorded) under its own key and goes through the cost
    # question like anything else that long.
    detailed = bool(detailed) and t in (3, 4) and bool(avail.get("detailed"))
    est_key = 8 if detailed else t
    seconds = est.estimate(est_key)
    label = _label(desc, image_path, t)
    if detailed:
        label = f"a detailed {label}" if not label.startswith(("a ", "an ")) else f"{label}, in detail"

    # A THING MADE OF PIECES IS A DIFFERENT CONVERSATION. "About five minutes,
    # shall I?" is the right question for a render and the wrong one for a suit:
    # it answers something he did not ask and skips the two that matter — is
    # this a project, and which piece first.
    if t == 6 and not confirmed:
        import components
        names = pieces or await components.component_list(desc)
        if len(names) >= components.MIN_COMPONENTS:
            # SCALED BY THE PIECES, not a flat number for the tier. Every
            # component is its own search and its own reconstruction, so six of
            # them is six times the work and the estimate has to say so.
            secs = est.estimate(5) * len(names)
            listed = ", ".join(names[:3])
            if len(names) > 3:
                listed += f" and {len(names) - 3} others"
            # TWO DIFFERENT THINGS, and running them together made a sentence
            # that argued with itself: "about five minutes, and not something
            # we'll finish in one afternoon". The minutes are the first pass;
            # the afternoon is the suit.
            return {"_ask": {
                "subject": label,
                "question": (f"That's {len(names)} pieces, sir — {listed}. "
                             f"{est.spoken(secs).capitalize()} to get a first "
                             # NOT the label: it is his own phrasing and produces "the
                             # our own spider-man suit itself".
                             f"version of all of them up, though the whole "
                             f"thing won't be an afternoon's work. Shall I "
                             f"open a project and start with the {names[0]}?"),
                "tool": "make_hologram",
                # The list travels WITH the confirmation: asking again could
                # return a different one, and then what he agreed to is not
                # what gets made.
                "args": {"description": desc, "tier": 6, "name": name,
                         "pieces": names, "confirmed": True},
            },
                "pieces": names, "piece_count": len(names),
                "instruction": ("This is a project, not a render. Say how many "
                                "pieces and that it is not one afternoon's "
                                "work, offer to open a project, and ask which "
                                "piece to start with. If he names one, make "
                                "just that piece instead.")}

    # LOOK FIRST, FOR EVERY RENDER. Skipped only when there is nothing to look
    # up: he supplied the dimensions, he supplied the picture, or it is a
    # template that IS the answer.
    if (not confirmed and not image_path and t not in (0, 6)
            and not create3d._DIMENSIONED.search(desc)):
        import scout
        # A picture scouted for an earlier question he never said yes to is
        # scaffolding nobody will use now; one at a time on his disk, at most.
        global _scout_pic
        if _scout_pic and _scout_pic != image_path:
            try:
                os.remove(_scout_pic)
            except OSError:
                pass
            _scout_pic = ""
        found = await scout.look(desc)
        if found and not found.get("timed_out"):
            q = scout.question(desc, found)
            secs = est.estimate(5 if q["route"] == "fetch" else t)
            ask = q["question"]
            # The cost, once, at the end — never instead of what was found.
            if q["found"] in ("model", "dimensions"):
                ask = ask.replace("Shall I", f"{est.spoken(secs).capitalize()}. "
                                             f"Shall I", 1)
            # THE ANSWER RUNS WHAT HE AGREED TO. These args used to carry the
            # pre-scout tier and the scouted photo regardless of the route, so
            # "Somebody's already made one — shall I fetch it?" / "yes" ran
            # tier 1 and generated a case from scratch instead of fetching, and
            # "yes" to a flat emblem handed tier 2 the scouted PHOTOGRAPH to
            # trace — the emblem regression he reported, through the voice
            # path. A fetch is tier 5. The scouted picture is NOT `image_path`
            # (that would make it a photo HE supplied and route it to tier 3,
            # or hand tier 2 a photograph to trace); it travels as `reference`,
            # which only tier 4 — and tier 5 falling back to 4 — reads, and the
            # found model travels as `scouted_model`, which tier 5 tries first.
            # Looking again could find something else, and then what he agreed
            # to is not what gets made.
            fetch = q["route"] == "fetch"
            # THE SCOUT'S PICTURE IS SCAFFOLDING, NOT A PART. It was downloaded
            # into his work folder before he was asked and never removed:
            # fifteen strangers' JPEGs including "yes-finish-the-render-ref.jpg".
            # Tier 4 removes it after building from it; a "no" leaves it for
            # the next scout to remove (above). One on disk at a time.
            pic = (found.get("picture") or {}).get("path", "")
            _scout_pic = pic
            # THE SCOUT'S "NOTHING" IS AN ANSWER TOO. It looked for a published
            # model and found none; handing that back means the build does not
            # run the same two searches again and fall back to tier 4 five
            # seconds later (the duck, 2026-09-05). A scout that timed out is
            # not "nothing", so tier 5 still gets its try then.
            tier_after = 5 if fetch else t
            if (tier_after == 5 and not fetch and not found.get("timed_out")
                    and "model" in found):
                log.info("scout found no model for %r; building from a picture", desc[:40])
                tier_after = 4
            return {"_ask": {
                "subject": label,
                "question": ask,
                "tool": "make_hologram",
                # What he was shown is what gets used: looking again could find
                # something else.
                "args": {"description": desc, "tier": tier_after, "name": name,
                         "image_path": "", "confirmed": True,
                         "reference": pic, "detailed": detailed,
                         "scouted_model": (found.get("model") or {}) if fetch else {}},
            },
                "found": q["found"], "scouted": found,
                "instruction": ("Tell him what was actually found — the model, "
                                "the dimensions, or that there were none — and "
                                "then ask. Never lead with how long it takes.")}

    if not confirmed and seconds > est.ask_threshold():
        return {"_ask": {
            "subject": label,
            "question": (f"That's {est.spoken(seconds)}{est.confidence_note(est_key)}, sir. "
                         f"Shall I?"),
            "tool": "make_hologram",
            # confirmed=True, so answering "go ahead" runs this same tool and
            # takes the other path rather than asking again.
            "args": {"description": desc, "image_path": image_path,
                     "tier": t, "name": name, "confirmed": True,
                     "detailed": detailed},
        }}

    _last_make.update({"description": desc, "image_path": image_path,
                       "tier": t, "name": name, "skip": skip, "label": label,
                       "detailed": detailed, "est_key": est_key})

    async def job():
        if t == 6 and pieces:
            # What he agreed to, built exactly as listed.
            import components
            from tools.fabrication import safe_name
            r = await components.build_each(desc, list(pieces),
                                            safe_name(name or desc))
            if r.get("error") and not r.get("stl"):
                r = await create3d.build(t, desc, image_path, name, skip=skip,
                                         progressive=True, reference=reference,
                                         scouted_model=scouted_model, detailed=detailed)
        else:
            # PROGRESSIVE ONLY HERE. This is the one render he asked for as a
            # whole, so it is the one he should watch resolve. The per-part
            # builds inside a composite never set it, or five half-built objects
            # would take turns on the stage and none of them would be the thing
            # he asked for.
            r = await create3d.build(t, desc, image_path, name, skip=skip,
                                     progressive=True, reference=reference,
                                     scouted_model=scouted_model, detailed=detailed)
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
    # "Stopped the a lighthouse, sir" - the label carries his article
    label = re.sub(r"^(?:a|an|the)\s+", "", str(r.get("label") or "render"), flags=re.I)
    return {**r, "spoken": f"Stopped the {label}, sir."}


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
                                    "3 photo to mesh, 4 text to mesh, 5 find one "
                                    "somebody published, 6 build it piece by "
                                    "piece; OMIT to choose automatically, which "
                                    "is almost always right"},
            "name": {"type": "string"},
            "confirmed": {"type": "boolean",
                          "description": "he has agreed to the wait"},
            "detailed": {"type": "boolean",
                         "description": "he asked for a DETAILED / high-quality / proper "
                                        "one: the slower volumetric reconstruction "
                                        "(minutes) instead of the quick relief"},
            "pieces": {"type": "array", "items": {"type": "string"},
                       "description": "for tier 6 only: the exact components he "
                                      "agreed to, passed straight back from the "
                                      "question so the list cannot change"}},
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
