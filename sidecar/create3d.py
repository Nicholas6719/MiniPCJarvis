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

TIERS = (0, 1, 2, 3, 4)

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
    return 4


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
def _outline_scad(points: list[list[float]], height_mm: float, width_mm: float) -> str:
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


def _shapes_scad(shapes: list[dict], height_mm: float, width_mm: float) -> str:
    """Extrude every traced shape, with its holes cut out of it."""
    body = []
    for sh in shapes:
        pts = list(sh["outline"])
        paths = [list(range(len(pts)))]
        for hole in sh["holes"]:
            start = len(pts)
            pts.extend(hole)
            paths.append(list(range(start, len(pts))))
        pt_s = ", ".join(f"[{x:.3f},{y:.3f}]" for x, y in pts)
        pa_s = ", ".join("[" + ",".join(str(i) for i in path) + "]"
                         for path in paths)
        body.append(f"    polygon(points = [{pt_s}], paths = [{pa_s}]);")
    joined = "\n".join(body)
    return ("$fn = 48;\n"
            f"// traced from an image, extruded {height_mm:g} mm\n"
            f"linear_extrude(height = {height_mm:g})\n"
            f"  resize([{width_mm:g}, 0, 0], auto = true)\n"
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
    rc, out, err = await _run([exe, "-o", str(stl), str(scad)], 180)
    if rc != 0 or not stl.exists():
        return {"error": f"OpenSCAD could not build that: {(err or out or '').strip()[:200]}",
                "tier": 2}
    return {"tier": 2, "name": base, "scad": str(scad), "stl": str(stl),
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
    r = await _run_model3d("photo_to_mesh.py", [str(p), str(stl)], 900)
    if r.get("error"):
        return {**r, "tier": 3}
    return {"tier": 3, "name": base, "stl": str(stl), "note": TIER_NOTE[3],
            "repair_likely": True}


async def reference_image(description: str, flat: bool = False) -> str:
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
    query = f"{description} logo silhouette black on white transparent png"         if flat else description
    try:
        imgs = await brave_web.images(query, 8 if flat else 4)
    except Exception:
        log.debug("reference image search failed", exc_info=True)
        return ""

    import httpx
    d = work_dir()
    # A PNG WITH ALPHA IS THE BEST POSSIBLE TRACE — `trace_shapes` uses the alpha
    # channel directly and does no guessing at all. Try those first when the
    # picture is going to be traced rather than reconstructed.
    ordered = list(imgs or [])
    if flat:
        ordered.sort(key=lambda im: 0 if ".png" in (im.get("src") or "").lower()
                     else 1)
    for i, img in enumerate(ordered):
        src = (img or {}).get("src") or ""
        if not src.startswith(("http://", "https://")):
            continue
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True,
                                         max_redirects=3) as c:
                resp = await c.get(src)
            # A CEILING AS WELL AS A FLOOR. This URL came from a web search, not
            # from him, and the only bound on it was "at least 2 KB" — a
            # hundred-megabyte image would have been pulled into memory whole.
            # 24 MB is far more than any photograph worth reconstructing.
            if (resp.status_code != 200
                    or not (2048 <= len(resp.content) <= MAX_REFERENCE_BYTES)):
                continue
            head = resp.content[:4]
            if head != b"\x89PNG" and head[:2] != b"\xff\xd8":
                continue                      # not a PNG or a JPEG; not a picture
            ext = ".png" if head == b"\x89PNG" else ".jpg"
            p = d / f"{safe_name(description)}-ref{ext}"
            p.write_bytes(resp.content)
            return str(p)
        except Exception:
            log.debug("reference image %d would not download", i, exc_info=True)
    return ""


async def from_text(description: str, name: str = "") -> dict:
    """TIER 4: a description to a mesh, by way of a reference picture."""
    from tools.fabrication import safe_name
    if not available()[4]:
        return _missing(4)
    desc = (description or "").strip()
    if not desc:
        return {"error": "what should I make, sir?", "tier": 4}

    ref = await reference_image(desc)
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

      * it did not come out as asked — the loudest, and the one that used to be
        silent;
      * numbers WE chose rather than he did, because a default nobody mentioned
        is a default he discovers at the printer;
      * the mesh needs repairing.

    One place, so the immediate answer and the background announcement cannot
    tell him different things about the same part.
    """
    bits: list[str] = []
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


async def build(tier: int, description: str = "", image_path: str = "",
                name: str = "") -> dict:
    """Run one tier and return its result, tier included.

    Every generated mesh is checked before it is handed on. Non-manifold
    geometry is the commonest reason a slicer refuses a file and AI-generated
    meshes are specifically prone to it — which is tiers 3 and 4 exactly — so
    what came out is reported rather than assumed.
    """
    tier = int(tier)
    if tier in (0, 1):
        from tools.fabrication import generate_part
        r = await generate_part(description, name)
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
            image_path = ref_fetched = await reference_image(description, flat=True)
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
        r = await from_text(description, name)
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
        tris = await asyncio.to_thread(meshio.load_stl, str(r["stl"]))
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
