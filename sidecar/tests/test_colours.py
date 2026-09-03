"""What colour each part actually is, and one object with another render on it.

His question: *"if I want to fully 3D render things they typically aren't cyan —
Spider-Man's suit has white eyes and is usually red with black spiderweb lines.
Iron Man's suits are many colours. My AirPod case is white."*

And his correction, twice: the examples are examples. Nothing here knows what a
Spider-Man suit is. It samples the picture it was given, splits on the colours it
finds, and names the pieces from their geometry — a two-colour badge and a
four-colour logo go through exactly the same code.

Run: python tests/test_colours.py
"""
import asyncio
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "col.db"))

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


async def main() -> int:
    import colours

    print("\n-- a colour someone named --")
    check("a colour in a sentence is picked up",
          colours.from_words("make it gold and red") == colours.NAMED["gold"])
    check("...and nothing is invented when none is named",
          colours.from_words("a plain bracket") == "")
    check("a colour can be named back", colours.label("#c62828") == "red")
    check("...and nonsense is not given a name", colours.label("nope") == "")

    print("\n-- the colour out of the picture itself --")
    try:
        import cv2
        import numpy as np

        import create3d
        import features
    except ImportError:
        print("  SKIP  (opencv is not importable here)")
        print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
        return 1 if fails else 0

    # NOT a Spider-Man mask: a shape with a body colour and two lighter holes.
    # The code has never heard of Spider-Man and must not need to have.
    img = np.full((400, 400, 3), 255, np.uint8)
    cv2.circle(img, (200, 200), 150, (30, 30, 200), -1)          # BGR: red
    cv2.ellipse(img, (150, 150), (40, 22), 0, 0, 360, (250, 250, 250), -1)
    cv2.ellipse(img, (250, 150), (40, 22), 0, 0, 360, (250, 250, 250), -1)
    d = tempfile.mkdtemp()
    pic = os.path.join(d, "design.png")
    cv2.imwrite(pic, img)

    shapes = create3d.trace_shapes(pic)
    pieces = features.label(shapes)
    got = colours.sample(pic, shapes, pieces)
    check("the body's own colour is read from the image",
          colours.label(got.get("outline", "")) == "red", got)
    check("...and the holes' colour is read separately",
          colours.label(got.get("left eye", "")) == "white", got)
    check("a hole does not tint the shape around it",
          got.get("outline") != got.get("left eye"),
          "sampling the parent through its holes makes a body read as the "
          "colour of its eyes")

    print("\n-- and the design becomes parts, because a hole cannot be white --")
    from pathlib import Path

    import assembly
    import meshio
    import tools.fabrication as fab
    real_wd = fab.work_dir
    fab.work_dir = lambda: Path(d)
    try:
        r = await create3d.from_image(pic, "design")
    finally:
        fab.work_dir = real_wd

    man = json.load(open(assembly.manifest_path(r["stl"]), encoding="utf-8"))
    parts = {e["name"]: e for e in man["parts"]}
    check("the body and each coloured hole are separate parts",
          len(parts) >= 3, list(parts))
    check("...each carrying its colour",
          all(e.get("colour", "").startswith("#") for e in man["parts"]))
    check("...which is also what multi-colour printing needs",
          len({e["colour"] for e in man["parts"]}) >= 2,
          "one part per filament is how it is actually done")

    # THE BUG THIS SECTION EXISTS FOR. `_shapes_scad` ends with resize() to the
    # requested width, so emitting the body and then each hole separately scaled
    # EACH to 60 mm — and the eyes came out as wide as the whole design.
    sizes = {n: meshio.describe(e["stl"])["size_mm"] for n, e in parts.items()}
    body = sizes.get("body")
    holes = [v for n, v in sizes.items() if n != "body"]
    check("the parts share one scale", all(h[0] < body[0] * 0.6 for h in holes),
          f"body {body}, holes {holes} — a hole as wide as the body no longer "
          f"fills the hole it was cut from")
    check("...and sit inside the body's own footprint",
          all(meshio.describe(e["stl"])["min_mm"][0]
              >= meshio.describe(parts["body"]["stl"])["min_mm"][0] - 0.01
              for n, e in parts.items() if n != "body"))
    # A GATE THAT CANNOT FAIL TEACHES NOTHING. A plain black silhouette has
    # nothing to separate, and splitting it would put a "2 of 2" in front of him
    # for a badge.
    flat = np.full((300, 300, 3), 255, np.uint8)
    cv2.circle(flat, (150, 150), 110, (20, 20, 20), -1)
    plain = os.path.join(d, "plain.png")
    cv2.imwrite(plain, flat)
    fab.work_dir = lambda: Path(d)
    try:
        pr = await create3d.from_image(plain, "plain")
    finally:
        fab.work_dir = real_wd
    check("a one-colour design stays one part",
          not os.path.exists(assembly.manifest_path(pr["stl"])),
          "splitting a plain badge would announce '2 of 2' for nothing")

    print("\n-- one object with another render on it --")
    import composite
    for said, want in (
            ("a baseball with spider-man's face on it", ("a baseball", "spider-man's face")),
            ("a mug with the batman logo", ("a mug", "the batman logo")),
            ("a keychain with my initials on it", ("a keychain", "my initials")),
            ("a mug with a lid", None),           # an assembly, not a decoration
            ("a bracket with two holes", None),   # a feature, not a decoration
            ("a baseball", None)):
        check(f"{said!r}", composite.split(said) == want, composite.split(said))
    check("...and it is routed before anything else reads the words",
          create3d.choose_tier("a mug with the batman logo", "") == 7,
          "'logo' made the flat-emblem rule claim the whole sentence, and he "
          "got a logo and no mug")

    print("\n-- and it can be asked for, and asked to stop --")
    import holo_angles
    for said, want in (("show it in colour", "colour"),
                       ("show me the real colours", "colour"),
                       ("what does it really look like", "colour"),
                       ("back to the hologram", "hologram"),
                       ("no colour please", "hologram"),
                       # The cyan is the look he asked to keep, so these have to
                       # go on meaning what they meant.
                       ("put it back the way it was", "reset"),
                       ("explode it", "explode"),
                       ("show me the layers", "layers")):
        check(f"{said!r} -> {want}",
              holo_angles.parse_action(said) == want,
              holo_angles.parse_action(said))

    import tools.holo_tools as ht
    check("the stage is told which way to switch",
          "act == " + chr(34) + "colour" + chr(34) in
          open(ht.__file__, encoding="utf-8").read(),
          "one action, two directions")

    stage_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))),
        "src", "components", "HoloStage.tsx")
    stage = open(stage_path, encoding="utf-8").read()
    check("the stage paints with vertex colours, not extra meshes",
          "vertexColors" in stage and "setAttribute(" in stage,
          "one attribute over a buffer that already exists, so no extra draw "
          "call on a GPU llama-server is already using")
    check("...and does nothing on a model that has no colours",
          "if (hasColour) paintTrue" in stage,
          "flickering to a white blob is worse than staying cyan")
    check("...and cyan is what it starts as",
          "let trueColour = false;" in stage,
          "the hologram is the look he asked to keep")


    print("\n-- which side of a tight crop is the background --")
    # A logo reference is usually a TIGHT CROP. Deciding background by majority
    # over the whole frame made a subject filling 55% of the picture the
    # majority, so the mask was left the wrong way round: the traced "outline"
    # came back as the four corners of the image and sampled #ffffff, with the
    # real subject labelled a hole inside it. His example is exactly this
    # picture — a red mask with white eyes.
    try:
        from PIL import Image, ImageDraw
        import create3d, features, colours as C
    except Exception as e:
        # cv2 and PIL are both bundled, so this is not a skip — a tracer that
        # cannot import is a broken build, not an absent optional feature.
        check("the tracer and the palette import at all", False, str(e))
    else:
        d = tempfile.mkdtemp()

        def mask_png(name, fill_frame):
            p = os.path.join(d, name)
            im = Image.new("RGB", (520, 640), (255, 255, 255))
            dr = ImageDraw.Draw(im)
            m = 1.0 if fill_frame else 0.5
            dr.ellipse([260 - 220 * m, 320 - 290 * m,
                        260 + 220 * m, 320 + 290 * m], fill=(196, 26, 32))
            dr.polygon([(120, 230), (240, 200), (230, 300), (120, 300)],
                       fill=(245, 245, 245))
            dr.polygon([(400, 230), (280, 200), (290, 300), (400, 300)],
                       fill=(245, 245, 245))
            im.save(p)
            return p

        p = mask_png("tight.png", True)
        shapes = create3d.trace_shapes(p)
        pieces = features.label(shapes)
        got = C.sample(p, shapes, pieces)
        check("a mask filling the frame traces the FACE, not the frame",
              C.label(got.get("outline", "")) == "red",
              f"outline came back {got.get('outline')!r} "
              f"({C.label(got.get('outline', ''))})")
        eyes = [v for k, v in got.items() if "eye" in k]
        check("...and its eyes are white",
              len(eyes) >= 2 and all(C.label(v) == "white" for v in eyes),
              str({k: v for k, v in got.items() if "eye" in k}))
        body = next((q for q in pieces if q.get("name") == "outline"), None)
        check("...and the outline is a real contour, not four corners",
              bool(body) and len(body.get("points") or []) > 8,
              f"the image border is a rectangle and a face is not; "
              f"outline has {len((body or {}).get('points') or [])} points")

        # Both polarities of the input the tier is actually built for.
        for nm, bg, fg in (("black on white", (255, 255, 255), (0, 0, 0)),
                           ("white on black", (0, 0, 0), (255, 255, 255))):
            q = os.path.join(d, nm.replace(" ", "_") + ".png")
            im = Image.new("RGB", (400, 400), bg)
            dr = ImageDraw.Draw(im)
            dr.ellipse([90, 90, 310, 310], fill=fg)
            dr.ellipse([170, 150, 230, 210], fill=bg)
            im.save(q)
            sh = create3d.trace_shapes(q)
            check(f"{nm} still traces one figure with one hole",
                  bool(sh) and len(sh) == 1
                  and len(sh[0].get("holes") or []) == 1,
                  f"{len(sh or [])} shape(s)")


    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
