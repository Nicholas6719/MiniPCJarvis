"""Four ways to turn something into a mesh, and honesty about which one ran.

    tier  technique                        typical  editable        printable
    ----  -------------------------------  -------  --------------  -----------------
    0     a parametric template            ~0.2 s   yes, by voice   yes, exactly
    1     the model writes OpenSCAD        ~27 s    yes, by voice   yes, exactly
    2     an image traced and extruded     ~1 s     thickness/scale yes, sharp
    2     a photo as a relief (lithophane) ~0.1 s   thickness/scale yes, watertight
    3     a photo to a mesh (TripoSR)      ~19 s    no              a likeness
    4     text -> reference picture -> 3    ~35 s    no              a likeness

WHAT THE RADEON 780M IS ACTUALLY WORTH, because the first answer here was wrong
and it changed a decision. A 2048-square matmul under `torch-directml` came back
1.3x the Ryzen 7 8845HS, and that was written down as "no acceleration to be
had". A matmul is a bad proxy: it is memory-bound, it flatters eight Zen 4 cores
with AVX-512, and `torch-directml` is not the best DirectML implementation
available. Re-measured on 2026-09-02 against the ONNX models this project
ALREADY ships, through ONNX Runtime's DirectML provider:

    yolox   1x3x640x640   CPU  65.3 ms    780M  17.3 ms    3.76x
    sface   1x3x112x112   CPU   7.9 ms    780M   4.7 ms    1.67x

So the iGPU is worth roughly 4x on a real convolutional workload, and the
smaller the tensor the less it wins — which is the shape of every GPU, and the
reason the first measurement misled.

A PICTURE STILL DEFAULTS TO THE RELIEF regardless, because that takes a tenth of
a second and is the thing people actually print from photographs. Tier 3 is for
when he asks for a real reconstruction.


EVERY RESULT SAYS WHICH TIER MADE IT, because what he can do next depends
entirely on that. A tier-1 part can be edited by voice — "make the hole bigger"
rewrites a parameter. A tier-3 mesh has no parameters at all, and pretending
otherwise wastes his time on a request that cannot be honoured.

TIERS 3 AND 4 ARE NOT BUNDLED AND MUST NOT BE. They need PyTorch, which is about
2.5 GB; the sidecar is already 980 MB. They live in their own environment under
`C:\\AI\\model3d` and are invoked as a subprocess, exactly the way
`llm.server_binary` points at `C:\\AI\\llama.cpp`. If that install is not there,
the tier says so in a sentence — it does not fall back to a different tier and
hand him something he did not ask for.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
from pathlib import Path

from config import config

log = logging.getLogger("jarvis.create3d")

TIERS = (0, 1, 2, 3, 4, 5, 6, 7)

# Below this in its SMALLEST dimension, a generated part is a sliver rather than
# a part: no printer can lay it down and nobody asked for it. Half a millimetre
# is under the 0.8 mm minimum wall by enough that a legitimately thin plate is
# never caught by it.
MIN_SENSIBLE_MM = 0.5

# The most a reference picture may weigh. Its URL comes from a web search rather
# than from him, so it needs an upper bound as well as a lower one.
MAX_REFERENCE_BYTES = 24 * 1024 * 1024

TIER_NOTE_RELIEF = ("the picture as a relief — dark is thick, so hold it up to a "
                    "light and the photograph appears")

TIER_NOTE = {
    0: "from a parametric template, so it's exact, instant, and you can change "
       "it by voice",
    1: "written as OpenSCAD, so it's exact and you can change it by voice",
    2: "traced from the picture and extruded, so the outline is sharp",
    3: "a mesh built from the photo — a likeness rather than a measured part",
    4: "built from a reference picture I found, so it's a likeness of that "
       "picture rather than a measured part",
    # SAID BEFORE THE WORK RUNS, so it must describe the ATTEMPT. It used to
    # say "somebody sculpted this and I've fetched their file" — announced at
    # submission, before anything had been searched for, and then contradicted
    # forty seconds later by "nobody had one to download, so I built this one".
    # `spoken_caveats` says which one actually happened, at the end, when it is
    # known.
    5: "I'll look for one somebody has already made, and build it from a "
       "reference picture if nobody has",
    # SAID BEFORE THE WORK RUNS, like tier 5's, so it describes the plan.
    6: "made piece by piece — each part built on its own, so you can take them "
       "one at a time",
    7: "the object and the design on it, made separately and put together — so "
       "the design can still be changed",
}

# Words that say he wants a FLAT emblem out of a picture rather than a 3D
# likeness of what is in it. A logo becomes a badge; a photo of a chair becomes
# a chair.
_FLAT = re.compile(r"\b(?:emblem|logo|badge|sign|plaque|stencil|silhouette|"
                   r"outline|flat|extrude|extruded|keychain|key ?ring|coaster)\b", re.I)

# Things OpenSCAD is genuinely good at: parts with dimensions. If he is
# describing hardware, tier 1 gives him something exact, printable and editable —
# which is strictly better than a generated blob of the same shape.
_MECHANICAL = re.compile(
    r"\b(?:bracket|plate|box|case|lid|holder|mount|stand|clip|spacer|washer|"
    r"adapter|adaptor|hook|knob|handle|bushing|standoff|shim|jig|fixture|"
    r"cube|cylinder|tube|cone|sphere|ring|disc|disk|rod|bar|block|"
    r"gear|pulley|flange|gasket|grommet|enclosure|tray|rack|peg|dowel)\b", re.I)
_DIMENSIONED = re.compile(r"\b\d+(?:\.\d+)?\s*(?:mm|millimet(?:er|re)s?|cm|inch|in|\")\b", re.I)

# Shapes nobody writes as code. OpenSCAD is a SOLID modeller — primitives,
# booleans, extrusions — so it is excellent at a tape measure and hopeless at a
# dragon, and no amount of research changes that. These keep the photo
# reconstruction, which is soft and unmeasured and still the better answer for
# them. Being honest about which is which is the whole point of having tiers.
_ORGANIC = re.compile(
    r"\b(?:dragon|duck|animal|dog|cat|horse|bird|fish|lion|bear|"
    r"figure|figurine|statue|sculpture|bust|character|person|people|"
    r"face|head|skull|hand|body|creature|monster|alien|"
    r"suit|armou?r|helmet|mask|costume|"
    # SCULPTURAL VEHICLES too. A spaceship has no dimensions to look up and no
    # canonical form — it is a shape somebody drew, which is the same problem a
    # dragon is. A rocket or a wheel is not here: those ARE cones and cylinders.
    r"spaceship|starship|spacecraft|space ?shuttle|ufo|"
    r"car|truck|motorbike|motorcycle|aeroplane|airplane|jet|"
    r"tree|flower|plant|leaf|shell|rock|organic)\b", re.I)


def choose_tier(description: str = "", image_path: str = "") -> int:
    """Which technique fits what he asked for.

    Deliberately explicit rather than asking the model: this decides how long he
    waits and whether the result can be edited afterwards, and a coin-flip
    dressed up as a judgement is the wrong thing to build a wait on. He can
    always name a tier outright.
    """
    desc = (description or "").strip()
    if image_path:
        # A picture defaults to the FAST printable thing, not a minutes-long
        # reconstruction he did not ask for. A logo becomes an extruded outline;
        # anything else becomes a relief, which takes about a second. A real
        # mesh of the object is tier 3 and only on request.
        if _FLAT.search(desc):
            return 2
        return 3 if _RECONSTRUCT.search(desc) else 2
    # ONE OBJECT WITH ANOTHER RENDER ON IT, before any other rule reads the
    # words. "A mug with the batman logo" contains "logo" and the flat-emblem
    # rule claimed the whole sentence — so he got a logo and no mug.
    import composite
    if composite.split(desc):
        return 7

    # A shape we can write out exactly needs no model and takes a fifth of a
    # second. Checked first, because it changes both the wait and whether he is
    # asked about it at all.
    import parts_library
    if parts_library.match(desc):
        return 0
    if _MECHANICAL.search(desc) or _DIMENSIONED.search(desc):
        return 1
    # A FLAT THING IS FLAT WHETHER OR NOT HE HANDED OVER THE PICTURE.
    #
    # `_FLAT` already knew "emblem" and "logo" — but it was only consulted when
    # an image_path was given, so "create me a 3D image of the Spider-Man
    # emblem" fell through to tier 4 and was RECONSTRUCTED: a single-photo mesh
    # of a flat two-colour logo, which is a blob. His words: "doesn't really look
    # right... didn't seem like it took any references".
    #
    # It did take a reference — that is what tier 4 does — and then used it for
    # the wrong technique. An emblem wants its outline traced and extruded,
    # which is sharp, fast and actually printable. Tier 2 fetches the reference
    # itself when he has not supplied one.
    if _FLAT.search(desc):
        return 2
    # ORGANIC AND SCULPTURAL THINGS STAY WITH THE PHOTO RECONSTRUCTION.
    #
    # OpenSCAD is a solid modeller: primitives, booleans, extrusions. It is
    # excellent at a tape measure and hopeless at a dragon, and no amount of
    # research changes that. A duck, a face, a helmet, a figure — those are
    # shapes nobody writes as code, and a reconstruction of a photograph is
    # genuinely the better answer for them even though it is soft and
    # unmeasured. Being honest about which is which is the whole point of tiers.
    # A THING WITH NAMED PIECES IS BUILT AS PIECES. "Iron Man Mark 3 SUIT" is
    # the case he described: a whole suit cannot be reconstructed from one
    # photograph, but a helmet can be found and a gauntlet can be rebuilt from a
    # picture of a gauntlet — and then he can zoom into each. Tier 6 falls back
    # to building the thing whole when it turns out not to come apart.
    import components
    if components.worth_splitting(desc):
        return 6
    if _ORGANIC.search(desc):
        # SOMEBODY HAS ALREADY MADE THIS ONE, AND MADE IT PROPERLY.
        #
        # Reconstruction from a single photograph gives a soft lump, and it is
        # what "render Iron Man Mark 3" used to produce. Nobody prints a suit
        # that way — they download a model somebody spent weeks sculpting, and
        # the Mark 3 exists in dozens of versions. Measured: a search finds real
        # free STLs, and a GitHub result can be followed all the way to the file.
        #
        # Tier 4 remains the fallback INSIDE tier 5 for THESE, so a request
        # never dead-ends because the web happened not to have it. Everything
        # else that reaches tier 5 falls back to tier 1 instead.
        return 5
    # A NAMED REAL OBJECT IS RESEARCHED AND BUILT, NOT PHOTOGRAPHED AND GUESSED.
    #
    # This used to return 4: find one photograph and run a single-image
    # reconstruction over it, which gives a likeness of a photograph — soft,
    # unmeasured, unprintable and impossible to edit afterwards.
    #
    # His instruction: "I expect him to do the research he needs to do to make
    # that." So the web becomes an idea base rather than a texture source — a
    # short brief of the object's form and real dimensions — and the model
    # writes OpenSCAD from it, which is exact, printable and editable by voice.
    # Tier 3/4 remain for when he actually asks to SCAN or RECONSTRUCT something.
    #
    # ...BUT THE WEB GETS ASKED FIRST, because "render Iron Man Mark 3" reached
    # here and came back as OpenSCAD. `_ORGANIC` knows "suit" and "helmet"; it
    # cannot know that a Mark 3 is one, and listing every character name is the
    # reflex he objected to. Everything that survives to this line is a thing in
    # the world with no dimensions in the sentence — precisely the case where a
    # model somebody already sculpted beats anything we can invent. Tier 5 falls
    # back to tier 1 when the web has nothing, so being wrong costs one search.
    return 5


# Words that ask for a RELIEF — the picture's brightness becoming height. This
# is the lithophane, and it is the most useful thing anyone actually prints from
# a photograph.
_RELIEF = re.compile(r"\b(?:lithophane|litho|relief|emboss(?:ed)?|engrav\w*|"
                     r"height ?map|3d photo|raised)\b", re.I)

# ...and words that ask for a real reconstructed MESH of the thing in the photo,
# which is tier 3 and slow. Only on request: the default for a picture is the
# fast, printable thing, not a minutes-long reconstruction he did not ask for.
_RECONSTRUCT = re.compile(r"\b(?:scan|mesh|photogrammetry|triposr|reconstruct\w*|"
                          r"3d model of (?:the|this|that) (?:object|thing|item)|"
                          r"actual shape|real shape|full 3d)\b", re.I)


def note_for(tier: int, description: str = "", image_path: str = "") -> str:
    """The one-line explanation, decided the same way `build` decides the work.

    Tier 2 is two techniques wearing one number — an extruded outline for a logo,
    a relief for a photograph — so the note has to be worked out rather than
    looked up. Submitting a photograph and being told it would be "traced and
    extruded", then getting a relief, is a small lie that costs trust in every
    other thing the tool says about itself.
    """
    if int(tier) == 2:
        desc = description or ""
        outline = bool(_FLAT.search(desc)) and not _RELIEF.search(desc)
        return TIER_NOTE[2] if outline else TIER_NOTE_RELIEF
    return TIER_NOTE.get(int(tier), "")


# ---------------------------------------------------------- tier 2, as a relief
MAX_RELIEF_GRID = 120       # ~57k triangles: detailed enough, still light to draw


def relief_stl(image_path: str, out_path: str, width_mm: float = 80.0,
               thick_mm: float = 3.0, base_mm: float = 0.8) -> dict | None:
    """A photograph as a printable relief. BLOCKING — numpy work, call in a thread.

    A LITHOPHANE, in the ordinary sense: brightness becomes height, dark becomes
    thick, and held up to a light the picture appears. It is the one thing almost
    everybody wants from a photo and a printer, it needs no model of any kind,
    and it takes about a second.

    DARK IS THICK, which is the way round that works: thicker plastic passes less
    light, so the dark parts of the picture stay dark when it is lit from behind.
    Getting this backwards produces a photographic negative, which looks like a
    bug and is one.
    """
    try:
        import cv2
        import numpy as np
    except Exception:
        return None
    img = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if img is None or img.size == 0:
        return None

    h, w = img.shape
    scale = MAX_RELIEF_GRID / float(max(h, w))
    if scale < 1.0:
        img = cv2.resize(img, (max(2, int(w * scale)), max(2, int(h * scale))),
                         interpolation=cv2.INTER_AREA)
    # A little blur first: printing per-pixel noise wastes detail the nozzle
    # cannot reproduce anyway, and it makes the surface look sanded.
    img = cv2.GaussianBlur(img, (3, 3), 0)
    gh, gw = img.shape

    z = base_mm + (1.0 - img.astype("float64") / 255.0) * thick_mm
    px = width_mm / float(gw - 1)
    xs = np.arange(gw) * px
    ys = np.arange(gh) * px

    def top(r, c):
        return (xs[c], ys[gh - 1 - r], z[r, c])

    def bot(r, c):
        return (xs[c], ys[gh - 1 - r], 0.0)

    tris = []
    for r in range(gh - 1):
        for c in range(gw - 1):
            a, b, cc, d = top(r, c), top(r, c + 1), top(r + 1, c + 1), top(r + 1, c)
            tris.append((a, d, cc))
            tris.append((a, cc, b))
            a2, b2, c2, d2 = bot(r, c), bot(r, c + 1), bot(r + 1, c + 1), bot(r + 1, c)
            tris.append((a2, c2, d2))
            tris.append((a2, b2, c2))
    # The four walls, so it is a solid and not a sheet. A slicer will not print a
    # surface, and printcheck would rightly call it not watertight.
    for c in range(gw - 1):
        for r, flip in ((0, False), (gh - 1, True)):
            t1, t2 = top(r, c), top(r, c + 1)
            b1, b2 = bot(r, c), bot(r, c + 1)
            tris += ([(t1, b1, b2), (t1, b2, t2)] if flip
                     else [(t1, t2, b2), (t1, b2, b1)])
    for r in range(gh - 1):
        for c, flip in ((0, True), (gw - 1, False)):
            t1, t2 = top(r, c), top(r + 1, c)
            b1, b2 = bot(r, c), bot(r + 1, c)
            tris += ([(t1, b1, b2), (t1, b2, t2)] if flip
                     else [(t1, t2, b2), (t1, b2, b1)])

    import struct
    arr = np.asarray(tris, dtype="float32")
    with open(out_path, "wb") as f:
        f.write(b"\0" * 80)
        f.write(struct.pack("<I", len(arr)))
        blank = struct.pack("<3f", 0.0, 0.0, 0.0)
        for t in arr:
            f.write(blank)
            f.write(t.tobytes())
            f.write(b"\0\0")
    return {"triangles": int(len(arr)), "grid": [int(gw), int(gh)]}


async def from_relief(image_path: str, name: str = "") -> dict:
    """TIER 2, as a relief: a photograph made printable, in about a second."""
    from tools.fabrication import safe_name, work_dir

    p = Path(str(image_path or "")).expanduser()
    if not p.exists():
        return {"error": "I can't find that picture, sir", "tier": 2}
    base = safe_name(name or p.stem)
    stl = work_dir() / f"{base}.stl"
    info = await asyncio.to_thread(relief_stl, str(p), str(stl))
    if not info:
        return {"error": "I couldn't read that picture, sir", "tier": 2}
    return {"tier": 2, "name": base, "stl": str(stl), "mode": "relief",
            "triangles": info["triangles"], "note": TIER_NOTE_RELIEF}


# --------------------------------------------------------------------- tier 2
def _outline_scad(points: list[list[float]], height_mm: float,
                  width_mm: float) -> str:
    """An OpenSCAD polygon, extruded. Scaled to the width he asked for."""
    pts = ", ".join(f"[{x:.3f},{y:.3f}]" for x, y in points)
    return (f"$fn = 48;\n"
            f"// traced from an image, extruded {height_mm:g} mm\n"
            f"linear_extrude(height = {height_mm:g})\n"
            f"  resize([{width_mm:g}, 0, 0], auto = true)\n"
            f"    polygon(points = [{pts}]);\n")


def trace_outline(image_path: str, max_points: int = 400) -> list[list[float]] | None:
    """The largest outline in a picture, as a simplified polygon.

    BLOCKING — cv2 work, called from a thread. Returns None when there is nothing
    to trace, which is a real answer: a photograph of a room has no single
    silhouette, and inventing one would produce a shape he did not ask for.
    """
    try:
        import cv2
    except Exception:
        return None
    img = cv2.imread(str(image_path), cv2.IMREAD_UNCHANGED)
    if img is None:
        return None

    # An alpha channel IS the outline — a logo with transparency needs no
    # guessing at all, and guessing would do worse.
    if img.ndim == 3 and img.shape[2] == 4 and img[:, :, 3].min() < 250:
        mask = (img[:, :, 3] > 127).astype("uint8") * 255
    else:
        grey = cv2.cvtColor(img[:, :, :3] if img.ndim == 3 else img, cv2.COLOR_BGR2GRAY) \
            if img.ndim == 3 else img
        # Otsu rather than a fixed threshold: a scanned black logo on white and a
        # white one on black both have to work, and a constant only serves one.
        _, mask = cv2.threshold(grey, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        if mask.mean() > 127:                 # mostly white: the subject is dark
            mask = 255 - mask

    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cnts = [c for c in cnts if cv2.contourArea(c) > 32]
    if not cnts:
        return None
    c = max(cnts, key=cv2.contourArea)
    # Simplify until it is a shape rather than a bitmap traced point by point:
    # a 400-point polygon renders slowly and prints no better.
    peri = cv2.arcLength(c, True)
    eps = 0.0015 * peri
    while True:
        approx = cv2.approxPolyDP(c, eps, True)
        if len(approx) <= max_points or eps > 0.05 * peri:
            break
        eps *= 1.4
    h = mask.shape[0]
    # Y down in an image, Y up in a model. Flipped here rather than in OpenSCAD,
    # so the polygon is already the right way up wherever it is looked at.
    return [[float(p[0][0]), float(h - p[0][1])] for p in approx.reshape(-1, 1, 2)]


# HOW SMALL A SHAPE STILL COUNTS, as a fraction of the biggest one. A spider's
# legs are small next to its body but they are the emblem; a stray speck of JPEG
# noise is not.
MIN_PART_FRACTION = 0.02


def trace_shapes(image_path: str, max_points: int = 400) -> list[dict] | None:
    """Every significant shape in the picture, with its holes.

    `trace_outline` returns ONE outline and drops everything inside it, which is
    fine for a silhouette and wrong for an emblem: the Spider-Man badge came back
    as a plain oval disc, because `RETR_EXTERNAL` discards internal detail and
    `max(contourArea)` then keeps only the outer boundary. A logo is a figure
    WITH holes, and often several parts.

    BLOCKING — cv2 work, called from a thread.
    """
    try:
        import cv2
    except Exception:
        return None
    img = cv2.imread(str(image_path), cv2.IMREAD_UNCHANGED)
    if img is None:
        return None

    if img.ndim == 3 and img.shape[2] == 4 and img[:, :, 3].min() < 250:
        mask = (img[:, :, 3] > 127).astype("uint8") * 255
    else:
        grey = (cv2.cvtColor(img[:, :, :3], cv2.COLOR_BGR2GRAY)
                if img.ndim == 3 else img)
        _, mask = cv2.threshold(grey, 0, 255,
                                cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        if mask.mean() > 127:
            mask = 255 - mask

    # RETR_CCOMP gives two levels: outer boundaries and the holes inside them,
    # which is exactly the structure a logo has.
    cnts, hier = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    if hier is None or not len(cnts):
        return None
    hier = hier[0]
    h = mask.shape[0]

    def simplify(c):
        peri = cv2.arcLength(c, True)
        eps = 0.0015 * peri
        while True:
            approx = cv2.approxPolyDP(c, eps, True)
            # Stop simplifying much earlier than the silhouette tracer does. At
            # 0.05 of the perimeter a spider becomes an ellipse, which is
            # literally what he was shown.
            if len(approx) <= max_points or eps > 0.008 * peri:
                break
            eps *= 1.4
        return [[float(q[0][0]), float(h - q[0][1])] for q in approx]

    outers = [(i, cv2.contourArea(c)) for i, c in enumerate(cnts)
              if hier[i][3] < 0 and cv2.contourArea(c) > 32]
    if not outers:
        return None
    biggest = max(a for _, a in outers)
    shapes: list[dict] = []
    for i, area in outers:
        if area < biggest * MIN_PART_FRACTION:
            continue          # noise, not a leg
        outline = simplify(cnts[i])
        if len(outline) < 3:
            continue
        holes = []
        child = hier[i][2]
        while child >= 0:
            if cv2.contourArea(cnts[child]) > max(32.0, area * 0.004):
                hp = simplify(cnts[child])
                if len(hp) >= 3:
                    holes.append(hp)
            child = hier[child][0]
        shapes.append({"outline": outline, "holes": holes})
    return shapes or None


# Two colours are the same colour when they are this close. Loose on purpose:
# JPEG artefacts and lighting move a flat red around by more than a little, and
# splitting a body into three reds is worse than not splitting it at all.
_COLOUR_SAME = 60


def _colour_distance(a: str, b: str) -> float:
    try:
        ar, ag, ab = (int(a[i:i + 2], 16) for i in (1, 3, 5))
        br, bg, bb = (int(b[i:i + 2], 16) for i in (1, 3, 5))
    except (ValueError, IndexError):
        return 1e9
    return ((ar - br) ** 2 + (ag - bg) ** 2 + (ab - bb) ** 2) ** 0.5


async def _colour_parts(image_path: str, stl_path: str, shapes: list,
                        thickness_mm: float, width_mm: float) -> dict:
    """Split a traced design into one part per colour, when it has more than one.

    A hole with its own colour is extruded as a SOLID filling that hole, so the
    pieces tile: they share the contour that separates them. That is what turns
    "the eyes are missing" into "the eyes are white".
    """
    import asyncio

    import assembly
    import colours
    import features
    from tools.fabrication import _run, openscad_path

    try:
        pieces = features.label(shapes)
        found = await asyncio.to_thread(colours.sample, image_path, shapes, pieces)
    except Exception:
        log.debug("could not read the colours out of %s", image_path, exc_info=True)
        return {}
    if not found:
        return {}

    body = next((p for p in pieces if p.get("name") == "outline"), None)
    if body is None:
        return {}
    base_colour = found.get("outline", "")

    # Holes whose colour is genuinely different from the body around them.
    distinct = [p for p in pieces
                if p.get("hole") is not None and found.get(p.get("name"))
                and (not base_colour
                     or _colour_distance(found[p["name"]], base_colour)
                     > _COLOUR_SAME)]
    if not distinct:
        return {}                        # one colour: one part, as before

    exe = openscad_path()
    if not exe:
        return {}
    from pathlib import Path
    stl = Path(stl_path)
    # ONE FRAME FOR ALL OF THEM. Emitted separately, each part would be resized
    # to fill the width on its own and the eyes would come out as wide as the
    # mask.
    frame = _design_frame(shapes)
    made = []

    # The body, holes and all, exactly as it was.
    made.append({"name": "body", "stl": str(stl), "colour": base_colour,
                 "colour_name": colours.label(base_colour)})

    # Each distinct hole, extruded as a solid that fills it.
    by_name: dict = {}
    for p in distinct:
        by_name.setdefault(p["name"], []).append(p)
    for name, group in by_name.items():
        part_shapes = [{"outline": g["points"], "holes": []} for g in group]
        out = stl.with_name(f"{stl.stem}.{name.replace(' ', '_')}.stl")
        scad = out.with_suffix(".scad")
        scad.write_text(_shapes_scad(part_shapes, thickness_mm, width_mm,
                                     frame=frame), encoding="utf-8")
        try:
            out.unlink()
        except OSError:
            pass
        rc, o, e = await _run([exe, "-o", str(out), str(scad)], 180)
        if rc != 0 or not out.exists():
            log.info("colour part %s did not build: %s", name,
                     (e or o or "").strip()[:120])
            continue
        made.append({"name": name.replace(" ", "_"), "stl": str(out),
                     "colour": found[name],
                     "colour_name": colours.label(found[name])})

    if len(made) < 2:
        return {}
    assembly.write_manifest(str(stl), made)
    return {"parts": [m["name"] for m in made],
            "colours": {m["name"]: m["colour"] for m in made}}


def shapes_path(stl_path: str) -> str:
    """Where a traced design's shapes live, beside the model."""
    base = str(stl_path)
    for ext in (".stl", ".obj"):
        if base.lower().endswith(ext):
            base = base[: -len(ext)]
            break
    return base + ".shapes.json"


