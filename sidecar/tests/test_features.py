"""Naming the parts of a traced design, so one of them can be changed.

His words: *"if I say 'make his eyes smaller' — if we're still referring to the
Spider-Man baseball — it refactors and makes the eye smaller. If I say 'make the
lines on the mask bigger', it does that."*

The obvious answer was a face model, and it is the wrong one: a Spider-Man mask
is a stylised drawing, and the detectors that would name its features are the
detectors that will not fire on it. What works is the geometry the tracer
already produces — two similar shapes, mirrored about the middle, above the
centre line, ARE the eyes, whether or not anything recognises a face.

Offline, synthetic, and exact. Every case here is a shape whose size and
position I chose, so a failure is the labeller's and never the tracer's.

Run: python tests/test_features.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "feat.db"))

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


def rect(cx, cy, w, h):
    return [[cx - w / 2, cy - h / 2], [cx + w / 2, cy - h / 2],
            [cx + w / 2, cy + h / 2], [cx - w / 2, cy + h / 2]]


def main() -> int:
    import features as F

    # A mask: a big outline, two eyes high and mirrored, webbing, and one thing
    # low down that is none of those.
    MASK = [{"outline": rect(50, 50, 100, 100),
             "holes": [rect(30, 72, 22, 12), rect(70, 72, 22, 12),
                       rect(50, 40, 60, 3), rect(50, 30, 58, 3),
                       rect(50, 20, 20, 8)]}]

    print("\n-- what the shapes say they are --")
    pieces = F.label(MASK)
    names = [p["name"] for p in pieces]
    check("the biggest shape is the outline", names[0] == "outline")
    check("two shapes mirrored above the middle are the eyes",
          "left eye" in names and "right eye" in names,
          "similar area, same height, mirrored about the centre — that is what "
          "makes them a pair rather than two blobs")
    check("...named left and right as HE sees them",
          pieces[names.index("left eye")]["centre"][0]
          < pieces[names.index("right eye")]["centre"][0],
          "'his left' and 'the left' are opposite sides; the viewer's is the "
          "one he can check")
    check("long thin shapes are lines", names.count("lines") == 2,
          "the webbing on a mask, the veins on a leaf")
    check("anything else gets a positional name",
          any("lower" in n for n in names), names)

    print("\n-- and what is NOT a pair --")
    # Same height, similar size, but both on the same side of the middle.
    lopsided = [{"outline": rect(50, 50, 100, 100),
                 "holes": [rect(20, 72, 20, 12), rect(34, 72, 20, 12)]}]
    check("two shapes on the same side are not eyes",
          "left eye" not in [p["name"] for p in F.label(lopsided)],
          "mirrored about the middle is the whole test")
    # Mirrored and similar, but down at the bottom.
    feet = [{"outline": rect(50, 50, 100, 100),
             "holes": [rect(30, 12, 20, 12), rect(70, 12, 20, 12)]}]
    check("a pair low down is not eyes",
          "left eye" not in [p["name"] for p in F.label(feet)],
          "feet are a pair too")
    # Very different sizes.
    odd = [{"outline": rect(50, 50, 100, 100),
            "holes": [rect(30, 72, 30, 20), rect(70, 72, 8, 5)]}]
    check("two shapes of very different size are not a pair",
          "left eye" not in [p["name"] for p in F.label(odd)])
    check("an empty design names nothing", F.label([]) == [])

    print("\n-- from what he says to what he means --")
    for said, want in (("make his eyes smaller", {"left eye", "right eye"}),
                       ("make the lines on the mask bigger", {"lines"}),
                       ("shrink the left eye", {"left eye"}),
                       ("make the outline thicker", {"outline"}),
                       ("bigger webbing please", {"lines"})):
        got = {p["name"] for p in F.find(pieces, said)}
        check(f"{said!r}", got == want, got)
    check("something it does not know is not guessed at",
          F.find(pieces, "make the flurb bigger") == [],
          "a wrong guess changes the wrong part of his design")

    print("\n-- changing one, and only one --")
    eyes = F.find(pieces, "eyes")
    out = F.scaled(MASK, eyes, 0.6)
    before, after = F._pieces(MASK), F._pieces(out)
    check("the eyes are smaller",
          round(after[1]["w"] / before[1]["w"], 2) == 0.6
          and round(after[2]["w"] / before[2]["w"], 2) == 0.6)
    check("...each about its OWN centre, not sliding inward",
          abs(after[1]["centre"][0] - before[1]["centre"][0]) < 1e-6
          and abs(after[2]["centre"][0] - before[2]["centre"][0]) < 1e-6,
          "shrinking about the design's centre would move both eyes toward "
          "the nose")
    check("the outline is untouched", after[0]["w"] == before[0]["w"])
    check("the lines are untouched", after[3]["w"] == before[3]["w"])
    check("nothing changes when nothing matched",
          F.scaled(MASK, [], 0.5) == MASK)
    check("a nonsense factor changes nothing", F.scaled(MASK, eyes, 0) == MASK)

    print("\n-- and it can say what it found --")
    said = F.describe(pieces)
    check("it lists the parts", "left eye" in said and "lines" in said, said)
    check("...and says whose left it means", "looking at it" in said, said)
    check("nothing to describe says nothing", F.describe([]) == "")

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
