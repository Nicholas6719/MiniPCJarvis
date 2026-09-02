"""The toolpath, read back out of the G-code the slicer actually wrote.

The layer preview in the hologram is not a simulation of what the printer might
do. It is the file the printer will be handed, parsed. That distinction is the
whole point: a simulated preview agrees with itself and tells him nothing, while
this disagrees with his expectations exactly where the slicer did something he
did not expect — which is the only time a preview earns its keep.

WHAT THIS HANDLES, because G-code is a dialect rather than a format:

  * ABSOLUTE AND RELATIVE positioning (G90/G91) and, separately, absolute and
    relative extrusion (M82/M83). PrusaSlicer writes M82; Cura writes M83. Read
    an M83 file as though it were M82 and every single move looks like a
    retraction, so nothing is drawn at all.
  * G92, which sets the position without moving — the extruder-reset that appears
    between layers and would otherwise read as one enormous negative extrusion.
  * LAYER BOUNDARIES from `;LAYER_CHANGE` (PrusaSlicer) or `;LAYER:` (Cura), and
    from a plain Z rise when the file has no comments at all. Never from Z alone
    when markers exist: a Z-hop during travel is not a new layer.
  * TRAVEL vs EXTRUSION. Only moves that push filament become geometry; travel
    breaks the polyline. Drawing travels would fill the preview with the diagonal
    hatch that makes a print look like a scribble.

WHAT IT DOES NOT DO: arcs (G2/G3). PrusaSlicer emits them only when
`arc_fitting` is enabled, which the bundled profile does not set. They are
counted and reported rather than silently dropped, so a file full of them says
so instead of appearing half empty.
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger("jarvis.gcode")

# A whole print is millions of points and the HUD is a 1920-wide window. These
# are what get sent over the wire, not what gets parsed — parsing is always
# complete, so the counts and heights are true even when the geometry is thinned.
# 250 mm of Z at a 0.0625 mm layer is 4,000 layers, which is past anything this
# printer can make. The real memory bound is MAX_POINTS; a layer costs almost
# nothing on its own, so capping layers tightly bought nothing and cost accuracy.
MAX_LAYERS = 4_000
MAX_POINTS = 200_000

_WORD = re.compile(r"([XYZEF])(-?\d*\.?\d+)")
_LAYER_MARK = (";LAYER_CHANGE", ";LAYER:")
_TYPE = re.compile(r";\s*TYPE:\s*(.+?)\s*$", re.I)
# The leading command, whether or not a space follows it. Splitting on whitespace
# instead read `G1X10Y0` — which some slicers and most post-processors emit, since
# the space is optional in the spec — as a command named "G1X10Y0", and every
# move in such a file was skipped in silence.
_HEAD = re.compile(r"^([GM]\d+)")


def _words(line: str) -> dict:
    return {m.group(1): float(m.group(2)) for m in _WORD.finditer(line)}


def parse(path, max_layers: int = MAX_LAYERS, max_points: int = MAX_POINTS) -> dict:
    """Read a G-code file into per-layer extrusion polylines.

    Returns z heights, a segment count, and flat [x,y,x,y,...] point lists per
    layer, ready to hand to the renderer without further work in JS.
    """
    x = y = z = 0.0
    e = 0.0
    abs_xyz = True          # G90 is the near-universal default and what every
    abs_e = True            # slicer emits explicitly anyway
    layers: list[dict] = []
    cur: dict | None = None
    poly: list[float] = []
    arcs = 0
    pts = 0
    seen_layers = 0
    seen_marker = False
    truncated = False

    def close_poly():
        nonlocal poly
        if cur is not None and len(poly) >= 4:
            cur["paths"].append(poly)
        poly = []

    def start_layer(zz: float):
        nonlocal cur, truncated
        close_poly()
        cur = {"z": round(zz, 3), "paths": [], "types": set(), "n": 0}
        if len(layers) >= max_layers:
            # Past the cap, keep COUNTING but stop keeping. Returning early left
            # `cur` pointing at the last retained layer, so everything printed
            # above the cap was silently welded onto it — a preview that was
            # wrong rather than merely incomplete.
            truncated = True
            return
        layers.append(cur)

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                line = raw.strip()
                if not line:
                    continue
                if line[0] == ";":
                    up = line.upper()
                    if any(up.startswith(m) for m in _LAYER_MARK):
                        seen_marker = True
                        start_layer(z)
                    else:
                        m = _TYPE.match(line)
                        if m and cur is not None:
                            cur["types"].add(m.group(1))
                    continue

                # Strip a trailing comment; PrusaSlicer annotates half its lines.
                if ";" in line:
                    line = line.split(";", 1)[0].strip()
                    if not line:
                        continue
                m = _HEAD.match(line.upper())
                if not m:
                    continue
                head = m.group(1)

                if head == "G90":
                    abs_xyz = True
                    continue
                if head == "G91":
                    abs_xyz = False
                    continue
                if head == "M82":
                    abs_e = True
                    continue
                if head == "M83":
                    abs_e = False
                    continue
                if head == "G92":
                    w = _words(line)
                    # Sets position, moves nothing. The E reset lives here.
                    if "X" in w: x = w["X"]
                    if "Y" in w: y = w["Y"]
                    if "Z" in w: z = w["Z"]
                    if "E" in w: e = w["E"]
                    continue
                if head in ("G2", "G3"):
                    arcs += 1
                    close_poly()
                    continue
                if head not in ("G0", "G1"):
                    continue

                w = _words(line)
                nx = (w.get("X", x) if abs_xyz else x + w.get("X", 0.0))
                ny = (w.get("Y", y) if abs_xyz else y + w.get("Y", 0.0))
                nz = (w.get("Z", z) if abs_xyz else z + w.get("Z", 0.0))
                if "E" in w:
                    de = (w["E"] - e) if abs_e else w["E"]
                    ne = w["E"] if abs_e else e + w["E"]
                else:
                    de, ne = 0.0, e

                # No markers anywhere? Then a Z RISE on its own starts a layer.
                # Only for marker-less files: a Z-hop mid-travel is not a layer,
                # and treating it as one shreds a PrusaSlicer file into hundreds
                # of one-path fragments.
                #
                # And only once the CURRENT layer has actually printed something.
                # PrusaSlicer's start G-code contains `G1 Z5 ; lift nozzle`, which
                # arrives before the first `;LAYER_CHANGE` — so with no such
                # guard every real file began with a phantom layer at Z=5 that had
                # extruded nothing. It was filtered out of the result, so it was
                # invisible until a layer cap made the preview one layer short.
                # A marker-less file's first layer starts at its first EXTRUSION,
                # which the extruding branch below already handles.
                if not seen_marker and nz > z + 1e-6 and cur is not None and cur["n"]:
                    start_layer(nz)

                extruding = de > 1e-9 and (abs(nx - x) > 1e-9 or abs(ny - y) > 1e-9)
                if extruding:
                    if cur is None:
                        start_layer(nz)
                    if cur["n"] == 0:
                        # Counted when a layer first EXTRUDES, not when a marker
                        # is seen: a trailing `;LAYER_CHANGE` with nothing after
                        # it is not a layer, and counting it would report one
                        # more than the printer will lay down.
                        seen_layers += 1
                        # The layer's true height is the Z of its FIRST extruding
                        # move, never the Z in force when the marker was read:
                        # PrusaSlicer writes `;LAYER_CHANGE` BEFORE the `;Z:` and
                        # before the move that gets there, so trusting the marker
                        # stamped layer one at 0.0 and shifted every layer down by
                        # one. True in every dialect, marker or no marker.
                        cur["z"] = round(nz, 3)
                    cur["n"] += 1
                    if pts >= max_points:
                        truncated = True
                    else:
                        if not poly:
                            poly.extend((round(x, 3), round(y, 3)))
                            pts += 1
                        poly.extend((round(nx, 3), round(ny, 3)))
                        pts += 1
                else:
                    close_poly()

                x, y, z, e = nx, ny, nz, ne
        close_poly()
    except OSError as ex:
        return {"error": f"could not read the G-code ({ex})"}

    out = []
    for L in layers:
        # Keep a layer that EXTRUDED, not one that retained geometry. Filtering
        # on `paths` made summary() — which asks for no geometry at all — report
        # zero layers for a file with a hundred.
        if not L["n"]:
            continue
        out.append({"z": L["z"], "paths": L["paths"],
                    "types": sorted(L["types"])})
    heights = [L["z"] for L in out]
    return {
        "layers": out,
        "count": seen_layers or len(out),
        "shown": len(out),
        "z_max": round(max(heights), 3) if heights else 0.0,
        "layer_height": round(heights[1] - heights[0], 3) if len(heights) > 1 else None,
        "points": pts,
        "arcs_skipped": arcs,
        "truncated": truncated,
    }


def summary(path) -> dict:
    """Layer count and heights without the geometry — for a spoken answer."""
    r = parse(path, max_points=0)
    if "error" in r:
        return r
    return {k: r[k] for k in ("count", "z_max", "layer_height", "arcs_skipped")}