def save_shapes(stl_path: str, shapes: list, thickness_mm: float,
                width_mm: float) -> str:
    """Record a traced design so a feature of it can be changed later."""
    import json
    p = shapes_path(stl_path)
    try:
        with open(p, "w", encoding="utf-8") as fh:
            json.dump({"shapes": shapes, "thickness_mm": thickness_mm,
                       "width_mm": width_mm}, fh)
        return p
    except OSError:
        log.warning("could not keep the traced shapes for %s", stl_path,
                    exc_info=True)
        return ""


def load_shapes(stl_path: str) -> dict:
    """A traced design's shapes, or {} for a model that was not traced."""
    import json
    import os
    p = shapes_path(stl_path)
    if not os.path.exists(p):
        return {}
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh) or {}
    except (OSError, ValueError):
        log.warning("could not read the traced shapes %s", p, exc_info=True)
        return {}


async def rebuild_shapes(stl_path: str, shapes: list, thickness_mm: float,
                         width_mm: float) -> dict:
    """Re-extrude an edited design over the model it came from."""
    from pathlib import Path

    from tools.fabrication import _run, openscad_path

    exe = openscad_path()
    if not exe:
        return {"error": "OpenSCAD is not installed", "unavailable": True}
    stl = Path(stl_path)
    scad = stl.with_suffix(".scad")
    scad.write_text(_shapes_scad(shapes, thickness_mm, width_mm),
                    encoding="utf-8")
    rc, out, err = await _run([exe, "-o", str(stl), str(scad)], 180)
    if rc != 0 or not stl.exists():
        return {"error": f"that change wouldn't build: "
                         f"{(err or out or '').strip()[:200]}"}
    save_shapes(str(stl), shapes, thickness_mm, width_mm)
    return {"stl": str(stl), "scad": str(scad)}


