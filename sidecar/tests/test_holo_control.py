"""Phase C: JARVIS works on the model with him.

The angle parser, the control tool, body separation for the exploded view, and
the "slice it" ambiguity. Offline — no renderer, no model, no embeddings.

WHAT THIS GATE IS REALLY FOR. Every control here is a sentence that ends in
something MOVING on screen, and the failure that costs him time is not a crash:
it is the wrong thing moving. "Reset it" spinning the model, "turn it upside
down" leaving it upright but backwards, "explode it" announcing a separation of
a part with nothing to separate. Each of those was a real defect in the first
version of this code and each has a case below.

The routing — that "rotate it 90 degrees" reaches holo_move at all, and that
"show me Spider-Man" still reaches images — is gated in test_brain.py, because
that is where utterances LAND rather than where they parse.

Run: python tests/test_holo_control.py
"""
import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "holoc.db"))

import numpy as np  # noqa: E402

import holo_angles as A  # noqa: E402
import meshio  # noqa: E402

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


def box(sx=10.0, sy=10.0, sz=10.0, ox=0.0):
    a, b, c, d = (ox, 0, 0), (ox + sx, 0, 0), (ox + sx, sy, 0), (ox, sy, 0)
    e, f, g, h = (ox, 0, sz), (ox + sx, 0, sz), (ox + sx, sy, sz), (ox, sy, sz)
    return np.array([(a, c, b), (a, d, c), (e, f, g), (e, g, h), (a, b, f), (a, f, e),
                     (d, g, c), (d, h, g), (a, e, h), (a, h, d), (b, c, g), (b, g, f)],
                    dtype=np.float64)


