"""One thing with another thing on it.

His example: *"Render me a baseball with Spider-Man's face on it — it does that,
and it has the dimensions for it and how I would need it printed."* And then,
of the same object: *"make his eyes smaller."*

Two different things are being asked for in one sentence, and each has a route
that already works. A baseball is a measured object — researched, written as
OpenSCAD, exact. A face on it is a picture, traced into outlines and holes and
extruded. Trying to make one route do both produces the worse of each: OpenSCAD
cannot draw a spider, and a reconstruction of "a baseball with a face on it"
is a lumpy ball.

So it is built as two named parts and joined:

    baseball          the base, by whatever route suits it
    spider-man face   traced, scaled to sit on the base, raised off its face

WHICH MAKES THE SECOND HALF OF HIS SENTENCE WORK FOR FREE. The face keeps its
traced shapes, so "make his eyes smaller" finds the eyes on the part that has
them. A single fused mesh would have no eyes in it, and no way to get them back.

THE PLACEMENT IS ARITHMETIC. Centred on the base's own top face, scaled to a
fraction of its width, lifted so it sits proud rather than buried. Nothing here
asks a model where to put anything — the same rule the component bench follows,
for the same measured reason.
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger("jarvis.composite")

# How much of the base's width the decoration covers.
FACE_FRACTION = 0.55
# How far it stands off the surface, as a fraction of the base's height.
RELIEF = 0.06

# "a baseball with spider-man's face on it", "a mug with the batman logo".
# The trailing "on it" is optional because half the time he does not say it.
_WITH = re.compile(
    r"^\s*(?P<base>.+?)\s+with\s+(?P<mark>.+?)"
    r"(?:\s+(?:on|onto|across)\s+(?:it|the\s+\w+|its\s+\w+))?\s*$", re.I)

# Words that mean the second half is a DECORATION rather than a component.
# "a mug with a lid" is an assembly; "a mug with a logo" is this.
_MARKS = re.compile(
    r"\b(?:face|logo|emblem|badge|symbol|crest|sign|mark|design|pattern|"
    r"picture|image|art|artwork|graphic|lettering|text|name|initials)\b", re.I)


def split(description: str) -> tuple[str, str] | None:
    """(the thing, the thing on it), or None if it is not that kind of request.

    Only when the second half is plainly a DECORATION. "A mug with a lid" is two
    components and belongs to the assembly path; "a mug with a logo" is one
    object with a picture on it, and they are not the same build at all.
    """
    m = _WITH.match((description or "").strip())
    if not m:
        return None
    base = m.group("base").strip()
    mark = m.group("mark").strip()
    if not base or not mark or not _MARKS.search(mark):
        return None
    return base, mark


async def build(description: str, name: str = "") -> dict:
    """Make the base, make the mark, and put one on the other."""
    import asyncio

    import assembly
    import create3d
    import meshio
    from tools.fabrication import safe_name, work_dir

    got = split(description)
    if not got:
        return {"error": "that isn't one thing with another on it, sir",
                "not_composite": True}
    base_desc, mark_desc = got
    stem = safe_name(name or description)

    # The base, by whatever route suits it — it is an ordinary request.
    base = await create3d.build(create3d.choose_tier(base_desc, ""),
                                description=base_desc, name=f"{stem}-base")
    if base.get("error") or not base.get("stl"):
        return {"error": f"I couldn't make the {base_desc}, sir",
                "why": base.get("error", "")}

    # The mark is always traced: it is a picture of a thing, not a thing.
    pic = await create3d.reference_image(mark_desc, flat=True)
    if not pic:
        return {"error": f"I couldn't find a clean picture of {mark_desc}, sir",
                "base_only": base.get("stl")}
    mark = await create3d.from_image(pic, name=f"{stem}-mark")
    if mark.get("error") or not mark.get("stl"):
        return {"error": f"I couldn't trace {mark_desc}, sir",
                "why": mark.get("error", ""), "base_only": base.get("stl")}

    # ------------------------------------------------------------- placement
    b = await asyncio.to_thread(meshio.load, base["stl"])
    k = await asyncio.to_thread(meshio.load, mark["stl"])
    bf, kf = b.reshape(-1, 3), k.reshape(-1, 3)
    b_lo, b_hi = bf.min(axis=0), bf.max(axis=0)
    k_lo, k_hi = kf.min(axis=0), kf.max(axis=0)

    b_w = float(max(b_hi[0] - b_lo[0], b_hi[1] - b_lo[1]))
    k_w = float(max(k_hi[0] - k_lo[0], k_hi[1] - k_lo[1])) or 1.0
    scale = (b_w * FACE_FRACTION) / k_w

    k_scaled = (k - (k_lo + k_hi) / 2.0) * scale
    # Sitting proud of the top face rather than buried in it: its own underside
    # goes to the base's top, less a little so the two actually meet.
    sf = k_scaled.reshape(-1, 3)
    lift = float(b_hi[2]) - float(sf[:, 2].min()) - float(b_hi[2] - b_lo[2]) * RELIEF
    centre = ((b_lo[0] + b_hi[0]) / 2.0, (b_lo[1] + b_hi[1]) / 2.0, lift)
    k_placed = meshio.translated(k_scaled, centre)

    d = work_dir()
    base_out = d / f"{stem}.{safe_name(base_desc).replace('-', '_')}.stl"
    mark_out = d / f"{stem}.{safe_name(mark_desc).replace('-', '_')}.stl"
    await asyncio.to_thread(meshio.write_stl, b, str(base_out))
    await asyncio.to_thread(meshio.write_stl, k_placed, str(mark_out))

    # THE MARK KEEPS ITS TRACED SHAPES, moved and scaled to match, so "make his
    # eyes smaller" still has eyes to find. Without this the composite is a
    # fused lump with no features in it.
    kept = create3d.load_shapes(mark["stl"])
    if kept.get("shapes"):
        create3d.save_shapes(str(mark_out), kept["shapes"],
                             kept.get("thickness_mm", 3.0),
                             kept.get("width_mm", 60.0) * scale)

    import numpy as np
    whole = d / f"{stem}.stl"
    await asyncio.to_thread(meshio.write_stl,
                            np.concatenate([b, k_placed], axis=0), str(whole))
    assembly.write_manifest(str(whole), [
        {"name": base_out.name.split(".")[-2], "stl": str(base_out)},
        {"name": mark_out.name.split(".")[-2], "stl": str(mark_out)},
    ])
    return {"stl": str(whole), "name": stem, "composite": True,
            "parts": [base_out.name.split(".")[-2], mark_out.name.split(".")[-2]],
            "part_count": 2, "base": base_desc, "mark": mark_desc,
            "note": f"a {base_desc} with {mark_desc} raised on it",
            "instruction": ("Two named parts: the object and the design on it. "
                            "The design can still be changed by feature — "
                            "'make his eyes smaller' works on it.")}