def _design_frame(shapes: list) -> tuple:
    """(min x, min y, max x, max y) over a whole traced design."""
    xs, ys = [], []
    for sh in shapes or []:
        for group in [sh.get("outline") or []] + list(sh.get("holes") or []):
            for x, y in group:
                xs.append(x)
                ys.append(y)
    if not xs:
        return (0.0, 0.0, 1.0, 1.0)
    return (min(xs), min(ys), max(xs), max(ys))


def _shapes_scad(shapes: list[dict], height_mm: float, width_mm: float,
                 frame: tuple | None = None) -> str:
    """Extrude every traced shape, with its holes cut out of it.

    `frame` is the bounding box of the WHOLE design, for when this is emitting
    one coloured part of it. Without it, `resize()` scales whatever it is given
    to fill `width_mm` — so a body and an eye emitted separately each became
    60 mm wide, and the eye stopped fitting the hole it was cut from. Given a
    frame, the scale is computed once from the whole design and applied here,
    and `resize` is left out because it would undo exactly that.
    """
    scale, ox, oy = 1.0, 0.0, 0.0
    if frame is not None:
        fx0, fy0, fx1, fy1 = frame
        span = max(fx1 - fx0, 1e-6)
        scale = width_mm / span
        ox, oy = fx0, fy0

    def place(x, y):
        return ((x - ox) * scale, (y - oy) * scale)

    body = []
    for sh in shapes:
        pts = list(sh["outline"])
        paths = [list(range(len(pts)))]
        for hole in sh["holes"]:
            start_i = len(pts)
            pts.extend(hole)
            paths.append(list(range(start_i, len(pts))))
        pt_s = ", ".join("[%.3f,%.3f]" % place(x, y) for x, y in pts)
        pa_s = ", ".join("[" + ",".join(str(i) for i in path) + "]"
                         for path in paths)
        body.append(f"    polygon(points = [{pt_s}], paths = [{pa_s}]);")
    joined = "\n".join(body)
    sizing = ("" if frame is not None
              else f"  resize([{width_mm:g}, 0, 0], auto = true)\n")
    return ("$fn = 48;\n"
            f"// traced from an image, extruded {height_mm:g} mm\n"
            f"linear_extrude(height = {height_mm:g})\n"
            + sizing +
            "    union() {\n" + joined + "\n    }\n")



