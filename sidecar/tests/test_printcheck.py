"""Phase B: the print checks, against meshes whose right answer is known.

Every mesh here is BUILT rather than loaded, so the expected answer is arithmetic
rather than a value copied out of a previous run. A test that asserts whatever the
code did last time cannot fail when the code becomes wrong.

  * OVERHANGS at exactly 30°, 45°, 60° and 90°, because the boundary is where a
    threshold is either right or off by one. 45° must NOT be flagged — it is the
    limit, and a chamfer cut deliberately to the limit is the commonest thing in
    a printable part.
  * DELIBERATELY BROKEN MESHES: a hole, a flipped normal, a zero-area face.
    Non-manifold geometry is the commonest reason a slicer refuses a file, so
    each failure mode gets its own mesh rather than one mesh broken four ways.
  * G-CODE IN THREE DIALECTS. The M83 case is the one that matters: read a Cura
    file as though extrusion were absolute and every move looks like a
    retraction, so the preview comes back empty rather than wrong — a silent
    failure, and the worst kind.

Run: python tests/test_printcheck.py
"""
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "pcheck.db"))

import numpy as np  # noqa: E402

import gcode  # noqa: E402
import printcheck as pc  # noqa: E402

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


# --------------------------------------------------------------- mesh builders
def box(sx=20.0, sy=20.0, sz=20.0, z0=0.0) -> np.ndarray:
    """A closed box with every normal facing outward. 12 triangles."""
    a, b, c, d = (0, 0, z0), (sx, 0, z0), (sx, sy, z0), (0, sy, z0)
    e, f, g, h = (0, 0, z0 + sz), (sx, 0, z0 + sz), (sx, sy, z0 + sz), (0, sy, z0 + sz)
    tris = [
        (a, c, b), (a, d, c),        # bottom, -z
        (e, f, g), (e, g, h),        # top, +z
        (a, b, f), (a, f, e),        # front, -y
        (d, g, c), (d, h, g),        # back, +y
        (a, e, h), (a, h, d),        # left, -x
        (b, c, g), (b, g, f),        # right, +x
    ]
    return np.array(tris, dtype=np.float64)


def sloped(theta_deg: float, L: float = 10.0, z0: float = 5.0) -> np.ndarray:
    """One downward face whose angle from vertical is exactly theta.

    Normal works out as (cos t, 0, -sin t), so asin(-nz) is t by construction —
    the same arithmetic the checker does, arrived at from the other direction.
    """
    t = math.radians(theta_deg)
    p = np.array([0.0, 0.0, z0])
    u = np.array([0.0, L, 0.0])
    v = np.array([L * math.sin(t), 0.0, L * math.cos(t)])
    return np.array([[p, p + u, p + v]], dtype=np.float64)


