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

    print("\n-- dense meshes are drawn, not speckled --")
    # THIS GATE ASSERTED THE OPPOSITE, and justified a 40,000 ceiling with "it
    # looks no different at this size". Measured, that was wrong: a stride
    # through a 399,118-triangle duck keeps one triangle in ten and puts HOLES
    # in the surface, and the result is a speckled shape he would reasonably
    # have judged a bad model. 40k draws in 0.7 s and 400k in 4.5 s, on a path
    # that already took minutes.
    check("the ceiling is high enough not to perforate a surface",
          meshshot.MAX_DRAW_TRIS >= 200_000,
          "a stride does not thin a surface evenly")
    # This gate used to assert <= 1,000,000 on the grounds that "a million
    # triangles through PIL is minutes". Measured: a million is 13.9 s. The
    # reasoning was wrong, so the bound was wrong with it.
    check("...but there is still a ceiling",
          meshshot.MAX_DRAW_TRIS <= 2_500_000,
          "1.99M draws in 22.7 s at ~11.4 us/triangle; and nothing above "
          "2,399,998 can load at all, since meshio.MAX_BYTES is 120 MB and a "
          "binary STL is 50 bytes a triangle")
    import meshio as _mio
    check("...set where a loadable model cannot reach it",
          meshshot.MAX_DRAW_TRIS >= (_mio.MAX_BYTES - 84) // 50,
          "a stride perforates at every count, so the guard should never fire "
          "on a file that loads")


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
          "sent_photo or await telegram.send_proactive" in dl,
          "a failed upload must not swallow the announcement")
    check("...and a message Telegram refuses is held, not filed as delivered",
          "telegram did not accept it" in dl and "proactive_held" in dl,
          "a send that never left must not be remembered as told")

    print("\n-- one scale across front, side and plan --")
    # Left to scale itself, each view filled its own panel: a 40 x 28 x 30
    # model came out at 10.9 px/mm in front and 14.5 in side, and the same
    # sphere was visibly a different size in two views side by side. A
    # front/side/plan sheet exists to be compared across, and it is the sheet
    # he judges a physical print from.
    import numpy as np
    from PIL import Image
    import meshio, meshshot

    # A 60 x 30 x 15 box: every view has a different aspect, so an unshared
    # scale shows up as one edge measuring two lengths.
    X, Y, Z = 60.0, 30.0, 15.0
    c = np.array([
        [[0,0,0],[X,0,0],[X,Y,0]], [[0,0,0],[X,Y,0],[0,Y,0]],
        [[0,0,Z],[X,0,Z],[X,Y,Z]], [[0,0,Z],[X,Y,Z],[0,Y,Z]],
        [[0,0,0],[X,0,0],[X,0,Z]], [[0,0,0],[X,0,Z],[0,0,Z]],
        [[0,Y,0],[X,Y,0],[X,Y,Z]], [[0,Y,0],[X,Y,Z],[0,Y,Z]],
        [[0,0,0],[0,Y,0],[0,Y,Z]], [[0,0,0],[0,Y,Z],[0,0,Z]],
        [[X,0,0],[X,Y,0],[X,Y,Z]], [[X,0,0],[X,Y,Z],[X,0,Z]],
    ], dtype=np.float32)
    d = tempfile.mkdtemp()
    stl = os.path.join(d, "box.stl")
    meshio.write_stl(c, stl)
    png = meshshot.shot(stl, os.path.join(d, "box.png"))

    im = np.array(Image.open(png).convert("RGB")).astype(int)
    W = im.shape[1] // 3
    bg = np.array(Image.new("RGB", (1, 1), meshshot.BG)).astype(int)[0, 0]
    drawn = []
    for k in range(3):
        p = im[26:, k * W:(k + 1) * W]
        ys, xs = np.where(np.abs(p - bg).sum(axis=2) > 24)
        drawn.append((xs.max() - xs.min() + 1, ys.max() - ys.min() + 1)
                     if len(xs) else (0, 0))

    # front is X by Z, side is Y by Z, plan is X by Y.
    want = ((X, Z), (Y, Z), (X, Y))
    scales = []
    for (w, h), (mw, mh) in zip(drawn, want):
        scales += [w / mw, h / mh]
    lo, hi = min(scales), max(scales)
    check("every view is drawn at the same millimetres-per-pixel",
          hi - lo < 0.15,
          f"{[round(s, 2) for s in scales]} px/mm across front/side/plan")
    check("...and the box really is twice as wide as it is deep",
          abs(drawn[2][0] / max(drawn[2][1], 1) - X / Y) < 0.1,
          f"plan drawn {drawn[2]}")
    check("...and nothing is clipped at a panel edge",
          all(w <= W - 20 for w, _ in drawn),
          f"widths {[w for w, _ in drawn]} in panels of {W}")


    print("\n-- a dense model comes back solid, not speckled --")
    # Raising the cap only MOVED the speckle. At 400,000 a 766,322-triangle
    # sphere — well inside what tier 5 downloads — strided by two and came
    # back pinholed in all three views. There is no count at which a stride
    # stops perforating, so the guard now sits where nothing real reaches it.
    import numpy as np
    from PIL import Image
    import meshio, meshshot

    n = 620
    u = np.linspace(0, np.pi, n)
    v = np.linspace(0, 2 * np.pi, n)
    U, V = np.meshgrid(u, v)
    Pt = np.stack([np.sin(U) * np.cos(V), np.sin(U) * np.sin(V),
                   np.cos(U)], axis=-1) * 20.0
    t = []
    for a in range(n - 1):
        for b in range(n - 1):
            t.append([Pt[a, b], Pt[a + 1, b], Pt[a, b + 1]])
            t.append([Pt[a + 1, b], Pt[a + 1, b + 1], Pt[a, b + 1]])
    dense = np.array(t, dtype=np.float32)
    check("the test model really is past the old 400k cap",
          len(dense) > 400_000, f"{len(dense)} triangles")
    check("...and inside the guard that replaced it",
          len(dense) <= meshshot.MAX_DRAW_TRIS,
          f"guard is {meshshot.MAX_DRAW_TRIS}")

    dd = tempfile.mkdtemp()
    ds = os.path.join(dd, "dense.stl")
    meshio.write_stl(dense, ds)
    dp = meshshot.shot(ds, os.path.join(dd, "dense.png"))

    im = np.array(Image.open(dp).convert("RGB")).astype(int)
    bg = np.array(Image.new("RGB", (1, 1), meshshot.BG)).astype(int)[0, 0]
    W = im.shape[1] // 3
    worst = 0.0
    for k in range(3):
        p = im[26:, k * W:(k + 1) * W]
        solid = np.abs(p - bg).sum(axis=2) > 24
        ys, xs = np.where(solid)
        if not len(xs):
            continue
        # Well inside the silhouette, where a sphere has no business showing
        # the background through it.
        cy, cx = (ys.min() + ys.max()) // 2, (xs.min() + xs.max()) // 2
        r = int(min(ys.max() - ys.min(), xs.max() - xs.min()) * 0.30)
        core = solid[cy - r:cy + r, cx - r:cx + r]
        holes = float((~core).mean()) if core.size else 1.0
        worst = max(worst, holes)
    # CALIBRATED, not guessed. Measured on this same sphere: unstrided gives
    # exactly 0.000% background inside the core in all three views, and
    # strided gives 0.391% in the side view. The first threshold tried here
    # was 0.5%, which sat just ABOVE the bug and passed with it — the same
    # can-never-fail shape as the other gates caught this session.
    check("no background shows through the middle of a solid sphere",
          worst < 0.0005,
          f"{worst * 100:.3f}% of the core was background; unstrided "
          f"measures 0.000% and a stride by two measures 0.391%")


    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
