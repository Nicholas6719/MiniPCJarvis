"""A picture of a model, for when he is not at the screen.

His words: *"if it takes him hours to render whatever I ask him to render, then
he should say okay I will notify you when it's done... he'll message me through
Telegram and say hey the render's done, do you want to see a screenshot of it."*

Not "do you want to see it" — just send it. A message saying a two-hour job
finished, with no way to tell whether the thing is any good, is a message that
makes him walk to the PC to find out.

THIS IS ALSO THE CHECK THAT HAS CAUGHT EVERYTHING. Every bad model this project
has produced arrived watertight, correctly measured and sliceable, and was the
wrong object: an emblem that was a plain disc, a "d20" that was a calibration
card, an "Iron Man" that was a forearm shell laid flat. Not one was caught by a
number. All of them were obvious in a picture. The same renderer that shows him
the result is therefore also what lets JARVIS check its own work before
announcing it.

DRAWN, NOT RAY TRACED. Painter's algorithm — sort the triangles back to front
and fill them — shaded by the angle of each face to a fixed light. It is a
hundred lines of numpy and PIL, both of which are already bundled, and it
produces something instantly readable as an object. A real renderer would be
better looking and would cost a dependency, a GPU context, and a class of
failure this cannot have.

PARTS ARE COLOURED SEPARATELY, because "zoom in on the helmet" starts with being
able to see that there IS a helmet.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger("jarvis.meshshot")

# THE STRIDE WAS THE PROBLEM, not the count. Dropping nine triangles in ten does
# not thin a surface evenly — it puts HOLES in it, and a 399,118-triangle duck
# came out as a speckled shape he would reasonably have judged a bad model.
# Measured on this machine: 40k draws in 0.7 s, 400k in 4.5 s. On a completion
# path that already took minutes, four seconds buys a picture that looks like
# the thing.
MAX_DRAW_TRIS = 400_000

WIDTH = 460
BG = (10, 14, 20)

# The stage's own palette, so a picture on his phone and the hologram in front
# of him are recognisably the same object.
CYAN = (40, 210, 255)
# Distinct hues for an assembly. Deliberately close together: this is one
# object seen in parts, not eight unrelated things.
PART_HUES = ((40, 210, 255), (90, 235, 200), (150, 200, 255), (60, 240, 150),
             (200, 190, 255), (255, 200, 120), (120, 255, 220), (255, 160, 190))

# front, side, plan — as (across, up, depth) axis indices, for a Z-up model.
VIEWS = (("front", 0, 2, 1), ("side", 1, 2, 0), ("plan", 0, 1, 2))


def _shade(tris, base):
    """Brightness per triangle, from its angle to a fixed light."""
    import numpy as np
    e1 = tris[:, 1] - tris[:, 0]
    e2 = tris[:, 2] - tris[:, 0]
    n = np.cross(e1, e2)
    ln = np.linalg.norm(n, axis=1)
    ln[ln == 0] = 1.0
    n = n / ln[:, None]
    light = np.array([-0.4, -0.5, 0.77])
    lam = np.clip(n @ light, 0.0, 1.0) * 0.72 + 0.28
    return [(int(base[0] * v), int(base[1] * v), int(base[2] * v)) for v in lam]


def _draw(tris, labels, colours, ax, ay, az, width, sc=None):
    """One orthographic view, drawn back to front.

    `sc` is millimetres-to-pixels and is passed IN, shared by all three views.
    Left to scale itself, each view filled its own panel — so a 40 x 28 x 30
    model came out with the front view at 10.9 px/mm and the side at 14.5, and
    the same sphere was visibly a different size in two views standing next to
    each other. That is exactly the comparison a front/side/plan sheet exists
    to support, and it is the sheet he judges a physical print from.
    """
    import numpy as np
    from PIL import Image, ImageDraw

    pts = tris[:, :, [ax, ay]]
    flat = pts.reshape(-1, 2)
    lo, hi = flat.min(axis=0), flat.max(axis=0)
    if sc is None:
        span = float(max(hi[0] - lo[0], hi[1] - lo[1])) or 1.0
        sc = (width - 24) / span
    h = int((hi[1] - lo[1]) * sc) + 24
    # Centred rather than left-aligned: now that the scale is shared, a view of
    # a narrower face genuinely IS narrower, and pinning it left would read as
    # the model being off to one side.
    x0 = (width - (hi[0] - lo[0]) * sc) / 2.0

    img = Image.new("RGB", (width, max(h, 48)), BG)
    d = ImageDraw.Draw(img)
    order = np.argsort(tris[:, :, az].mean(axis=1))
    for i in order:
        xy = [((p[0] - lo[0]) * sc + x0, h - ((p[1] - lo[1]) * sc + 12))
              for p in pts[i]]
        d.polygon(xy, fill=colours[labels[i]][i])
    return img


def shot(stl_path: str, out_path: str = "", width: int = WIDTH) -> str:
    """Draw a model — front, side and plan — and return the file written.

    Blocking and numpy-heavy: call it through `asyncio.to_thread`. It is on the
    completion path of a render that already took minutes, so a second here is
    not the problem; the event loop is.
    """
    import numpy as np
    from PIL import Image, ImageDraw

    import assembly
    import meshio

    named = assembly.read_manifest(stl_path)
    if len(named) >= 2:
        chunks, names = [], []
        for n, p in named:
            try:
                chunks.append(meshio.load(p))
                names.append(n)
            except Exception:
                log.debug("part %s would not load for the picture", n)
        if not chunks:
            raise meshio.BadMesh("none of that model's parts would load")
        tris = np.concatenate(chunks, axis=0)
        labels = np.concatenate([np.full(len(c), i, dtype=np.int32)
                                 for i, c in enumerate(chunks)])
    else:
        tris = meshio.load(stl_path)
        labels = np.zeros(len(tris), dtype=np.int32)
        names = []

    if len(tris) == 0:
        raise meshio.BadMesh("there is nothing in that model to draw")
    if len(tris) > MAX_DRAW_TRIS:
        step = int(np.ceil(len(tris) / MAX_DRAW_TRIS))
        tris, labels = tris[::step], labels[::step]

    palette = [PART_HUES[i % len(PART_HUES)] for i in range(max(1, len(names)))]
    colours = [_shade(tris, c) for c in palette] or [_shade(tris, CYAN)]

    flat = tris.reshape(-1, 3)
    dims = flat.max(axis=0) - flat.min(axis=0)
    # ONE SCALE FOR ALL THREE VIEWS, taken from the largest dimension of the
    # model rather than of each face. This is what makes the sheet readable as
    # a drawing instead of three unrelated pictures.
    sc = (width - 24) / (float(max(dims)) or 1.0)
    imgs = [_draw(tris, labels, colours, ax, ay, az, width, sc)
            for _, ax, ay, az in VIEWS]

    top = 26
    h = max(i.height for i in imgs)
    sheet = Image.new("RGB", (width * len(imgs), h + top), BG)
    for k, im in enumerate(imgs):
        # Vertically centred, so a shorter view sits level with the others
        # rather than hanging from the caption.
        sheet.paste(im, (width * k, top + (h - im.height) // 2))
    label = os.path.basename(stl_path)
    label = label[:-4] if label.lower().endswith((".stl", ".obj")) else label
    caption = (f"{label}   {dims[0]:.0f} x {dims[1]:.0f} x {dims[2]:.0f} mm"
               f"   front / side / plan")
    if names:
        # PIL's default font has no em dash and draws a box for it.
        caption += f"   |   {', '.join(names[:6])}"
    ImageDraw.Draw(sheet).text((10, 8), caption[:170], fill=(150, 200, 230))

    if not out_path:
        from tools.fabrication import work_dir
        out_path = str(work_dir() / f"{label}.png")
    sheet.save(out_path)
    return out_path


async def shot_async(stl_path: str, out_path: str = "") -> str:
    """`shot`, off the event loop. Returns "" rather than raising.

    A picture that will not draw must never turn a finished model into a failed
    one — he loses the photograph, not the part.
    """
    import asyncio
    try:
        return await asyncio.to_thread(shot, stl_path, out_path)
    except Exception:
        log.warning("could not draw %s", stl_path, exc_info=True)
        return ""
