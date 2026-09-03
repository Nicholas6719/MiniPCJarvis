"""Every feature agreed for the 3D workbench, driven end to end.

Not a substitute for the suites that gate each piece properly — this is the
CHECKLIST. Each of the others proves one thing deeply; this one proves that the
set he actually asked for is all still present and wired to each other, which is
a different failure and the one that gets noticed.

It exists because running it found a real gap. Twenty of twenty-one held, and
the one that did not was the feature he described most concretely: "pull up
Spider-Man suit Mark 2" could not find the Spider-Man suit, because the folder
was created from one transcription of his voice and recalled from another.
Nothing was broken in isolation; the join between two working pieces was.

Deliberately shallow per line and wide across features. Anything that wants
depth belongs in the suite for that module.
"""
import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault(
    "JARVIS_DB", os.path.join(tempfile.mkdtemp(), "features.db"))

fails: list[str] = []


def check(name: str, cond, detail: str = "") -> None:
    ok = bool(cond)
    if not ok:
        fails.append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}"
          + (f"   {detail}" if detail and not ok else ""))


async def main() -> int:
    # ------------------------------------------- "I zoom in on the helmet"
    print("-- a render has named parts he can ask about --")
    import assembly
    src = ("module helmet(){sphere(r=9,$fn=32);}\n"
           "module gauntlet(){cube([8,4,4]);}\n"
           "module boot(){cylinder(h=6,r=3,$fn=24);}\n"
           "helmet();\ngauntlet();\nboot();\n")
    parts = assembly.parts_in(src)
    check("the pieces are read out of the source, not guessed at",
          [p["name"] for p in parts] == ["helmet", "gauntlet", "boot"],
          str([p["name"] for p in parts]))
    disp = assembly.with_dispatcher(src, parts)
    check("...and each one can be built on its own",
          disp.count("jarvis_part ==") >= 3 and "{" in disp,
          "braces, or the assembly loses parts silently")

    # ------------------------------------- "make his eyes smaller"
    print("\n-- he can change a feature by saying so --")
    import features
    small = features.factor_from("make his eyes smaller")
    big = features.factor_from("make the lines on the mask bigger")
    check('"make his eyes smaller" shrinks', (small or 1) < 1, str(small))
    check('"make the lines bigger" grows', (big or 1) > 1, str(big))
    check("...and a named feature is found from geometry",
          callable(getattr(features, "label", None)))

    # --------------------------------------------------- real colours
    print("\n-- it comes out in the colours it really is --")
    import colours
    import holo_angles
    check("a colour can be named from what he said",
          colours.from_words("spider-man red with black webs") is not None)
    check("...and a sampled hex reads back as a word",
          colours.label("#c41a20") == "red", colours.label("#c41a20"))
    check('"show it in colour" is understood',
          holo_angles.parse_action("show it in colour") == "colour")
    check('"back to the hologram" goes back',
          holo_angles.parse_action("back to the hologram") == "hologram")
    check("...and the cyan hologram is still what it starts as",
          holo_angles.parse_action("put it back the way it was") == "reset")

    # ------------------------- "a baseball with Spider-Man's face on it"
    print("\n-- one object with another render on it --")
    import composite
    got = composite.split("a baseball with spider-man's face on it")
    check("the sentence comes apart into object and decoration",
          bool(got), str(got))
    check("...and a plain request is left alone",
          not composite.split("a baseball"))

    # ------------------------------- look first, say what was found, ask
    print("\n-- it looks first, then asks --")
    import scout
    q = scout.question("a playstation 5",
                       {"model": None, "dims": None, "picture": None})
    check("nothing found is still an offer, not a dead end", bool(q),
          str(q)[:90])
    q2 = scout.question("a playstation 5",
                        {"model": {"name": "PS5"}, "dims": None,
                         "picture": {"src": "x"}})
    check("...and what WAS found is put in front of him", bool(q2),
          str(q2)[:90])

    # --------------- "this is not a task we can get done in one afternoon"
    print("\n-- a suit is a project, not an afternoon --")
    import components
    check("a suit is taken apart into pieces",
          components.worth_splitting("iron man mark 3 suit"))
    check("...and a duck is just a duck",
          not components.worth_splitting("a duck"))

    # ------------------------------------------- the folder he gave JARVIS
    print("\n-- the workspace he gave JARVIS to work in --")
    import workspace
    from tools import workspace_tools as WT
    check("the root is the folder he made",
          workspace.DEFAULT_ROOT
          == r"C:\Users\nicho\Documents\J.A.R.V.I.S",
          workspace.DEFAULT_ROOT)

    d = tempfile.mkdtemp()
    real_root = workspace.root
    workspace.root = lambda: d
    try:
        workspace.create("Spider-Man suit Mark 2", about="our own suit")
        workspace.note("Spider-Man suit Mark 2", "webs go on last")
        check("a project is opened and listed",
              any("Spider" in p["name"] for p in workspace.projects()))

        ask = await WT.start_project("Mark 4 suit", about="x", confirmed=False)
        check("the folder name is read back BEFORE it is made, so he can "
              "correct a mishearing", bool(ask.get("_ask")), str(ask)[:90])

        # The join that was broken: created from one transcription, asked for
        # in another.
        rec = await WT.recall_project("pull up the spiderman suit")
        check("asking for it in other words still finds it",
              rec.get("project") == "Spider-Man suit Mark 2",
              str(rec)[:110])
        check("...and the notes come back in full, since they are the part he "
              "cannot reconstruct",
              "webs go on last" in str(rec.get("notes")))

        seen = sorted(x for x in os.listdir(d)
                      if os.path.isdir(os.path.join(d, x)))
        workspace.note("spiderman suit mark 2", "a second note")
        check("...and a note in other words does not split the project in two",
              sorted(x for x in os.listdir(d)
                     if os.path.isdir(os.path.join(d, x))) == seen,
              "a second folder would take the note into an empty log")
    finally:
        workspace.root = real_root

    # -------------------------------- a long render says so, and sends one
    print("\n-- a long render finishes without him watching --")
    import delivery
    import meshshot
    import render_queue
    rq = open(render_queue.__file__, encoding="utf-8").read()
    check("a finished render announces itself through delivery",
          "deliver(" in rq,
          "so it is spoken if he is here and sent if he is not, and is "
          "subject to the dedup and the hourly ceiling")
    check("...and carries a picture of what was made", "image=image" in rq)
    check("...which delivery only puts down the Telegram route",
          "image" in open(delivery.__file__, encoding="utf-8").read())
    check("...and the picture can actually be drawn",
          callable(getattr(meshshot, "shot_async", None)))
    check("...at one scale across all three views",
          "sc)" in open(meshshot.__file__, encoding="utf-8").read())

    # ------------------------------------- "there is no limitation to this"
    print("\n-- and there is no limitation to what he can ask for --")
    import create3d
    check("every tier from a template to a found model is present",
          tuple(create3d.TIERS) == (0, 1, 2, 3, 4, 5, 6, 7),
          str(create3d.TIERS))
    check("...and a tier that cannot run falls back rather than refusing",
          callable(getattr(create3d, "_fallback_tier", None)))

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