async def from_image(image_path: str, name: str = "", thickness_mm: float = 3.0,
                     width_mm: float = 60.0) -> dict:
    """TIER 2: trace a picture and extrude it. No model involved, no GPU."""
    from tools.fabrication import _run, openscad_path, safe_name, work_dir

    p = Path(str(image_path or "")).expanduser()
    if not p.exists():
        return {"error": "I can't find that picture, sir", "tier": 2}
    exe = openscad_path()
    if not exe:
        return {"error": "OpenSCAD is not installed", "unavailable": True, "tier": 2}

    # EVERY shape and its holes, not the single largest outline. The emblem came
    # back as a plain oval because the old tracer kept the outer boundary and
    # threw the spider away.
    shapes = await asyncio.to_thread(trace_shapes, str(p))
    if not shapes:
        return {"error": "I couldn't find a clear outline in that picture, sir",
                "tier": 2}
    pts = [q for sh in shapes for q in sh["outline"]]

    base = safe_name(name or p.stem)
    d = work_dir()
    scad, stl = d / f"{base}.scad", d / f"{base}.stl"
    scad.write_text(_shapes_scad(shapes, thickness_mm, width_mm), encoding="utf-8")
    # KEEP THE DESIGN IN THE FORM IT WAS UNDERSTOOD IN. The .scad holds the same
    # geometry as six hundred loose coordinates with no eye in it; these are the
    # outlines and holes, which is what "make his eyes smaller" needs to exist.
    save_shapes(str(stl), shapes, thickness_mm, width_mm)
    # ...and, when the picture has more than one colour in it, as coloured parts.
    await _colour_parts(str(p), str(stl), shapes, thickness_mm, width_mm)
    rc, out, err = await _run([exe, "-o", str(stl), str(scad)], 180)
    if rc != 0 or not stl.exists():
        return {"error": f"OpenSCAD could not build that: {(err or out or '').strip()[:200]}",
                "tier": 2}
    # ...and, when the picture has more than one colour in it, as coloured parts.
    coloured = await _colour_parts(str(p), str(stl), shapes, thickness_mm,
                                   width_mm)
    return {"tier": 2, "name": base, "scad": str(scad), "stl": str(stl),
            **({"parts": coloured["parts"], "colours": coloured["colours"],
                "part_count": len(coloured["parts"])} if coloured else {}),
            "points": len(pts), "shapes": len(shapes),
            "holes": sum(len(sh["holes"]) for sh in shapes),
            "note": TIER_NOTE[2]}


# ----------------------------------------------------------------- tiers 3, 4
def model3d_dir() -> Path:
    return Path(config.get("fabrication", "model3d_dir",
                           default=r"C:\AI\model3d")).expanduser()


def model3d_python() -> str | None:
    """The interpreter of the separate environment, if it is installed.

    Its own env on purpose. PyTorch is ~2.5 GB and the sidecar is already 980 MB;
    bundling it would double the app to serve two tiers he may never use.
    """
    d = model3d_dir()
    for cand in (d / ".venv" / "Scripts" / "python.exe", d / "python.exe"):
        if cand.exists():
            return str(cand)
    found = shutil.which("python3d")
    return found or None


def available() -> dict:
    """Which of the heavy tiers can actually run right now.

    Tier 4 needs exactly what tier 3 needs, because it IS tier 3 with a
    reference picture in front of it. Whether the image search can reach the web
    is checked when it runs rather than here — a browser that is installed but
    offline is a different failure and deserves a different sentence.
    """
    py = model3d_python()
    d = model3d_dir()
    has3 = bool(py and (d / "photo_to_mesh.py").exists())
    return {"python": py, "dir": str(d), 3: has3, 4: has3}


def _missing(tier: int) -> dict:
    """The honest sentence when the install is not there.

    It does NOT fall back to another tier. Handing him a tier-1 approximation of
    something he asked for as a photo scan would look like success and be wrong,
    and he would only find out when the shape was not the thing in the photo.
    """
    what = "photo-to-mesh" if tier == 3 else "the model that builds from a picture"
    return {"error": f"I don't have the {what} model installed, sir — "
                     f"it lives outside the app, in {model3d_dir()}",
            "unavailable": True, "tier": tier}


