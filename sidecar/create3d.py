"""Four ways to turn something into a mesh, and honesty about which one ran.

    tier  technique                        typical  editable        printable
    ----  -------------------------------  -------  --------------  -----------------
    1     the model writes OpenSCAD        ~4 s     yes, by voice   yes, exactly
    2     an image traced and extruded     ~6 s     thickness/scale yes, sharp
    3     a photo to a mesh (TripoSR)      ~60 s    no              a likeness
    4     text to a mesh (Shap-E)          1-3 min  no              rarely, unrepaired

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
import re
import shutil
from pathlib import Path

from config import config

log = logging.getLogger("jarvis.create3d")

TIERS = (1, 2, 3, 4)

# Below this in its SMALLEST dimension, a generated part is a sliver rather than
# a part: no printer can lay it down and nobody asked for it. Half a millimetre
# is under the 0.8 mm minimum wall by enough that a legitimately thin plate is
# never caught by it.
MIN_SENSIBLE_MM = 0.5

TIER_NOTE = {
    1: "written as OpenSCAD, so it's exact and you can change it by voice",
    2: "traced from the picture and extruded, so the outline is sharp",
    3: "a mesh built from the photo — a likeness rather than a measured part",
    4: "generated from the description — expect it to be rough",
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
        return 2 if _FLAT.search(desc) else 3
    if _MECHANICAL.search(desc) or _DIMENSIONED.search(desc):
        return 1
    return 4


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

    pts = await asyncio.to_thread(trace_outline, str(p))
    if not pts or len(pts) < 3:
        return {"error": "I couldn't find a clear outline in that picture, sir",
                "tier": 2}

    base = safe_name(name or p.stem)
    d = work_dir()
    scad, stl = d / f"{base}.scad", d / f"{base}.stl"
    scad.write_text(_outline_scad(pts, thickness_mm, width_mm), encoding="utf-8")
    rc, out, err = await _run([exe, "-o", str(stl), str(scad)], 180)
    if rc != 0 or not stl.exists():
        return {"error": f"OpenSCAD could not build that: {(err or out or '').strip()[:200]}",
                "tier": 2}
    return {"tier": 2, "name": base, "scad": str(scad), "stl": str(stl),
            "points": len(pts), "note": TIER_NOTE[2]}


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
    """Which of the heavy tiers can actually run right now."""
    py = model3d_python()
    d = model3d_dir()
    return {
        "python": py,
        "dir": str(d),
        3: bool(py and (d / "photo_to_mesh.py").exists()),
        4: bool(py and (d / "text_to_mesh.py").exists()),
    }


def _missing(tier: int) -> dict:
    """The honest sentence when the install is not there.

    It does NOT fall back to another tier. Handing him a tier-1 approximation of
    something he asked for as a photo scan would look like success and be wrong,
    and he would only find out when the shape was not the thing in the photo.
    """
    what = "photo-to-mesh" if tier == 3 else "text-to-mesh"
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


async def from_text(description: str, name: str = "") -> dict:
    """TIER 4: text to a mesh. The slow one, and the roughest."""
    from tools.fabrication import safe_name, work_dir
    if not available()[4]:
        return _missing(4)
    desc = (description or "").strip()
    if not desc:
        return {"error": "what should I make, sir?", "tier": 4}
    base = safe_name(name or desc)
    stl = work_dir() / f"{base}.stl"
    r = await _run_model3d("text_to_mesh.py", [desc, str(stl)], 1800)
    if r.get("error"):
        return {**r, "tier": 4}
    return {"tier": 4, "name": base, "stl": str(stl), "note": TIER_NOTE[4],
            "repair_likely": True}


async def build(tier: int, description: str = "", image_path: str = "",
                name: str = "") -> dict:
    """Run one tier and return its result, tier included.

    Every generated mesh is checked before it is handed on. Non-manifold
    geometry is the commonest reason a slicer refuses a file and AI-generated
    meshes are specifically prone to it — which is tiers 3 and 4 exactly — so
    what came out is reported rather than assumed.
    """
    tier = int(tier)
    if tier == 1:
        from tools.fabrication import generate_part
        r = await generate_part(description, name)
        r = {**r, "tier": 1, "note": TIER_NOTE[1]}
        r.setdefault("name", name or "")
    elif tier == 2:
        r = await from_image(image_path, name)
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
