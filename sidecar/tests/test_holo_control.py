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
                       ("tip it forward", "rotate")):
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
        wd = F.work_dir()
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
    # ...and the rewrites they were carved out of still work on everything else
    check("'open spotify' still canonicalises to open APP", _norm("open spotify") == "open APP")
    check("'hide everything' still does", _norm("hide everything") == "hide everything")
    check("plain numbers are still erased", "N" in _norm("set the volume to 40"))

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