async def _run_model3d(script: str, args: list[str], timeout: float) -> dict:
    """Run one of the heavy scripts as a subprocess and read back its JSON."""
    from tools.fabrication import _run
    py = model3d_python()
    if not py:
        return {"error": "the 3D model environment isn't installed"}
    rc, out, err = await _run([py, str(model3d_dir() / script), *args], timeout)
    if rc != 0:
        return {"error": (err or out or "it didn't come back").strip()[:200]}
    # The script's LAST line of stdout is its JSON; anything before it is a
    # progress log from the model, which is noise here but useful in the log.
    for line in reversed((out or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                continue
    return {"error": "the model produced no result"}


async def from_photo(image_path: str, name: str = "") -> dict:
    """TIER 3: a photo to a mesh. A likeness, and labelled as one."""
    from tools.fabrication import safe_name, work_dir
    if not available()[3]:
        return _missing(3)
    p = Path(str(image_path or "")).expanduser()
    if not p.exists():
        return {"error": "I can't find that picture, sir", "tier": 3}
    base = safe_name(name or p.stem)
    stl = work_dir() / f"{base}.stl"
    # OURS, NOT THE WORKER'S DEFAULT. 192 was chosen for speed when a render had
    # to feel quick; measured here, 512 costs 141 s against 25 and gives seven
    # times the triangles. His budget is quarter-hours, so the detail is free.
    res = int(config.get("fabrication", "reconstruct_resolution", default=512))
    r = await _run_model3d("photo_to_mesh.py",
                           [str(p), str(stl), "--resolution", str(res)], 900)
    if r.get("error"):
        return {**r, "tier": 3}
    return {"tier": 3, "name": base, "stl": str(stl), "note": TIER_NOTE[3],
            "repair_likely": True}


# What a picture has to be before a reconstruction is run on it. An icon
# reconstructs into nothing, and a banner reconstructs into a wall — both were in
# the search results for "a duck", including an orange Vine logo twice.
MIN_REFERENCE_PX = 256
# A STANDING FIGURE IS TALL, and a picture of one is tall with it. At 2.2 this
# rejected the two best references for "iron man mark 3" — both 474 x 1159, both
# a single whole suit — and left only the catalogue shots with two figures in
# them. The cap is here to catch banners and buttons, not portraits.
MAX_REFERENCE_ASPECT = 3.2


async def reference_image(description: str, flat: bool = False,
                          skip: int = 0) -> str:
    """A picture of the thing he described, saved to the work folder.

    TIER 4 IS TEXT -> PICTURE -> MESH, not a text-to-3D model, and that is a
    deliberate choice rather than a shortcut. Direct text-to-3D (Shap-E and its
    kin) is another 1.3 GB, minutes of CPU here, and produces the blobs the
    plan itself called "rarely printable". Finding a reference photograph and
    reconstructing THAT reuses the image search JARVIS already has and the
    tier-3 model already installed, and the result is markedly better.

    It is also the honest version only if it SAYS so, which TIER_NOTE[4] does:
    what comes back is a mesh of a picture of a dragon, not a dragon.
    """
    from search_brave_web import brave_web
    from tools.fabrication import safe_name, work_dir

    if not brave_web.available:
        return ""
    # WHAT MAKES A GOOD REFERENCE DEPENDS ON WHAT IT IS FOR.
    #
    # Tier 3 wants a photograph of the object. Tier 2 wants a CLEAN LOGO: high
    # contrast, plain background, ideally transparent. Asked for "the
    # spider-man emblem" the plain search returned photographs, Otsu turned one
    # into a single blob, and the traced result was an oval disc with a smudge
    # in the middle. The tracer was not the problem by then — the picture was.
    # WHAT A RECONSTRUCTION NEEDS IS NOT WHAT A SEARCH GIVES YOU. Plain "a duck"
    # returns a duck swimming with half of it under water, and a close-up of two
    # webbed feet. TripoSR builds what it can see, so it built a lump.
    query = (f"{description} logo silhouette black on white transparent png"
             if flat else
             f"{description} full body single object on white background 3d render")
    try:
        imgs = await brave_web.images(query, 8 if flat else 6)
    except Exception:
        log.debug("reference image search failed", exc_info=True)
        return ""

    d = work_dir()
    # A PNG WITH ALPHA IS THE BEST POSSIBLE TRACE — `trace_shapes` uses the alpha
    # channel directly and does no guessing at all. Try those first when the
    # picture is going to be traced rather than reconstructed.
    ordered = list(imgs or [])
    if flat:
        ordered.sort(key=lambda im: 0 if ".png" in (im.get("src") or "").lower()
                     else 1)
    # SKIP THE ONES HE HAS ALREADY SEEN. "Find another design" means the same
    # subject from a different picture — the reference IS the design, so a
    # re-roll is simply the next usable candidate down the list.
    if flat:
        # TIER 2 PICKS DIFFERENTLY. A traced logo wants an alpha channel more
        # than it wants pixels, and it takes the first usable one in that order.
        if skip > 0:
            ordered = ordered[skip:] + ordered[:skip]
        for i, img in enumerate(ordered):
            got = await _download_reference(img, description, d)
            if got:
                return got
            log.debug("reference image %d was not usable", i)
        return ""

    # A RECONSTRUCTION CHOOSES. Taking the first that downloaded handed TripoSR
    # a cropped bust of the Mark III when a full-body render was third in the
    # same list of four.
    scored: list = []
    for img in ordered:
        blob = await _fetch_reference(img)
        if not blob:
            continue
        size = _reference_size(blob)
        if not size:
            continue
        w, h = size
        if min(w, h) < MIN_REFERENCE_PX:
            continue
        if max(w, h) / max(1, min(w, h)) > MAX_REFERENCE_ASPECT:
            continue
        whole, single, fill = _framing(blob)
        scored.append((1 if single else 0, 1 if whole else 0, fill,
                       min(w, h), blob))
    if not scored:
        return ""
    # ORDERED, NOT FILTERED. Rejecting outright is how this came back with
    # nothing at all: one subject matters more than perfect framing, and perfect
    # framing matters more than size — but the worst picture in the list still
    # beats no picture, because "there is no limitation to this" means something
    # gets rendered.
    scored.sort(key=lambda t: (-t[0], -t[1], -t[2], -t[3]))
    # "Find another design" walks down the ranking rather than round the list.
    blob = scored[min(skip, len(scored) - 1)][-1]
    ext = ".png" if blob[:4] == b"\x89PNG" else ".jpg"
    p = d / f"{safe_name(description)}-ref{ext}"
    p.write_bytes(blob)
    return str(p)


async def _fetch_reference(img: dict) -> bytes:
    """One candidate picture's bytes, at the best size we can get them."""
    src = (img or {}).get("src") or ""
    if not src.startswith(("http://", "https://")):
        return b""
    # NOT A RESULT — A SITE ICON. DuckDuckGo proxies favicons through `/ip3/`,
    # and one came back 869 x 1017, beat every real photograph on size, and
    # handed the reconstruction TurboSquid's orange SQUID logo. What came out
    # was a tangle of tentacles labelled "iron man mark 3".
    low = src.lower()
    if "/ip3/" in low or low.split("?")[0].endswith(".ico"):
        return b""
    for candidate in _bigger_first(src):
        blob = await _get_image(candidate)
        if blob:
            return blob
    return b""


# THE SEARCH HANDS OVER A 474-PIXEL THUMBNAIL and every reconstruction so far
# was built from one. Behind the proxy is Bing's thumbnail service, which
# resizes on request: the same Mark III at `h=1200` came back a clean 490 x 1200
# full-body cutout. Height only — `w=1200&h=1200` squares the picture off.
_THUMB_HOST = re.compile(r"https?://[^/]*\.bing\.net/th/id/[^?&]+", re.I)


def _bigger_first(src: str) -> list:
    """The same picture at a usable size, then the thumbnail as a fallback."""
    from urllib.parse import unquote
    inner = src
    if "u=" in src:
        try:
            inner = unquote(src.split("u=", 1)[1].split("&f=")[0])
        except Exception:
            inner = src
    got = _THUMB_HOST.match(inner)
    if got:
        return [f"{got.group(0)}?h=1200&rs=1&pid=ImgDetMain", src]
    return [src]


async def _get_image(url: str) -> bytes:
    """One URL's bytes, if they are a picture within sane bounds.

    A CEILING AS WELL AS A FLOOR: this URL came from a web search rather than
    from him, and the only bound on it was once "at least 2 KB".
    """
    import httpx
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True,
                                     max_redirects=3) as c:
            resp = await c.get(url, headers={"User-Agent": "Mozilla/5.0"})
    except Exception:
        log.debug("reference image would not download", exc_info=True)
        return b""
    if resp.status_code != 200:
        return b""
    if not (2048 <= len(resp.content) <= MAX_REFERENCE_BYTES):
        return b""
    head = resp.content[:4]
    if head != b"\x89PNG" and head[:2] != b"\xff\xd8":
        return b""                       # not a PNG or a JPEG; not a picture
    return resp.content


def _framing(blob: bytes):
    """(the whole object is in shot, there is only one of it, how much it fills).

    These pictures are nearly all on plain backgrounds, which makes this cheap:
    read the border colour, mask everything that is not it, and see where the
    subject's bounding box lands.

    THE BOTTOM EDGE DOES NOT COUNT. Measured across eight real candidates for
    "iron man mark 3", every single one touched the bottom — figures stand on
    the ground and objects sit on a surface. Treating that as a crop rejected
    the two best pictures in the set. Left, right and top are the ones that mean
    something has been cut off.

    A busy background gets no verdict rather than a wrong one: `False` there
    would throw away every photograph that was not taken in a studio.
    """
    try:
        from io import BytesIO

        import numpy as np
        from PIL import Image
        with Image.open(BytesIO(blob)) as raw:
            im = raw.convert("RGB")
            im.thumbnail((256, 256))
        a = np.asarray(im, dtype=np.int16)
    except Exception:
        return False, False, 0.0
    h, w = a.shape[:2]
    border = np.concatenate([a[0], a[-1], a[:, 0], a[:, -1]])
    bg = np.median(border, axis=0)
    if float((np.abs(border - bg).max(axis=1) < 26).mean()) < 0.85:
        # A busy background gets no verdict rather than a wrong one, and is
        # not rejected: `False` here would throw away every photograph that was
        # not taken in a studio.
        return False, True, 0.0
    mask = np.abs(a - bg).max(axis=2) > 32
    fill = float(mask.mean())
    if fill < 0.01:
        return False, True, 0.0              # nothing in it
    ys, xs = np.where(mask)
    whole = bool(xs.min() >= 2 and xs.max() <= w - 3 and ys.min() >= 2)

    # ONE OF THE THING, NOT TWO — and this is a REJECTION, not a demotion. A
    # catalogue picture showing the same figure from the front and the back is a
    # perfectly good photograph and a terrible reference: TripoSR built BOTH,
    # and the result was two Iron Men lying side by side. Ranking it down was
    # not enough, because every candidate in that set was cropped somewhere and
    # this one filled the most frame, so it won anyway. A slight crop still
    # yields the object; two objects never do.
    #
    # Two subjects leave a column of pure background between them, and a single
    # object never does — a mug's handle is attached, and the gap between a
    # duck's legs still has duck above it.
    inner = mask[:, xs.min():xs.max() + 1]
    single = bool(inner.shape[1] <= 8 or inner.any(axis=0)[2:-2].all())
    return whole, single, fill


def _reference_size(blob: bytes):
    """(width, height), or None when it will not open."""
    try:
        from io import BytesIO

        from PIL import Image
        with Image.open(BytesIO(blob)) as im:
            return im.size
    except Exception:
        return None


async def _download_reference(img: dict, description: str, d) -> str:
    """Save one candidate if it is a picture at all. Tier 2's path."""
    from tools.fabrication import safe_name
    blob = await _fetch_reference(img)
    if not blob:
        return ""
    ext = ".png" if blob[:4] == b"\x89PNG" else ".jpg"
    p = d / f"{safe_name(description)}-ref{ext}"
    p.write_bytes(blob)
    return str(p)


async def from_text(description: str, name: str = "",
                    skip: int = 0) -> dict:
    """TIER 4: a description to a mesh, by way of a reference picture."""
    from tools.fabrication import safe_name
    if not available()[4]:
        return _missing(4)
    desc = (description or "").strip()
    if not desc:
        return {"error": "what should I make, sir?", "tier": 4}

    ref = await reference_image(desc, skip=skip)
    if not ref:
        return {"error": "I couldn't find a picture to build that from, sir",
                "tier": 4}
    r = await from_photo(ref, name or desc)
    # Tidy the reference away: it was scaffolding, and his work folder is for
    # parts. Only after the mesh is made, so a failure leaves it to look at.
    try:
        if not r.get("error"):
            os.remove(ref)
    except OSError:
        pass
    if r.get("error"):
        return {**r, "tier": 4, "reference": ref}
    return {**r, "tier": 4, "name": safe_name(name or desc),
            "note": TIER_NOTE[4], "reference_used": True, "repair_likely": True}


def spoken_caveats(r: dict) -> str:
    """What else he needs to hear about a finished part, in one sentence or none.

    THREE THINGS, in order of how much they matter:

      * it is somebody else's work, or one part of several, or in units we are
        not sure of — everything tier 5 has to admit, said in the same breath
        as "ready";
      * it did not come out as asked — the loudest, and the one that used to be
        silent;
      * numbers WE chose rather than he did, because a default nobody mentioned
        is a default he discovers at the printer;
      * the mesh needs repairing.

    One place, so the immediate answer and the background announcement cannot
    tell him different things about the same part.
    """
    bits: list[str] = []
    # WHOSE WORK IT IS COMES FIRST, and it comes here rather than only in the
    # result dict, because THIS is the line he actually hears when a render
    # finishes in the background. A downloaded sculpture announced as "ready,
    # sir - 120 by 128 by 158 millimetres" is a stranger's model presented as
    # ours, and he might repeat that to someone. Same reason the piece count is
    # here: "ready" said over a quarter of a helmet is the emblem failure again.
    if r.get("found_not_made"):
        # One sentence, not three joined by semicolons — the caveats about a
        # downloaded model belong together because they are all the same point.
        found = (f"though I found this rather than made it, "
                 f"it's {r.get('credit') or 'someone else'}'s work")
        if r.get("in_pieces"):
            found += f", and it's one of {r.get('part_count')} parts"
        if r.get("coarse"):
            found += ", and it's low-detail — quite faceted"
        bits.append(found)
    if r.get("unit_note"):
        bits.append(str(r["unit_note"]))
    if r.get("fell_back_from") == 5:
        # Said, but not led with, and never as a limitation. "Nobody had one to
        # download so I built it" is a fact about provenance; "that site needs
        # an account" is a wall, and walls are not answers.
        bits.append("nobody had one to download, so I built this one")
    if r.get("spec_problems"):
        bits.append("but " + "; ".join(r["spec_problems"][:2]))
    elif r.get("retried"):
        bits.append("it took a second attempt to get the dimensions right")
    chose = r.get("chose") or {}
    if chose:
        bits.append("I chose " + " and ".join(str(v) for v in list(chose.values())[:2]))
    if r.get("mesh_warning"):
        bits.append(str(r["mesh_warning"]))
    if not bits:
        return ""
    line = "; ".join(bits)
    return line[:1].upper() + line[1:] + "."


async def _measure(stl_path: str) -> list | None:
    """The part's extents, or None if it will not read."""
    try:
        import meshio
        info = await asyncio.to_thread(meshio.describe, str(stl_path))
        return info["size_mm"]
    except Exception:
        log.debug("could not measure for verification", exc_info=True)
        return None


async def _verify_and_retry(r: dict, description: str, name: str) -> dict:
    """Check the part against what he stated, and have one more go if it is wrong.

    Borrowed from TalkCAD, which verifies generated CAD against the spec with a
    tolerance rather than trusting it. The retry only applies to the MODEL — a
    template is deterministic and would produce the identical wrong part again,
    so a template that fails verification is a bug in the template and says so.
    """
    import partspec

    if r.get("error") or not r.get("stl"):
        return r
    spec = partspec.extract(description)
    if not spec:
        return r                       # nothing stated, nothing to check

    size = await _measure(r["stl"])
    check = partspec.verify(spec, size, description)
    r["spec_checked"] = check.get("checked") or []
    if check.get("ok") is not False:
        return r

    if r.get("from") == "template":
        log.warning("a TEMPLATE produced a part that fails its own spec: %s",
                    check["problems"])
        r["spec_problems"] = check["problems"]
        return r

    from tools.fabrication import generate_part
    note = "; ".join(check["problems"])
    log.info("regenerating: %s", note)
    again = await generate_part(description, name, retry_note=note)
    if again.get("error") or not again.get("stl"):
        r["spec_problems"] = check["problems"]
        r["retry_failed"] = True
        return r

    again = {**again, "tier": 1, "note": TIER_NOTE[1], "retried": True}
    again.setdefault("name", name or "")
    size2 = await _measure(again["stl"])
    check2 = partspec.verify(spec, size2, description)
    again["spec_checked"] = check2.get("checked") or []
    if check2.get("ok") is False:
        # Still wrong. He is told, rather than handed it quietly — the second
        # attempt being no better is exactly the moment to stop pretending.
        again["spec_problems"] = check2["problems"]
    return again


def _fallback_tier(description: str) -> int:
    """What tier 5 becomes when the web has nothing.

    NOT a constant. A dragon that cannot be found is still best served by a
    photograph reconstruction; a tape measure that cannot be found is best
    served by research and OpenSCAD, and handing him a reconstructed lump of one
    would be strictly worse than the answer he would have got with tier 5 turned
    off. Missing must cost a few seconds, never a worse object.
    """
    # HIS RULE: "There is no limitation to this." Say "render Iron Man Mark 3"
    # and it renders — "take an image from the web and then create that into
    # 3D". Say "a duck" and a duck appears. Not a page to open, not an
    # approximation written in OpenSCAD, not "that site needs an account".
    #
    # Tier 4 IS that sentence: find a reference picture, reconstruct it into a
    # mesh. It was the fallback only for things `_ORGANIC` recognised, so "iron
    # man mark 3" fell to tier 1 and had OpenSCAD write code for a suit of
    # armour. Everything that reaches tier 5 is a thing in the world with no
    # dimensions in the sentence — a picture is the right source for all of it,
    # because a dimensioned or mechanical part never gets here in the first
    # place.
    if available().get(4):
        return 4
    # Only when the reconstruction is genuinely not installed. A tier that
    # answers "I don't have that installed" is not a fallback.
    return 1


# Words that carry no subject. A file called `model.stl` in a repo called
# `3d-prints` matches every search ever made, so these are removed before
# anything is said to be about what he asked for.
_STOP = frozenset("""
a an the my me of for and or with in on to at from that this these those
please can could you your i'd i'm it its is are was were be been being
make making made create creating render rendering build building show me
model models 3d three print printable printed printing stl file files
new some any thing things object objects version
""".split())


def _subject_words(description: str) -> list[str]:
    """The words a found file has to be about.

    Digits are kept however short they are. "Mark 3" reduced to ["mark"] under
    a three-character minimum, and `timhayduk/IronManMark41` then scored a
    perfect match on it — the 3 is the most identifying part of that request.
    """
    got = re.findall(r"[a-z0-9]+", (description or "").lower())
    return [w for w in got
            if (w.isdigit() or len(w) > 2) and w not in _STOP]


# Words that name SUPPORTING HARDWARE rather than the thing itself. Every props
# repo is full of these and every one of them inherits the repo's name, which is
# how "a d20 dice" came back as a webcam calibration plate and "a mandalorian
# helmet" came back as a keyslot bracket. Subtracted — but only when he did not
# ask for one, because "a phone mount" should match a file called mount.
_SUPPORTING = frozenset("""
plate calibration marker webcam camera test tests sample coupon jig fixture
bracket mount mounting holder stand case enclosure box tray spacer washer shim
adapter adaptor holster assembly clip peg hook cutter keyslot arduino servo
electronics wiring template guide draft draft1 scrap
""".split())

# Words that say a file is a PIECE of the thing rather than the thing. A helmet
# tall enough to wear does not fit on a 250 mm bed, so it is published as
# panels — and the best-matching STL in a helmet repo is therefore usually one
# panel. Penalised rather than rejected, because sometimes panels are all there
# is, and a quarter of a helmet offered as a quarter of a helmet is useful.
_PIECE = frozenset("""
front back left right top bottom upper lower inner outer rear side
half quarter part parts piece pieces section split segment panel
a b c d1 d2 v1 v2 p1 p2 pt1 pt2
""".split())

# How much a match has to be worth before a file is offered as the thing he
# asked for. Three is exactly "the filename says so", or "the repo says so
# three times over" — both real evidence; anything less is a coincidence.
MIN_MATCH = 3

# A SCULPTURE IS NEVER A SMALL FILE. A helmet, a figure, a suit — anything that
# reached tier 5 through `_ORGANIC` — is tens of thousands of triangles because
# somebody sculpted it. The two wrong answers were 135 KB and 141 KB; the right
# ones were 7 MB and 11 MB. Applied only to sculptural requests, since a d20 or
# a bracket can legitimately be tiny.
MIN_SCULPT_BYTES = 300 * 1024


# How much longer than the subject word a filename token may be and still BE
# that word. "benchy" inside "3dbenchy" is two characters of prefix and is the
# same thing; "duck" inside "microduck" is five and is a robot kit — offered,
# live, as a duck. Without this "cat" also matches "catalogue" and
# "concatenate".
MAX_WORD_PADDING = 3


def _names(word: str, tokens) -> bool:
    """Does any token in this filename actually mean `word`?"""
    for t in tokens:
        if t == word:
            return True
        if word in t and len(t) - len(word) <= MAX_WORD_PADDING:
            return True
    return False


def _pick_mesh(meshes: list, description: str):
    """The file that is actually the thing he asked for, and its siblings.

    TWO QUESTIONS, ASKED SEPARATELY, because one score could not answer both.

    IS THE REPO THE OBJECT? Only when its name carries every word of the
    subject. `crashworks3d_arc_reactor` carries "arc" and "reactor" and its six
    files are the six parts of an arc reactor, none of them named for it.
    `D20-IRL-detection` carries "d20" but not "dice" — a project that USES dice,
    whose biggest file is a webcam calibration card. That one test separates
    every repo in the sample.

    WHICH FILE? Inside the object's own repo, anything that is not obviously
    supporting hardware, biggest first, since the big file is the piece and the
    small ones are its fittings. Inside a merely related repo, the filename has
    to name the subject itself.

    Returns (pick, siblings). `pick["is_piece"]` says the object is published in
    parts and this is one of them — which has to be SAID, because a quarter of a
    helmet handed over as a helmet is a confident wrong answer.
    """
    words = _subject_words(description)
    asked = set(words)
    sculpt = bool(_ORGANIC.search(description or ""))
    if not words:
        return (meshes[0] if meshes else None), meshes[1:4]

    repo = (meshes[0].get("repo", "").lower() if meshes else "")
    # EVERY word, not some — "d20" alone matched a dice-detection rig — AND at
    # least two of them, because a one-word subject makes this test trivially
    # true. "A duck" matched `apirrone/Open_Duck_Mini`, a robot, and came back
    # as its bottom case: 102,342 triangles, watertight, 50 mm, and not a duck.
    #
    # Being strict here costs nothing now. Tier 4 reconstructs from a reference
    # picture and is always underneath, so a rejected download means a generated
    # duck rather than no duck.
    repo_is_subject = (bool(repo) and len(words) >= 2
                       and all(w in repo for w in words))

    scored = []
    for m in meshes:
        fname = (m.get("path", "").split("/")[-1]).lower()
        # A trailing version digit still names a piece: `helmet_back1` split to
        # "back1" and matched nothing, so a helmet's back panel was offered as a
        # whole helmet.
        # TWO SETS, deliberately. Name matching needs the token as written —
        # stripping the trailing digits turns "d20" into "d" and the real d20
        # stopped matching. Piece detection needs them stripped, because
        # "helmet_back1" is a back panel and "back1" matches nothing.
        raw_tokens = [t for t in re.split(r"[^a-z0-9]+", fname) if t]
        tokens = {re.sub(r"\d+$", "", t) or t
                  for t in re.split(r"[^a-z0-9]+", fname)}
        supporting = sum(1 for bad in _SUPPORTING
                         if bad not in asked and bad in tokens)
        named = sum((6 if w.isdigit() else 3) for w in words
                    if _names(w, raw_tokens))

        if named - 3 * supporting >= MIN_MATCH:
            score, by_name = named - 3 * supporting, True
        elif repo_is_subject and not supporting:
            score, by_name = MIN_MATCH, False
        else:
            continue
        if sculpt and int(m.get("bytes") or 0) < MIN_SCULPT_BYTES:
            continue
        # A file is a piece if it says so, or if the repo is the object and this
        # is one of several files in it — six parts of an arc reactor name none
        # of themselves, and handing over one is still handing over one.
        piece = any(p not in asked and p in tokens for p in _PIECE) or not by_name
        scored.append((score, int(m.get("bytes") or 0), {**m, "is_piece": piece}))

    if not scored:
        return None, []
    scored.sort(key=lambda t: (-t[0], -t[1]))
    if len(scored) == 1:
        # One file and nothing to be a piece OF.
        only = {**scored[0][2], "is_piece": False}
        return only, []
    return scored[0][2], [t[2] for t in scored[1:]]


# What a printed object plausibly measures across its longest side. Below the
# floor it is jewellery; above the ceiling nothing on a desk prints it. Outside
# that range a fetched file is far more likely to be in inches or centimetres.
_PLAUSIBLE_MM = (8.0, 600.0)

# Thinnest over longest. Under this a mesh is a plate, a panel or a print
# layout rather than an object in the round — and everything that reaches tier 5
# is meant to be in the round, because a flat thing routes to tier 2.
MIN_ROUNDNESS = 0.14

# Under this a fetched model is FACETED, and that is worth saying out loud. The
# chess knight that came back is a real chess knight — recognisable from across
# the room and 45 mm tall — at 206 triangles, so its neck is six flat panels.
# Not a reason to refuse it; a reason not to let him find out at the printer.
COARSE_TRIANGLES = 1200


def _too_flat(size) -> bool:
    """Is this a print plate rather than the object?"""
    try:
        vals = [abs(float(v)) for v in size]
        return bool(vals) and min(vals) < MIN_ROUNDNESS * max(vals)
    except Exception:
        return False


def _unit_doubt(size) -> dict:
    """Say when a downloaded mesh is probably not in millimetres.

    An STL carries no units at all — the numbers in it are numbers. Everything
    downstream believes they are millimetres: the bed check, the wall-thickness
    warning, the sliver guard that rejects anything under half a millimetre. The
    Iron Man gauntlet came back 15 x 30 x 3 and would have been announced as
    three millimetres thick with a straight face.
    """
    try:
        big = max(float(v) for v in size)
    except Exception:
        return {}
    if _PLAUSIBLE_MM[0] <= big <= _PLAUSIBLE_MM[1]:
        return {}
    # Metres are here because robotics and game exports use them, and a duck
    # measuring 0.05 was reported as "0 by 0 by 0 millimetres".
    for name, factor in (("inches", 25.4), ("centimetres", 10.0),
                         ("metres", 1000.0)):
        if _PLAUSIBLE_MM[0] <= big * factor <= _PLAUSIBLE_MM[1]:
            guess, mult = name, factor
            break
    else:
        guess, mult = "", 0.0
    return {"units_uncertain": True, "unit_guess": guess, "unit_scale": mult,
            "unit_note": (f"that file has no units in it and the numbers come "
                          f"out at {big:g} across, which isn't millimetres"
                          + (f" — it's most likely {guess}, so I'd scale it "
                             f"{mult:g} times" if guess else ""))}


async def from_the_web(description: str, name: str = "", skip: int = 0) -> dict:
    """TIER 5: fetch a model somebody already sculpted, and say whose it is.

    Falls back to the tier this request would otherwise have used, so it never
    dead-ends on "the web did not have it" and never hands back something worse
    than tier 5 being absent — but it says which one it ended up doing, because
    a downloaded sculpture and a generated part are very different things to be
    handed.
    """
    import model_find as MF
    from tools.fabrication import safe_name

    desc = (description or "").strip()
    found = await MF.find(desc)
    # "FIND ANOTHER DESIGN" HAS TO ACTUALLY FIND ANOTHER ONE. `skip` reached
    # every other tier and was dropped here, so asking again returned the same
    # file from the same repo and looked like the request had been ignored.
    # Counted over usable candidates rather than over search results, so one
    # "another" moves one real model rather than skipping a dead repo.
    passed = 0
    for c in found.get("candidates") or []:
        if "github.com" not in c.get("host", ""):
            continue
        meshes = await MF.github_meshes(c["url"])
        pick, others = _pick_mesh(meshes, desc)
        if not pick:
            continue
        if passed < skip:
            passed += 1
            continue
        got = await MF.fetch(pick["url"], name=safe_name(name or desc))
        if got.get("error"):
            log.info("candidate %s: %s", pick.get("repo"), got["error"])
            continue
        if _too_flat(got.get("size_mm") or []) and not _FLAT.search(desc):
            # "Iron Man Mark 3" fetched a 395,000-triangle forearm shell laid
            # flat on a print bed, and every number about it said success.
            log.info("candidate %s is a print plate (%s), not the object",
                     pick.get("repo"), got.get("size_mm"))
            try:
                os.remove(got["stl"])
            except OSError:
                pass
            continue
        found_as = f"{pick['path'].split('/')[-1]} from {pick['repo']}"
        siblings = [o for o in others if o.get("is_piece")]
        in_pieces = bool(pick.get("is_piece") and siblings)
        if in_pieces:
            found_as += f" — one of {len(siblings) + 1} parts"
        return {**got, "tier": 5, "note": TIER_NOTE[5],
                "credit": pick["repo"], "found_not_made": True,
                "source_page": c.get("url", ""),
                "found_as": found_as,
                "in_pieces": in_pieces,
                "coarse": int(got.get("triangles") or 0) < COARSE_TRIANGLES,
                "part_count": len(siblings) + 1 if in_pieces else 1,
                **_unit_doubt(got.get("size_mm") or []),
                "alternatives": [{"path": o["path"], "url": o["url"],
                                  "bytes": o["bytes"]} for o in others[:6]],
                # SAY WHAT ARRIVED, not what was asked for. A search for the
                # Mark 3 can legitimately land on somebody's Mark 41, and being
                # handed that silently is worse than being told and offered
                # another — "find another design" already works.
                "instruction": (
                    f"This was FOUND, not made: {found_as}. Name it, credit "
                    f"{pick['repo']}, and say it can be swapped if it is not "
                    f"the right one."
                    + (f" It is published in {len(siblings) + 1} parts and this "
                       f"is one of them — say so and offer to fetch the rest."
                       if in_pieces else "")
                    + (f" It is only {got.get('triangles')} triangles, so it is "
                       f"visibly faceted — mention that."
                       if int(got.get("triangles") or 0) < COARSE_TRIANGLES
                       else "")),
                "name": got.get("name") or safe_name(name or desc)}

    # NOTHING COULD BE DOWNLOADED — but that is not the same as nothing existing.
    # Printables and Cults3D carry real sculpted models of exactly these
    # subjects and both put the file behind an account, which was measured. He
    # should be told they are there rather than quietly handed a generated
    # approximation of a suit of armour.
    pages = [{"title": c["title"], "url": c["url"], "host": c["host"]}
             for c in (found.get("candidates") or [])
             if c.get("host") and "github" not in c["host"]][:3]

    back = _fallback_tier(desc)
    log.info("no fetchable model for %r; falling back to tier %d", desc[:40], back)
    r = await build(back, description=desc, name=name, skip=skip)
    extra = {"fell_back_from": 5, "pages_found": pages}
    if pages:
        # AN ASIDE, NOT THE ANSWER. This used to lead with "they need an
        # account", which made a locked website the outcome of a request to
        # render something. He was blunt about it: "don't worry about an
        # account. Find an alternative to that. There is no limitation." The
        # alternative is the reconstruction that just ran; the hand-sculpted one
        # is a nicer thing he can go and get if he wants it.
        extra["instruction"] = (
            f"This was BUILT from a reference picture, not downloaded — say so "
            f"in passing. Do NOT lead with where it came from, and do NOT say "
            f"anything needs an account. If he asks for better, "
            f"{sorted({p['host'] for p in pages})[0]} has hand-sculpted ones.")
    if r.get("error"):
        return {**r, **extra}
    return {**r, **extra}


async def build(tier: int, description: str = "", image_path: str = "",
                name: str = "", skip: int = 0) -> dict:
    """Run one tier and return its result, tier included.

    Every generated mesh is checked before it is handed on. Non-manifold
    geometry is the commonest reason a slicer refuses a file and AI-generated
    meshes are specifically prone to it — which is tiers 3 and 4 exactly — so
    what came out is reported rather than assumed.
    """
    tier = int(tier)
    if tier in (0, 1):
        from tools.fabrication import generate_part
        # LOOK IT UP FIRST, when it is a thing in the world rather than a shape
        # with dimensions in the sentence. "Create me a Nintendo 2DS XL" is not
        # something anyone can model from memory, and "a baseball" has a real
        # diameter. A template needs no research — it IS the answer — and a part
        # he has already dimensioned needs none either.
        brief = ""
        srcs: list = []
        if tier == 1 and description and not _DIMENSIONED.search(description):
            import parts_library
            if not parts_library.match(description):
                try:
                    import research_build
                    got = await research_build.brief_for(description)
                    brief, srcs = got.get("brief", ""), got.get("sources", [])
                    if brief:
                        log.info("researched %r: %s", description[:40],
                                 " ".join(brief[:120].split()))
                except Exception:
                    log.debug("research failed; building from memory",
                              exc_info=True)
        r = await generate_part(description, name, brief=brief)
        if brief:
            r.setdefault("researched", True)
            r.setdefault("sources", srcs)
        # Which of the two actually produced it is decided inside generate_part
        # — it takes the template whenever it can — so the tier is corrected here
        # from what came back rather than from what was predicted.
        actual = 0 if r.get("from") == "template" else 1
        r = {**r, "tier": actual, "note": TIER_NOTE[actual]}
        r.setdefault("name", name or "")

        # DOES IT MATCH WHAT HE ASKED FOR? Nothing checked this before, which is
        # how "a hex spacer 12 mm tall" came back 0.4 mm wide and was announced
        # as ready. One retry with the failure fed back — asking the same
        # question twice and hoping is not a retry.
        r = await _verify_and_retry(r, description, name)
    elif tier == 2:
        # NO PICTURE? FIND ONE. "Create me a 3D image of the Spider-Man emblem"
        # names a flat thing and hands over nothing to trace, and tier 2 without
        # an image used to be an error. Fetching the reference here is the same
        # move tier 4 makes — the difference is what happens to it afterwards:
        # traced and extruded rather than reconstructed.
        ref_fetched = ""
        if not image_path and description:
            image_path = ref_fetched = await reference_image(
                description, flat=True, skip=skip)
            if not image_path:
                return {"error": "I couldn't find a picture of that to trace, sir",
                        "tier": 2}
        if not image_path:
            return {"error": "I need a picture to work from, sir", "tier": 2}
        # Which KIND of tier 2: an extruded outline for a logo, a relief for a
        # photograph. Decided from his words, and reported either way.
        outline = (note_for(2, description) == TIER_NOTE[2])
        r = await (from_image(image_path, name) if outline
                   else from_relief(image_path, name))
        if ref_fetched:
            r.setdefault("reference_used", True)
            # Scaffolding, not a part. Kept on failure so it can be looked at.
            try:
                if not r.get("error"):
                    os.remove(ref_fetched)
            except OSError:
                pass
    elif tier == 3:
        r = await from_photo(image_path, name)
    elif tier == 4:
        r = await from_text(description, name, skip=skip)
    elif tier == 5:
        r = await from_the_web(description, name, skip=skip)
    elif tier == 7:
        import composite
        r = await composite.build(description, name)
        if r.get("not_composite"):
            r = await build(choose_tier(description, image_path), description,
                            image_path, name, skip=skip)
    elif tier == 6:
        # TAKE THE REQUEST APART, NOT THE MESH. A suit cannot be reconstructed
        # from one photograph and OpenSCAD cannot sculpt armour — but a helmet
        # can be found, and a gauntlet can be reconstructed from a picture of a
        # gauntlet. See `components` for why the placement is arithmetic.
        import components
        r = await components.from_components(description, name)
        if r.get("no_components") or (r.get("error") and not r.get("stl")):
            # Not made of anything nameable after all, so make the thing itself
            # rather than refusing him over a decomposition he never asked for.
            log.info("%r did not come apart; building it whole", description[:40])
            r = await build(choose_tier(description, image_path), description,
                            image_path, name, skip=skip)
    else:
        return {"error": f"I don't have a way to make that (tier {tier})"}

    if r.get("error") or not r.get("stl"):
        return r
    try:
        import meshio
        import printcheck
        info = await asyncio.to_thread(meshio.describe, str(r["stl"]))
        w, h, d = info["size_mm"]
        r["size_mm"] = info["size_mm"]
        r["spoken_size"] = f"{round(w)} by {round(h)} by {round(d)} millimetres"
        tris = await asyncio.to_thread(meshio.load, str(r["stl"]))
        integ = await asyncio.to_thread(printcheck.integrity, tris)
        r["sliceable"] = integ.get("sliceable")
        if integ.get("sliceable") is False:
            r["mesh_warning"] = ("it isn't watertight, so it'll need repairing "
                                 "before it prints")
        # A part that came out as a SLIVER is a failed generation, not a part.
        # Asked for "a hex spacer 12 mm tall" the local model once produced
        # something 0.4 mm wide; the pipeline dutifully measured it, projected it
        # and reported success, and the only clue was a dimension rounding to
        # zero in the HUD. Nothing under half a millimetre in its smallest
        # dimension is a thing he asked for, and nothing can print it.
        if min(info["size_mm"]) < MIN_SENSIBLE_MM:
            r["mesh_warning"] = (f"that came out {r['spoken_size']}, which isn't "
                                 f"right — worth asking me again")
            r["degenerate"] = True
    except Exception:
        log.debug("could not measure the new mesh", exc_info=True)
    return r
