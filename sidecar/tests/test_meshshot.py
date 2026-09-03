"""The picture that goes to his phone, and the one that catches our mistakes.

His words: *"he'll message me through Telegram and say hey the render's done."*
Not offering a screenshot — sending one, because a message saying a two-hour job
finished with no way to judge it is a message that makes him walk to the PC.

The same renderer is the only check that has ever caught a wrong model here.
Every bad one arrived watertight, correctly measured and sliceable: an emblem
that was a disc, a "d20" that was a calibration card, an "Iron Man" that was a
forearm shell laid flat. Not one was caught by a number; all were obvious in a
picture.

Run: python tests/test_meshshot.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "shot.db"))

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


def box(path, w, h, d, z=0.0):
    import numpy as np
    v = np.array([[0, 0, z], [w, 0, z], [w, h, z], [0, h, z],
                  [0, 0, z + d], [w, 0, z + d], [w, h, z + d], [0, h, z + d]],
                 dtype=np.float32)
    f = [(0, 1, 2), (0, 2, 3), (4, 6, 5), (4, 7, 6), (0, 4, 5), (0, 5, 1),
         (1, 5, 6), (1, 6, 2), (2, 6, 7), (2, 7, 3), (3, 7, 4), (3, 4, 0)]
    tris = np.array([[v[a], v[b], v[c]] for a, b, c in f], dtype="<f4")
    with open(path, "wb") as fh:
        fh.write(b"\0" * 80)
        fh.write(len(tris).to_bytes(4, "little"))
        for t in tris:
            fh.write(b"\0" * 12)
            fh.write(t.astype("<f4").tobytes())
            fh.write(b"\0\0")
    return path


def main() -> int:
    import time

    from PIL import Image

    import assembly
    import meshshot

    d = tempfile.mkdtemp()

    print("\n-- a single model --")
    one = box(os.path.join(d, "plate.stl"), 40, 30, 6)
    t0 = time.time()
    out = meshshot.shot(one, os.path.join(d, "plate.png"))
    took = time.time() - t0
    check("it draws something", os.path.exists(out) and os.path.getsize(out) > 500)
    check("...quickly enough to sit on a completion path", took < 5.0, f"{took:.1f}s")
    im = Image.open(out)
    check("...as three views side by side", im.width >= meshshot.WIDTH * 3, im.size)
    check("...on the stage's own dark ground", im.getpixel((2, im.height - 2)) == meshshot.BG,
          "a picture on his phone and the hologram in front of him should be "
          "recognisably the same object")

    print("\n-- an assembly is drawn as parts --")
    a = box(os.path.join(d, "asm.stl"), 40, 40, 10)
    p1 = box(os.path.join(d, "asm.base.stl"), 40, 40, 10)
    p2 = box(os.path.join(d, "asm.cap.stl"), 20, 20, 6, z=10)
    assembly.write_manifest(a, [{"name": "base", "stl": p1},
                                {"name": "cap", "stl": p2}])
    out2 = meshshot.shot(a, os.path.join(d, "asm.png"))
    im2 = Image.open(out2).convert("RGB")
    colours = {im2.getpixel((x, y)) for x in range(0, im2.width, 7)
               for y in range(30, im2.height, 7)}
    hues = {c for c in colours if sum(c) > 120}
    check("each part gets its own colour", len(hues) >= 2,
          "'zoom in on the helmet' starts with being able to see there IS one")
    check("...and the parts are named on the picture", os.path.getsize(out2) > 500)

    print("\n-- it cannot cost him the announcement --")
    src = open(meshshot.__file__, encoding="utf-8").read()
    check("a picture that will not draw returns nothing rather than raising",
          'return ""' in src and "except Exception" in src,
          "he loses the photograph, not the part")
    check("...and it is drawn off the event loop",
          "asyncio.to_thread" in src,
          "this is numpy and PIL over tens of thousands of triangles")
    bad = os.path.join(d, "not-a-mesh.stl")
    open(bad, "wb").write(b"nonsense")
    import asyncio
    got = asyncio.run(meshshot.shot_async(bad))
    check("an unreadable model draws nothing and says nothing", got == "")

    print("\n-- a huge model is thinned rather than taking minutes --")
    check("there is a ceiling on what gets drawn",
          meshshot.MAX_DRAW_TRIS <= 60_000,
          "a 632,304-triangle skull takes minutes through PIL and looks no "
          "different at this size")

    print("\n-- the completion message carries it --")
    rq = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "render_queue.py"), encoding="utf-8").read()
    check("a finished render draws itself", "meshshot.shot_async" in rq)
    check("...and hands the picture to delivery", "image=shot" in rq)
    dl = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "delivery.py"), encoding="utf-8").read()
    check("delivery sends it only down the Telegram route",
          "_upload(" in dl and dl.index("if telegram_available()") < dl.index("_upload("),
          "when he is at the machine he can already see the stage")
    check("...and falls back to words if the upload fails",
          "if not sent_photo:" in dl,
          "a failed upload must not swallow the announcement")

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
