"""Taking the request apart, when the thing itself cannot be built.

His requirement: *"Render me Iron Man's Mark 3 suit... I zoom in on the helmet
to see the helmet specs. I zoom in on the gauntlet to see the gauntlet specs."*

A whole suit cannot be reconstructed from one photograph and OpenSCAD cannot
sculpt armour — but a helmet can be found and a gauntlet can be rebuilt from a
picture of a gauntlet. So the request comes apart instead of the mesh.

THE PART THIS FILE GUARDS HARDEST IS PLACEMENT. Measured in the literature: an
LLM emitting absolute coordinates places objects at or BELOW random — Holodeck
reports 0.364 against 0.369 for collision-free random placement, and a two-line
"put it against a wall" heuristic beats both at 0.645. So the model is asked for
NAMES ONLY and every position is arithmetic. If a coordinate ever comes back
from the model, that is the bug.

Offline: the model is not called, and the per-component build is stubbed, so
what is tested is the parsing, the layout and the honesty.

Run: python tests/test_components.py
"""
import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "comp.db"))

fails = []


# ITS OWN TIMINGS, NOT THE MACHINE'S. render_estimates calibrates from real
# runs in %APPDATA%/JARVIS/render_times.json, so a day of small test renders
# pulls the medians down and a sentence quoting "about a minute" starts saying
# "about 30 seconds". The estimate is right; the assertion was resting on state
# this suite does not own.
import pathlib as _pl
import tempfile as _tf

import render_estimates as _est
_est._PATH = _pl.Path(_tf.mkdtemp()) / "render_times.json"

def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


def box(path, w, h, d):
    """A closed box of a known size, as a binary STL."""
    import numpy as np
    v = np.array([[0, 0, 0], [w, 0, 0], [w, h, 0], [0, h, 0],
                  [0, 0, d], [w, 0, d], [w, h, d], [0, h, d]], dtype=np.float32)
    f = [(0, 1, 2), (0, 2, 3), (4, 6, 5), (4, 7, 6), (0, 4, 5), (0, 5, 1),
         (1, 5, 6), (1, 6, 2), (2, 6, 7), (2, 7, 3), (3, 7, 4), (3, 4, 0)]
    import meshio
    return meshio.write_stl(np.array([[v[a], v[b], v[c]] for a, b, c in f],
                                     dtype=np.float32), path)