def main() -> int:
    # ------------------------------------------------------------- overhangs
    bed = box(5, 5, 1)                       # something at z=0 so the ramp is not "on the bed"
    for deg, want in ((30.0, False), (45.0, False), (60.0, True), (89.0, True)):
        tris = np.concatenate([bed, sloped(deg, z0=5.0)])
        o = pc.overhangs(tris)
        check(f"a {deg:.0f}deg face is {'flagged' if want else 'accepted'}",
              (o["faces"] == 1) == want, o)
        if want:
            check(f"...and its angle is reported as {deg:.0f}",
                  abs(o["worst_deg"] - deg) < 0.15, o["worst_deg"])

    cube = box(20, 20, 20)
    o = pc.overhangs(cube)
    check("a plain cube has no overhangs", o["faces"] == 0, o)
    check("...specifically, its bed face is not one",
          o["faces"] == 0 and o["area_mm2"] == 0.0, o)

    # A flat ceiling ABOVE something else is the worst case and must be caught;
    # the identical face lying ON the bed must not be.
    ceiling = np.array([[(0, 0, 10.0), (0, 10, 10.0), (10, 0, 10.0)]], dtype=np.float64)
    n = pc.face_normals(ceiling)[0]
    check("the test ceiling really does face straight down", n[2] < -0.99, n)
    o = pc.overhangs(np.concatenate([bed, ceiling]))
    check("a flat ceiling is a 90 degree overhang",
          o["faces"] == 1 and abs(o["worst_deg"] - 90.0) < 0.01, o)
    o = pc.overhangs(ceiling)                 # alone, it IS the bed
    check("...but the same face lying on the bed is not", o["faces"] == 0, o)
    check("overhang positions come back as flat xyz floats",
          len(pc.overhangs(np.concatenate([bed, ceiling]))["positions"]) == 9)

    # ------------------------------------------------------------- bed fitting
    check("a 20 mm cube fits the bed", pc.bed_fit([20, 20, 20])["fits"])
    check("a 300 mm part does not", not pc.bed_fit([300, 50, 50])["fits"])
    check("...and it says by how much",
          pc.bed_fit([300, 50, 50])["over_by_mm"] == 80.0,
          pc.bed_fit([300, 50, 50]))
    check("height is not counted against the bed footprint",
          pc.bed_fit([50, 50, 400])["fits"],
          "a tall part fits a bed it is taller than; only x by y matters")
    check("...but being too tall for the printer is caught separately",
          pc.bed_fit([50, 50, 400])["too_tall"] is True, pc.bed_fit([50, 50, 400]))
    check("a part that would fit turned says so",
          pc.bed_fit([300, 50, 50])["fits_if_rotated"] is True, pc.bed_fit([300, 50, 50]))
    check("...and one that would not, does not",
          pc.bed_fit([300, 300, 300])["fits_if_rotated"] is False)

    # -------------------------------------------------------- wall estimation
    w = pc.thinnest_wall(cube)
    check("a 20 mm cube measures 20 mm through",
          abs(w["estimate_mm"] - 20.0) < 0.05, w)
    check("...and is not called thin", not w["below_minimum"] and not w["below_functional"])
    thin = box(40, 30, 0.6)
    w = pc.thinnest_wall(thin)
    check("a 0.6 mm plate is caught", abs(w["estimate_mm"] - 0.6) < 0.05, w)
    check("...and flagged under the nozzle minimum", w["below_minimum"] is True, w)
    mid = pc.thinnest_wall(box(40, 30, 1.2))
    check("a 1.2 mm wall prints but is flagged as not load-bearing",
          mid["below_minimum"] is False and mid["below_functional"] is True, mid)
    check("the estimate says it is an estimate", "sampled" in w["why"], w)
    check("empty geometry does not raise",
          pc.thinnest_wall(np.zeros((0, 3, 3)))["estimate_mm"] is None)

    # ----------------------------------------------------- deliberately broken
    good = pc.integrity(cube)
    check("a closed box is watertight", good["watertight"] is True, good)
    check("...and sliceable", good["sliceable"] is True, good)
    check("...and its volume is exactly 8000 cubic mm",
          abs(good["volume_mm3"] - 8000.0) < 0.5, good)
    check("...with no degenerate faces", good["degenerate_faces"] == 0, good)

    holed = cube[1:]                                   # one face removed
    bad = pc.integrity(holed)
    check("a mesh with a hole is not watertight", bad["watertight"] is False, bad)
    check("...and is refused", bad["sliceable"] is False, bad)
    check("...and the open edges are counted", bad["open_edges"] == 3, bad)

    flipped = cube.copy()
    flipped[0] = flipped[0][::-1]                      # one triangle wound backwards
    bad = pc.integrity(flipped)
    check("a flipped normal is caught", bad["winding_consistent"] is False, bad)
    check("...and is refused", bad["sliceable"] is False, bad)

    degen = np.concatenate([cube, np.array(
        [[(0, 0, 0), (1, 0, 0), (1, 0, 0)]], dtype=np.float64)])
    bad = pc.integrity(degen)
    check("a zero-area face is counted", bad["degenerate_faces"] == 1, bad)
    check("...counted from HIS file, not from a cleaned copy",
          pc.integrity(degen)["degenerate_faces"] == 1,
          "trimesh drops them on load; counting the cleaned mesh always says 0")

    # ------------------------------------------------------------ the sentence
    s = pc.spoken(pc.report(cube, [20, 20, 20]))
    check("a sound part gets a plain answer", "should print" in s, s)
    s = pc.spoken(pc.report(box(300, 300, 5), [300, 300, 5]))
    check("an oversized part leads with the bed", "too large for the bed" in s, s)
    s = pc.spoken(pc.report(thin, [40, 30, 0.6]))
    check("a thin part says so, and says 'about'", "about" in s and "thinnest wall" in s, s)
    check("...every sentence is addressed to him", s.rstrip().endswith("sir."), s)

    # ------------------------------------------- the warning before the slicer
    # An STL written to disk and read back, not a numpy array handed straight
    # over: the point is the whole path a real file takes.
    from tools import fabrication as F

    def stl(name, tris):
        p = os.path.join(tempfile.mkdtemp(), name)
        with open(p, "w", encoding="ascii") as f:
            f.write("solid t\n")
            for t in tris:
                f.write("facet normal 0 0 0\n outer loop\n")
                for v in t:
                    f.write(f"  vertex {v[0]:.6f} {v[1]:.6f} {v[2]:.6f}\n")
                f.write(" endloop\nendfacet\n")
            f.write("endsolid t\n")
        return p

    check("a sound STL draws no warning before slicing",
          F.mesh_warning_for(stl("good.stl", cube)) is None)
    w = F.mesh_warning_for(stl("holed.stl", holed))
    check("a leaky STL warns before it reaches the slicer", bool(w), w)
    check("...and says what is wrong, in words",
          w and "open edges" in w and "watertight" in w, w)
    check("...and that the slicer will change it",
          w and "repair" in w, w)
    check("an unreadable file warns about nothing rather than raising",
          F.mesh_warning_for(os.path.join(tempfile.mkdtemp(), "nope.stl")) is None)

    # ------------------------------------------------------------------ G-code
    d = tempfile.mkdtemp()

    def write(nm, body):
        p = os.path.join(d, nm)
        open(p, "w", encoding="utf-8").write(body)
        return p

    prusa = write("prusa.gcode", """
G21
G90
M82
G92 E0
;LAYER_CHANGE
;Z:0.25
;HEIGHT:0.25
G1 Z0.25 F5000
;TYPE:Perimeter
G1 X0 Y0 F3000
G1 X10 Y0 E1
G1 X10 Y10 E2
G1 E-2 F1800
;LAYER_CHANGE
;Z:0.45
G1 Z0.45 F5000
;TYPE:External perimeter
G1 X0 Y0 F3000
G1 E0 F1800
G1 X10 Y0 E1
""")
    r = gcode.parse(prusa)
    check("PrusaSlicer: both layers are found", r["count"] == 2, r["count"])
    check("...at their true heights, not the marker's",
          [L["z"] for L in r["layers"]] == [0.25, 0.45],
          [L["z"] for L in r["layers"]])
    check("...layer height is derived", r["layer_height"] == 0.2, r)
    check("...the retraction drew nothing",
          sum(len(p) for L in r["layers"] for p in L["paths"]) == 10,
          [L["paths"] for L in r["layers"]])
    check("...feature types are kept",
          r["layers"][1]["types"] == ["External perimeter"], r["layers"][1]["types"])

    cura = write("cura.gcode", """
G90
M83
;LAYER:0
G1 Z0.3 F1200
G1 X0 Y0 F3000
G1 X5 Y0 E0.5
;LAYER:1
G1 Z0.6 F1200
G1 X5 Y5 E0.5
""")
    r = gcode.parse(cura)
    check("Cura relative extrusion is read", r["count"] == 2, r)
    check("...and both moves drew",
          sum(len(p) for L in r["layers"] for p in L["paths"]) == 8, r["layers"])
    check("...at the right heights", [L["z"] for L in r["layers"]] == [0.3, 0.6],
          "M83 misread as M82 makes every move look like a retraction")

    bare = write("bare.gcode", """
G90
M82
G1 Z0.2
G1 X0 Y0
G1 X10 Y0 E1
G1 Z0.4
G1 X10 Y10 E2
""")
    r = gcode.parse(bare)
    check("a file with no layer comments still splits on Z", r["count"] == 2, r)

    reset = write("reset.gcode", """
G90
M82
G1 Z0.2
G1 X0 Y0 E500
G92 E0
G1 X10 Y0 E1
""")
    r = gcode.parse(reset)
    check("G92 resets the extruder rather than reading as a huge retraction",
          r["count"] == 1 and r["points"] == 2, r)

    # The space after the command is optional in the spec, and post-processors
    # routinely strip it. Splitting on whitespace read `G1X10Y0` as a command
    # named "G1X10Y0" and skipped every move in such a file without a word.
    tight = write("tight.gcode",
                  "G90\nM82\nG1Z0.2\nG1X0Y0\nG1X10Y0E1\nG1Z0.4\nG1X10Y10E2\n")
    r = gcode.parse(tight)
    check("G-code with no space after the command still parses",
          r["count"] == 2 and r["points"] == 4, r)

    arcs = write("arcs.gcode", """
G90
M82
G1 Z0.2
G1 X0 Y0 E1
G2 X10 Y10 I5 J5 E2
""")
    r = gcode.parse(arcs)
    check("arcs are counted rather than silently dropped", r["arcs_skipped"] == 1, r)

    # PrusaSlicer's start G-code lifts the nozzle to Z5 BEFORE the first
    # `;LAYER_CHANGE`. The Z-rise fallback used to read that as layer one, giving
    # every real file a phantom layer that had extruded nothing. It was filtered
    # out of the result, so it stayed invisible until a layer cap made the
    # preview come back one layer short of what was asked for.
    lift = write("lift.gcode", """
G90
M82
G28
G1 Z5 F5000
G92 E0
;LAYER_CHANGE
;Z:0.25
G1 Z0.25 F5000
G1 X0 Y0 F3000
G1 X10 Y0 E1
;LAYER_CHANGE
G1 Z0.45 F5000
G1 X0 Y0 F3000
G1 X10 Y0 E2
""")
    r = gcode.parse(lift)
    check("a nozzle lift before the first marker is not a layer",
          r["count"] == 2, r["count"])
    check("...so layer one is the real first layer",
          r["layers"][0]["z"] == 0.25, r["layers"][0]["z"])

    r = gcode.parse(prusa, max_points=2)
    check("a huge file is truncated, and says so", r["truncated"] is True, r)
    check("...but the layer count stays true", r["count"] == 2, r)
    r = gcode.parse(lift, max_layers=1)
    check("a layer cap keeps exactly what was asked for", r["shown"] == 1, r)
    check("...counts every layer anyway", r["count"] == 2, r)
    check("...and says it truncated", r["truncated"] is True, r)
    check("...and does not weld the dropped layers onto the last kept one",
          len(r["layers"][0]["paths"]) == 1, r["layers"][0]["paths"])
    s = gcode.summary(prusa)
    check("summary reports layers without carrying geometry",
          s["count"] == 2 and "layers" not in s, s)

    r = gcode.parse(os.path.join(d, "nothing-here.gcode"))
    check("a missing file is a sentence, not an exception", "error" in r, r)

    # --------------------------------------------------- the real captured slice
    real = os.path.join(os.environ.get("APPDATA", ""), "JARVIS", "fabrication",
                        "gate-cube.gcode")
    if os.path.exists(real):
        r = gcode.parse(real)
        # 0.25 first layer + 99 x 0.2 = 20.05 on a 20 mm cube, per the bundled
        # profile. Any other number means the parser drifted.
        check("a real PrusaSlicer slice of a 20 mm cube reads 100 layers",
              r["count"] == 100, r["count"])
        check("...topping out at 20.05 mm", r["z_max"] == 20.05, r["z_max"])
        check("...at the profile's 0.2 mm layer height", r["layer_height"] == 0.2, r)
    else:
        print("  note: no captured slice on this machine; synthetic cases only")

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