async def main() -> int:
    # ---------------------------------------------------------------- angles
    # "Upside down" names an OUTCOME, not an axis, and the outcome is only
    # reachable about a horizontal one. Read as a vertical turn — which the verb
    # "turn" would give it — the model ends up upright and merely facing
    # backwards, which looks like the command was ignored.
    for said in ("turn it upside down", "flip it", "flip it over", "on its head"):
        check(f"{said!r} turns it about a horizontal axis", A.parse_axis(said) == "x",
              A.parse_axis(said))
        check(f"...{said!r} by 180 degrees", A.parse_degrees(said) == 180.0,
              A.parse_degrees(said))

    check("'spin it' is the vertical axis", A.parse_axis("spin it round") == "z")
    check("'tip it' lays it over", A.parse_axis("tip it forward") == "x")
    check("'roll it' is the third axis", A.parse_axis("roll it") == "y")
    check("a named axis beats the verb",
          A.parse_axis("tip it about the z axis") == "z", A.parse_axis("tip it about the z axis"))

    check("degrees are read", A.parse_degrees("rotate it 90 degrees") == 90.0)
    check("a quarter turn is 90", A.parse_degrees("spin it a quarter turn") == 90.0)
    check("half a turn is 180", A.parse_degrees("give it half a turn") == 180.0)
    check("all the way round is 360", A.parse_degrees("turn it all the way around") == 360.0)
    check("spelled-out numbers are read", A.parse_degrees("turn it ninety degrees") == 90.0)
    check("'one eighty' is not read as 'one'",
          A.parse_degrees("turn it one eighty") == 180.0,
          A.parse_degrees("turn it one eighty"))
    check("'the other way' reverses it",
          A.parse_degrees("turn it ninety degrees the other way") == -90.0)
    check("a bare 'turn it' is a quarter turn, not zero",
          A.parse_degrees("turn it") == 90.0,
          "zero would look exactly like being ignored")

    # ------------------------------------------------------------- the scale
    check("'make it bigger' is a zoom", A.parse_scale("make it bigger") == 1.5)
    check("'zoom out' is the other way", (A.parse_scale("zoom out") or 9) < 1)
    check("a percentage is read", A.parse_scale("200%") == 2.0,
          "'%' is not a word character, so a trailing \\b never matched")
    check("'2x' is read", A.parse_scale("make it 2x") == 2.0)
    check("a sentence with no scale in it gives none",
          A.parse_scale("rotate it 90 degrees") is None)

    # ----------------------------------------------------------- the section
    check("'cut it in half' cuts through the middle",
          (A.parse_section("cut it in half") or {}).get("at") == 0.5)
    check("...horizontally by default",
          (A.parse_section("cut it in half") or {}).get("axis") == "z")
    check("'across' turns the plane", (A.parse_section("cut it across") or {})["axis"] == "x")
    check("a fraction is read",
          (A.parse_section("cut it three quarters of the way up") or {})["at"] == 0.75)
    check("'show me the inside' is a section", A.parse_section("show me the inside") is not None)
    check("a sentence with no cut in it gives none",
          A.parse_section("rotate it 90 degrees") is None)

    # ------------------------------------------------------------ the action
    # The first version fell through to "rotate" for anything it did not
    # recognise, so "reset it" and "show me the layers" both SPUN THE MODEL. A
    # wrong action is worse than an admitted miss: he watches it do the wrong
    # thing and then has to undo it.
    for said, want in (("reset it", "reset"), ("put it back the way it was", "reset"),
                       ("straighten it up", "reset"),
                       ("show me the layers", "layers"), ("show me the toolpath", "layers"),
                       ("back to the model", "solid"), ("hide the layers", "solid"),
                       ("pull it apart", "explode"), ("explode it", "explode"),
                       ("fit it on the screen", "fit"),
                       ("cut it in half", "section"), ("turn it upside down", "flip"),
                       ("zoom in", "scale"), ("rotate it 90 degrees", "rotate"),
                       ("tip it forward", "rotate"),
                       # his own words, 2026-09-04
                       ("center it", "fit"), ("put it in the middle", "fit"),
                       ("stop spinning", "still"), ("stop it from spinning", "still"),
                       ("keep it turning", "spin"),
                       # named views (2026-09-06: "show me the top" re-showed the model)
                       ("show me the top", "view"), ("let me see the front", "view"),
                       ("show me it from the side", "view"), ("top view", "view"),
                       ("what does it look like from behind", "view"),
                       # ...but a layer or a cut is not a view
                       ("show me the top layer", "layer"), ("cut the top off", "section")):
        check(f"{said!r} is {want}", A.parse_action(said) == want, A.parse_action(said))
    for said in ("what is the capital of france", "remind me to call dad",
                 "what's the weather", "open spotify"):
        check(f"{said!r} is not a control at all", A.parse_action(said) is None,
              A.parse_action(said))

    # ------------------------------------------------------------- the bodies
    check("one box is one body", int(meshio.bodies(box()).max()) + 1 == 1)
    two = np.concatenate([box(), box(ox=50)])
    check("two boxes are two bodies", int(meshio.bodies(two).max()) + 1 == 2)
    # Coincident faces WELD into one body, and that is the right answer: two
    # blocks sharing a face print as one lump, and an exploded view of them is
    # one lump moved. Asserted concretely rather than "one or two" — a check
    # that accepts either answer cannot fail.
    touching = np.concatenate([box(), box(ox=10)])
    check("two boxes sharing a face weld into one body",
          int(meshio.bodies(touching).max()) + 1 == 1,
          int(meshio.bodies(touching).max()) + 1)
    apart = np.concatenate([box(), box(ox=10.5)])
    check("...but a gap between them keeps them separate",
          int(meshio.bodies(apart).max()) + 1 == 2,
          int(meshio.bodies(apart).max()) + 1)
    check("an empty mesh has no bodies", len(meshio.bodies(np.zeros((0, 3, 3)))) == 0)

    # --------------------------------------------------------------- the tool
    from tools import holo_tools as H

    H._current.clear()
    r = await H.holo_control(action="rotate")
    check("nothing on the stage is refused, not crashed", bool(r.get("error")), r)

    H._current.update({"name": "gate", "path": "x.stl", "body_count": 1})
    r = await H.holo_control(phrase="turn it upside down")
    check("a flip becomes a rotation about x",
          r["applied"] == {"action": "rotate", "axis": "x", "degrees": 180.0}, r)
    check("...and it says so out loud", "180" in r["spoken"], r)

    r = await H.holo_control(phrase="reset it")
    check("'reset it' resets rather than rotating",
          r["applied"]["action"] == "reset", r)

    r = await H.holo_control(phrase="what is the capital of france")
    check("an unrecognised sentence is admitted, not guessed at",
          bool(r.get("error")), r)
    check("...and the message is not mangled by an f-string",
          "'that'" not in (r.get("error") or ""), r.get("error"))

    r = await H.holo_control(phrase="pull it apart")
    check("exploding a single body is refused honestly", bool(r.get("error")), r)
    check("...saying there is nothing to separate",
          "single body" in (r.get("error") or ""), r.get("error"))
    H._current["body_count"] = 3
    r = await H.holo_control(phrase="pull it apart")
    check("...and a three-part model separates", r["applied"]["action"] == "explode", r)
    check("...counting the parts", "3" in r["spoken"], r["spoken"])

    r = await H.holo_control(phrase="show me the layers")
    check("layers are refused before the part is sliced", bool(r.get("error")), r)
    check("...and it says a slice is what's missing",
          "slice" in (r.get("error") or "").lower(), r.get("error"))

    # NOTHING here may change the model. This is a part he is about to spend an
    # hour printing, and "make it bigger" must never be reported as the part
    # getting bigger.
    r = await H.holo_control(phrase="make the model bigger")
    check("scaling is the view, and says so",
          "view only" in (r.get("note") or ""), r)
    check("...and the spoken line does not claim the part changed",
          "bigger" not in (r.get("spoken") or "").lower(), r.get("spoken"))

    # ------------------------------------------------- editing the real part
    # Only the refusals are gated offline: everything past them needs the model
    # to rewrite the source, and llama-server belongs to the running app. The
    # edit that succeeds is exercised live through /debug/tool instead.
    from tools import fabrication as F

    check("an empty change is refused", bool((await F.edit_part("")).get("error")))
    if F.openscad_path():
        # In a temp folder, not his: this wrote gate-orphan-mesh.stl into the
        # real work folder, where a failed run would have left a nameless
        # "newest part" for `_pick()` to project.
        import tempfile
        from pathlib import Path as _P
        _real_work_dir = F.work_dir
        wd = _P(tempfile.mkdtemp(prefix="jarvis-holoctl-gate-"))
        F.work_dir = lambda: wd
        try:
            # A mesh with no source cannot be edited, and must SAY so rather than
            # approximate. This is every tier-3 and tier-4 part: a mesh from a photo
            # has no parameters to change, and quietly doing something else to it
            # would be the worst possible answer.
            orphan = wd / "gate-orphan-mesh.stl"
            orphan.write_bytes(b"solid x\nendsolid x\n")
            r = await F.edit_part("make the hole bigger", name="gate-orphan-mesh")
            check("a part with no source is refused", bool(r.get("error")), r)
            check("...and told why, in words",
                  "source" in (r.get("error") or ""), r.get("error"))
            try:
                orphan.unlink()
            except OSError:
                pass
        finally:
            F.work_dir = _real_work_dir
        r = await F.revert_part(name="gate-nothing-here")
        check("reverting a part with no earlier version is refused",
              bool(r.get("error")), r)
    else:
        print("  note: OpenSCAD is not installed; the edit refusals were not exercised")

    # --------------------------------------------------- the slice ambiguity
    import clarify

    H._current.clear()
    check("'slice it' with nothing on the stage is not ambiguous",
          clarify.detect("slice it") is None,
          "there is nothing to cross-section, so it plainly means the slicer")

    H._current.update({"name": "gate", "path": "x.stl", "body_count": 1})
    amb = clarify.detect("slice it")
    check("'slice it' with a model up asks which he means", amb is not None)
    if amb:
        check("...offering exactly two readings", len(amb.branches) == 2,
              [b.label for b in amb.branches])
        check("...a cross section and the printer",
              {b.label for b in amb.branches} == {"a cross section", "for the printer"},
              [b.label for b in amb.branches])
        # THE POINT. Both readings ACT. Speculating would cut the model open on
        # screen while he is still being asked, and start a real PrusaSlicer run
        # for an answer he might never give.
        check("...and NEITHER runs before he answers",
              all(not b.speculative for b in amb.branches),
              [(b.label, b.speculative) for b in amb.branches])
        check("...the printer branch is pointed at the model on the stage",
              dict(next(b for b in amb.branches if b.label == "for the printer").args)
              .get("stl_path") == "x.stl")
    check("a longer sentence is not caught by it",
          clarify.detect("slice it for the printer please") is None,
          "he already said which half he meant")

    # --------------------------------------------- the words that must survive
    from brain.router import _norm
    for said, word in (("open the hologram", "hologram"),
                       ("hide the hologram", "hologram"),
                       ("close the hologram", "hologram"),
                       ("hide the layers", "layers"),
                       ("show me a 3d image of that", "3d"),
                       ("show me a 3-D image of that", "3d"),
                       ("show me pictures of a bracket as a hologram", "hologram")):
        check(f"{word!r} survives canonicalisation of {said!r}",
              word in _norm(said), _norm(said))
    # ---- scrubbing the toolpath -----------------------------------------
    # The layer slider is the one control every slicer has and we did not, and
    # the failure it fixes is visible: a hundred layers drawn at once is a solid
    # block. The parse must tell "the layers" (a switch) from "layer 50" (a
    # position), because they arrive in the same sentence shape.
    check("'show me the layers' is not a scrub", A.parse_layer("show me the layers") is None)
    check("a numbered layer is a position", A.parse_layer("show me layer 50") == {"layer": 50},
          str(A.parse_layer("show me layer 50")))
    check("ordinals are read", A.parse_layer("show me the 20th layer") == {"layer": 20})
    check("'next layer' steps up", A.parse_layer("next layer") == {"delta": 1})
    check("'back a layer' steps down", A.parse_layer("go back a layer") == {"delta": -1})
    check("'the top layer' is the whole print", A.parse_layer("the top layer") == {"layer": -1})
    check("'the first layer' is the bed", A.parse_layer("show me the first layer") == {"layer": 0})
    check("a sentence with no layer in it is not a scrub",
          A.parse_layer("rotate it ninety degrees") is None)
    # And the ACTION splits the same way, in the right order: "layer 50" must not
    # merely switch the preview on again.
    check("a numbered layer is the 'layer' action", A.parse_action("show me layer 50") == "layer")
    check("'the layers' is still the 'layers' action",
          A.parse_action("show me the layers") == "layers")
    check("'back to the model' still wins over 'layer'",
          A.parse_action("back to the model") == "solid")
    # The slots carry the number down, because _CANON erases plain digits before
    # embedding and only the RAW sentence still has the 50 in it.
    import brain.skills as SK
    check("the slots carry the layer number",
          SK.slots_holo_move("show me layer 50") ==
          {"action": "layer", "phrase": "show me layer 50", "layer": 50},
          str(SK.slots_holo_move("show me layer 50")))
    check("the slots carry the step",
          (SK.slots_holo_move("next layer") or {}).get("delta") == 1)
    # An unsliced part cannot be scrubbed, and says so rather than showing an
    # empty ruler.
    import tools.holo_tools as HT
    HT._current.clear(); HT._current.update({"name": "definitely-not-sliced-xyz"})
    r = await HT.holo_control(action="layer", layer=5)
    check("scrubbing an unsliced part is refused", bool(r.get("error")), str(r))
    HT._current.clear()

    # ...and the rewrites they were carved out of still work on everything else
    check("'open spotify' still canonicalises to open APP", _norm("open spotify") == "open APP")
    check("'hide everything' still does", _norm("hide everything") == "hide everything")
    check("plain numbers are still erased", "N" in _norm("set the volume to 40"))

    # ------------------------------------------- one part of it (2026-09-06)
    # "Zoom in on the helmet to see the helmet specs" - his sentence, and
    # until now no action could act on a named part. The parts and their
    # sizes arrive with the geometry the stage fetched; the tool remembers
    # them, and "focus on the helmet" / "hide the gauntlet" / "everything
    # back" are view controls like any other.
    sent = []

    async def _cap(kind, **kw):
        sent.append((kind, kw))
    _real_emit = H.bus.emit
    H.bus.emit = _cap
    H._current.clear()
    H._current.update({"name": "suit", "path": "suit.stl", "body_count": 1})
    H.remember_geometry({"path": "suit.stl", "assembly": True, "body_count": 3,
                         "has_colour": True,
                         "parts": [{"name": "helmet", "size_mm": [120.0, 90.5, 100.2],
                                    "colour": "#ff0000"},
                                   {"name": "left_gauntlet", "size_mm": [60, 40, 80]},
                                   {"name": "power_core_2", "size_mm": [30, 30, 10]}]})
    check("the stage's facts reach the tool", H.current().get("has_colour") is True
          and len(H.current().get("parts") or []) == 3, H.current().get("parts"))
    got = await H.holo_control(phrase="focus on the helmet")
    check("'focus on the helmet' is the helmet on its own",
          got.get("action") == "part" and got.get("part") == "helmet"
          and got.get("mode") == "focus", got)
    check("...and it says how big the helmet is",
          "120 by 90 by 100 millimetres" in got.get("spoken", ""), got.get("spoken"))
    check("...as a view-only event", sent[-1][1].get("action") == "part"
          and sent[-1][1].get("part") == "helmet" and sent[-1][1].get("mode") == "focus",
          sent[-1])
    got = await H.holo_control(phrase="hide the left gauntlet")
    check("'hide the left gauntlet' puts THAT PART out of view, not the hologram",
          got.get("action") == "part" and got.get("part") == "left_gauntlet"
          and got.get("mode") == "hide" and H.current().get("path"), got)
    got = await H.holo_control(phrase="lose the power core")
    check("a numbered part answers to its bare name",
          got.get("part") == "power_core_2" and got.get("mode") == "hide", got)
    got = await H.holo_control(phrase="put all the parts back")
    check("'put all the parts back' is everything", got.get("action") == "part"
          and got.get("part") == "" and sent[-1][1].get("mode") == "all", got)
    got = await H.holo_control(action="part", part="helmet")
    check("the model can name the part directly", got.get("part") == "helmet"
          and got.get("mode") == "focus", got)
    got = await H.holo_control(action="part", part="visor")
    check("an unknown part is refused by name, with the names it has",
          "visor" in got.get("error", "") and "helmet" in got.get("error", ""), got)
    got = await H.holo_control(phrase="in colour")
    check("'in colour' no longer contradicts the stage on a coloured model",
          got.get("ok") and not got.get("no_colour"), got)
    got = await H.holo_control(phrase="turn it ninety degrees")
    check("ordinary controls are untouched with parts remembered",
          (got.get("applied") or {}).get("action") == "rotate", got)
    check("'part' is a control like any other", "part" in H._ACTIONS)

    # "FOCUS ON THE HELMET" arrives at focus_window - a SYNC tool, run in
    # the executor thread - and must still reach the hologram. Release 50's
    # live suite found it saying "no window": create_task from a thread.
    import threading
    from tools import windows_tools as WTOOLS
    import events as _events
    await _events.bus.emit("noop")          # records the loop, as the app does
    outcome: dict = {}

    def _from_thread():
        outcome["r"] = WTOOLS.focus_window("the helmet")
    th = threading.Thread(target=_from_thread)
    th.start()
    th.join(5)
    await asyncio.sleep(0.2)                # let the handed-over coroutine run
    check("focus_window from a worker thread hands the part to the hologram",
          outcome.get("r", {}).get("focused_part") == "helmet", outcome.get("r"))
    check("...and the stage heard it", sent[-1][1].get("action") == "part"
          and sent[-1][1].get("part") == "helmet", sent[-1])

    # ---------------------------------- the variety pass of 18:38 (2026-09-06)
    from brain.skills import ask_allowed, slots_holo_edit, slots_project_start
    check("a bare 'yes' is never a near-miss question", not ask_allowed("yes", "holo_again")
          and not ask_allowed("Okay.", "sleep") and ask_allowed("turn it a bit", "holo_move"))
    check("'index as test bench' names the project",
          slots_project_start("open a new project file, index as test bench") == {"name": "test bench"},
          slots_project_start("open a new project file, index as test bench"))
    check("'index as Mark II' too",
          slots_project_start("I'd like to open a new project file, index as Mark II") == {"name": "Mark II"})
    check("'for the arc reactor' names it",
          slots_project_start("create a new project folder for the arc reactor") == {"name": "arc reactor"})
    check("no name in the sentence leaves the tool to ask",
          slots_project_start("we're starting a new project") == {}
          and slots_project_start("start a new project for this") == {})
    check("'remove the render' is not an edit", slots_holo_edit("remove the render") is None)
    check("...'get rid of the handle' still is",
          slots_holo_edit("get rid of the handle") == {"change": "get rid of the handle"})

    # ------------------------------------------- before and after (2026-09-06)
    # edit_part keeps `<name>.prev.stl`; the stage draws it in amber over the
    # new one. With no earlier version the answer is a sentence, not a ghost.
    import tempfile
    td = tempfile.mkdtemp()
    stl = os.path.join(td, "plate.stl")
    open(stl, "wb").write(b"\0" * 84)
    H._current.clear()
    H._current.update({"name": "plate", "path": stl, "body_count": 1})
    got = await H.holo_control(phrase="show me the before and after")
    check("no earlier version is said, not shown",
          "no earlier version" in got.get("error", ""), got)
    open(os.path.join(td, "plate.prev.stl"), "wb").write(b"\0" * 84)
    got = await H.holo_control(phrase="show me the before and after")
    check("with one kept, the ghost goes up", got.get("action") == "compare" and got.get("on") is True
          and sent[-1][1] == {"action": "compare", "on": True}, (got, sent[-1]))
    got = await H.holo_control(phrase="just the new one")
    check("...and comes down again", got.get("on") is False and sent[-1][1].get("on") is False, got)
    from holo_angles import parse_action as _pa
    check("'what did it look like before' is a comparison", _pa("what did it look like before") == "compare")
    check("'put the old version back' is NOT (that is revert's)", _pa("put the old version back") != "compare",
          _pa("put the old version back"))
    H._current.clear()
    H.bus.emit = _real_emit

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