async def main() -> int:
    import assembly
    import components
    import create3d
    import meshio

    print("\n-- what he would expect to arrive in pieces --")
    for said, want in (("iron man mark 3 suit", True),
                       ("a spider-man suit", True),
                       ("a full body iron man armour", True),
                       ("a coffee mug", False),
                       ("a 20 mm cube", False),
                       ("the spider-man emblem", False)):
        check(f"{said!r} -> {'pieces' if want else 'whole'}",
              components.worth_splitting(said) is want)
    check("...and a suit routes to the tier that builds pieces",
          create3d.choose_tier("iron man mark 3 suit", "") == 6)
    check("...while a mug does not", create3d.choose_tier("a coffee mug", "") != 6)

    print("\n-- the list, out of what the model really says --")
    # A reasoning model narrates, numbers what it was told not to number, and
    # writes a closing sentence. None of that is a component.
    reply = """Okay, let me think about what an Iron Man Mark 3 suit consists of.
The main separate pieces would be:

helmet
chest plate
1. left gauntlet
- right gauntlet
boots

Note: these are the main pieces a person would name when describing the armour.
"""
    got = components.parse_components(reply)
    check("the pieces come out", got == ["helmet", "chest plate", "left gauntlet",
                                         "right gauntlet", "boots"], got)
    check("...and the narration does not", "okay let me think" not in " ".join(got))
    check("...and a heading with a colon is not a piece",
          not any(":" in g for g in got))
    check("NONE means there are none", components.parse_components("NONE") == [])
    check("prose alone yields nothing",
          components.parse_components("It has no separate pieces.") == [])
    check("there is a ceiling on how many",
          len(components.parse_components("\n".join(f"piece {i}"
                                                    for i in range(40))))
          <= components.MAX_COMPONENTS)

    print("\n-- every position is arithmetic, never the model's --")
    src = open(components.__file__, encoding="utf-8").read()
    prompt = src[src.index("prompt = ("):src.index("try:")]
    for word in ("position", "coordinate", "x,", "translate", "millimet"):
        check(f"the model is never asked for {word!r}",
              word not in prompt.lower(),
              "an LLM emitting coordinates places objects at or below random")
    check("...it is asked for names only", "no sizes" in prompt.lower())

    print("\n-- the bench: pieces laid out, measured, not guessed --")
    d = tempfile.mkdtemp()
    sizes = {"helmet": (30, 20, 25), "gauntlet": (10, 8, 20), "boot": (18, 9, 12)}
    stubs = {n: box(os.path.join(d, f"{n}.stl"), *s) for n, s in sizes.items()}

    async def fake_build(tier, description="", image_path="", name="", skip=0):
        for n in sizes:
            if description.endswith(n):
                return {"stl": stubs[n], "tier": 4}
        return {"error": "no"}

    real_build, real_wd = create3d.build, None
    create3d.build = fake_build
    import tools.fabrication as fab
    real_wd = fab.work_dir
    from pathlib import Path
    fab.work_dir = lambda: Path(d)
    try:
        r = await components.build_each("iron man mark 3 suit",
                                        list(sizes), "suit")
    finally:
        create3d.build = real_build
        fab.work_dir = real_wd

    check("every piece was made", r.get("part_count") == 3, r.get("error"))
    check("...and named as he would name them",
          r.get("components") == ["helmet", "gauntlet", "boot"], r.get("components"))
    check("...and recorded as an assembly",
          len(assembly.read_manifest(r["stl"])) == 3)

    named = assembly.read_manifest(r["stl"])
    boxes = {n: meshio.describe(p) for n, p in named}
    check("each piece keeps its own size",
          [round(boxes[n]["size_mm"][0]) for n, _ in named] == [30, 10, 18],
          [boxes[n]["size_mm"] for n, _ in named])
    check("...they do not sit on top of each other",
          boxes["gauntlet"]["min_mm"][0] > boxes["helmet"]["max_mm"][0],
          "laid out on a bench, in the order they were listed")
    check("...and they all sit on the same ground",
          all(abs(boxes[n]["min_mm"][2]) < 0.01 for n, _ in named),
          "z=0 for every piece, so the row reads as one object")
    whole = meshio.describe(r["stl"])
    check("the whole model is the row",
          whole["size_mm"][0] > sum(sizes[n][0] for n in sizes),
          whole["size_mm"])

    print("\n-- a suit is a project, not a render --")
    # His words: "sir, this is not a task we can get done in one afternoon, but
    # we can get started now. Where did you want to start?" The ordinary
    # question — "that's about five minutes, shall I?" — answers something he
    # did not ask and skips the two that matter.
    from tools import render_tools as RT
    real_list = components.component_list

    async def listed(_d=None):
        return ["helmet", "chest plate", "left gauntlet", "right gauntlet",
                "boots", "belt"]

    components.component_list = listed
    try:
        big = await RT.make_hologram(description="our own spider-man suit",
                                     name="spidey")
        small = await RT.make_hologram(description="a coffee mug", name="mug2")
    finally:
        components.component_list = real_list

    q = (big.get("_ask") or {}).get("question", "")
    check("it says how many pieces and names some",
          "6 pieces" in q and "helmet" in q, q)
    check("...and that it is not one afternoon's work", "afternoon" in q, q)
    check("...and offers a project rather than just starting",
          "open a project" in q, q)
    check("...and asks which piece to begin with",
          "start with the helmet" in q, q)
    check("...without arguing with itself about the time",
          "minute" in q and "the whole thing" in q,
          "the minutes are the first pass; the afternoon is the suit, and "
          "running them together produced a sentence that contradicted itself")
    check("the agreed list travels with the confirmation",
          (big["_ask"]["args"].get("pieces") or [None])[0] == "helmet",
          "asking the model again could return a different list, and then what "
          "he agreed to is not what gets made")
    check("...and a simple request still gets the ordinary question",
          "pieces" not in (small.get("_ask") or {}).get("question", ""),
          "a mug is not a project")


    print("\n-- and it is honest when a piece fails --")
    async def half_build(tier, description="", image_path="", name="", skip=0):
        if description.endswith("helmet"):
            return {"stl": stubs["helmet"], "tier": 4}
        return {"error": "nothing found"}

    create3d.build = half_build
    fab.work_dir = lambda: Path(d)
    try:
        bad = await components.build_each("a suit", ["helmet", "gauntlet"], "half")
    finally:
        create3d.build = real_build
        fab.work_dir = real_wd
    check("one piece out of two is not an assembly",
          bool(bad.get("error")) and "gauntlet" in bad.get("failed", []),
          bad)

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
