"""Will it print? The questions worth asking before plastic is spent.
Phase B of the hologram. These are the checks a person does by eye and then
regrets not doing properly, and every threshold here is the industry one rather
than a number invented for this file:
  * 45° FROM VERTICAL is the overhang limit for FDM. Past it surface quality
    degrades, and at steeper angles the print fails outright. Measured per face
    from its normal, which is exact and costs one dot product.
  * 0.8 mm is the thinnest wall any FDM machine will reliably produce.
    1.5 mm is the sensible minimum for a part that has to take load — so JARVIS
    can distinguish "that will print" from "that will print and then snap".
  * MESH INTEGRITY, because non-manifold geometry is the single commonest reason
    a slicer refuses a file, found in roughly one file in seven, and
    AI-generated meshes are specifically prone to it. Tiers 3 and 4 of the
    creation plan are exactly that.
WHAT THIS IS HONEST ABOUT. Wall thickness is an ESTIMATE and is labelled one
everywhere it surfaces. Rigorous minimum-thickness needs a medial-axis transform;
what this does is fire rays through the part and report a LOW PERCENTILE of the
solid spans it finds. That is genuinely useful for catching a 0.6 mm rib, and it
is not a guarantee — telling him a part is sound when it is not would be worse
than saying nothing.

A percentile rather than the minimum, because the minimum stopped being useful
the moment tier 3 arrived: a reconstructed mesh genuinely contains hair-thin
slivers where marching cubes met the isosurface tangentially, far below the
voxel size, and a real watertight 60 x 46 x 20 mm duck therefore measured a
0.01 mm wall and would have been called unprintable. A slicer ignores features
under its nozzle and so does this. The raw minimum is still reported, as
`thinnest_seen_mm`, so nothing is hidden.
"""
from __future__ import annotations
import logging
import numpy as np
log = logging.getLogger("jarvis.printcheck")
OVERHANG_LIMIT_DEG = 45.0        # from vertical; past this an FDM print wants support
MIN_WALL_MM = 0.8                # thinnest any FDM machine will reliably produce
FUNCTIONAL_WALL_MM = 1.5         # thinnest worth trusting with load
BED_MM = 220.0                   # matches profiles/generic_fdm_0.4.ini
MAX_Z_MM = 250.0                 # ...as does this: max_print_height
# A face cut to EXACTLY the limit must not be flagged. asin(-nz) for a true 45
# degree chamfer comes back as 45.00000000000001, and a float32 STL drifts
# further still, so a bare `> 45.0` reports every deliberate chamfer as an
# overhang. Nothing about FDM is precise to a tenth of a degree, so spending one
# on the boundary costs nothing real and removes a whole class of false alarm.
ANGLE_TOL_DEG = 0.1
# How square a ray must meet a surface for the span it measures to count as a
# wall. cos(75 degrees): anything more tangential than that is the ray clipping a
# silhouette, not passing through material. Measured need: without it, a
# watertight 60 x 46 x 20 mm mesh from TripoSR reported a 0.01 mm wall, because
# on a smooth organic surface the grazing rays are the minimum every time.
GRAZE_COS = 0.26
# The estimate is a LOW PERCENTILE of the measured spans, not their minimum.
#
# A reconstructed mesh genuinely contains hair-thin slivers — marching cubes
# produces them wherever the isosurface is tangent to the grid, far below the
# 0.375 mm voxel of a 60 mm model at resolution 160. The absolute minimum is
# therefore a true measurement of an artefact: a real TripoSR duck measured
# 0.01 mm and would have been reported as unprintable. A slicer ignores features
# under its nozzle; so should this.
#
# 5% is chosen so a deliberately thin part is still caught — a 0.6 mm plate has a
# THIRD of its rays at 0.6 mm — while a handful of artefacts cannot dominate. It
# is the same trade the docstring already states: this can miss a thin feature
# that almost no ray crossed.
WALL_PERCENTILE = 5.0
def face_normals(tris: np.ndarray) -> np.ndarray:
    n = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    ln = np.linalg.norm(n, axis=1, keepdims=True)
    ln[ln == 0] = 1.0
    return n / ln
def areas(tris: np.ndarray) -> np.ndarray:
    return 0.5 * np.linalg.norm(
        np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0]), axis=1)
def overhang_mask(tris: np.ndarray, limit_deg: float = OVERHANG_LIMIT_DEG) -> np.ndarray:
    """Which faces need support. One definition, used by the count and by the
    geometry the HUD paints red — so a face can never be counted and not drawn."""
    if len(tris) == 0:
        return np.zeros(0, dtype=bool)
    n = face_normals(tris)
    zmin = float(tris[:, :, 2].min())
    # A face lying ON the bed points straight down too, and is the one downward
    # surface that needs nothing under it.
    on_bed = np.all(np.abs(tris[:, :, 2] - zmin) < 1e-4, axis=1)
    ang = np.degrees(np.arcsin(np.clip(-n[:, 2], 0.0, 1.0)))
    return (n[:, 2] < 0) & (~on_bed) & (ang > limit_deg + ANGLE_TOL_DEG)
def overhangs(tris: np.ndarray, limit_deg: float = OVERHANG_LIMIT_DEG,
              want_positions: bool = True) -> dict:
    """Downward faces steeper than the limit, measured from vertical.
    The angle is asin(-nz): a vertical wall gives 0°, a 45° chamfer gives 45°,
    and a flat ceiling gives 90° — the worst case, and the one that always needs
    support. Faces lying ON the bed are excluded; they point straight down too,
    and they are the one downward surface that needs nothing under it.
    """
    if len(tris) == 0:
        return {"faces": 0, "area_mm2": 0.0, "worst_deg": 0.0, "positions": []}
    n = face_normals(tris)
    # asin is only defined on [-1, 1]; normals are unit but floating point drifts.
    ang = np.degrees(np.arcsin(np.clip(-n[:, 2], 0.0, 1.0)))
    bad = overhang_mask(tris, limit_deg)
    a = areas(tris)
    return {
        "faces": int(bad.sum()),
        "area_mm2": round(float(a[bad].sum()), 1),
        "worst_deg": round(float(ang[bad].max()), 1) if bad.any() else 0.0,
        "fraction": round(float(a[bad].sum() / max(a.sum(), 1e-9)), 4),
        "positions": tris[bad].reshape(-1, 3).astype(np.float32).round(3).ravel().tolist()
        if (want_positions and bad.any()) else [],
    }
def bed_fit(size_mm, bed: float = BED_MM, max_z: float = MAX_Z_MM) -> dict:
    """Does it fit AS IT SITS — and if not, would turning it help?
    The footprint is X by Y and the height is Z, because that is how the STL is
    oriented and how the slicer will place it. Sorting the three and calling the
    two largest the footprint — which this did first — declared a 50 x 50 x 400
    tower too wide for the bed, when what is actually wrong with it is that it is
    too tall. Height is checked against the printer's Z, separately, because the
    two problems have completely different answers: one is "turn it", the other
    is "cut it in half".
    """
    x, y, z = (float(v) for v in size_mm)
    fits = x <= bed and y <= bed
    # The best footprint any rotation could give is the two smallest dimensions.
    smallest = sorted([x, y, z])[:2]
    rotated = smallest[0] <= bed and smallest[1] <= bed
    return {"bed_mm": bed, "fits": bool(fits),
            "footprint_mm": [round(x, 1), round(y, 1)],
            "height_mm": round(z, 1),
            "over_by_mm": 0.0 if fits else round(max(x, y) - bed, 1),
            "fits_if_rotated": bool(rotated and not fits),
            "too_tall": bool(z > max_z),
            "max_z_mm": max_z}
def _axis_hits(tris: np.ndarray, axis: int, uv: np.ndarray) -> list:
    """Where rays parallel to `axis` cross the surface. Pure numpy, no rtree.
    trimesh's ray engines all want rtree — a compiled dependency, and a
    PyInstaller problem for one estimate. But these rays are AXIS-ALIGNED, which
    makes the general Möller–Trumbore machinery unnecessary: project each
    triangle onto the other two axes, test containment in 2D with barycentric
    coordinates, and solve the plane for the remaining coordinate. Exact, and it
    needs nothing but numpy.
    Returns, per ray, a sorted array of crossing depths AND the matching
    |n·axis| for each crossing — how square the surface was to the ray. The
    caller needs that second number to throw away GRAZES: a ray that clips a
    silhouette tangentially enters and leaves almost immediately and reports a
    hundredth of a millimetre of "wall" that does not exist. On a smooth organic
    mesh those grazes dominate the minimum completely.
    """
    u, v = [i for i in range(3) if i != axis]
    a, b, c = tris[:, 0], tris[:, 1], tris[:, 2]
    au, av = a[:, u], a[:, v]
    v0u, v0v = b[:, u] - au, b[:, v] - av
    v1u, v1v = c[:, u] - au, c[:, v] - av
    denom = v0u * v1v - v1u * v0v
    live = np.abs(denom) > 1e-12          # triangles edge-on contribute nothing
    if not live.any():
        return [(np.empty(0), np.empty(0)) for _ in range(len(uv))]
    n = face_normals(tris)
    out = []
    for pu, pv in uv:
        wu, wv = pu - au, pv - av
        s = (wu * v1v - v1u * wv)
        t = (v0u * wv - wu * v0v)
        # Edge-on triangles divide by ~0 and produce inf/NaN. They are masked out
        # by `live` anyway, but the arithmetic still happens on the whole array,
        # so the suppression has to cover the COMPARISONS too — NaN + NaN warns.
        with np.errstate(invalid="ignore", divide="ignore"):
            s = s / denom
            t = t / denom
            inside = live & (s >= 0) & (t >= 0) & (s + t <= 1)
        if not inside.any():
            out.append((np.empty(0), np.empty(0)))
            continue
        # The point on the triangle's plane, solved for the ray's own axis.
        na = n[inside][:, axis]
        ok = np.abs(na) > 1e-12
        if not ok.any():
            out.append((np.empty(0), np.empty(0)))
            continue
        pa = a[inside][ok]
        nn = n[inside][ok]
        du = pu - pa[:, u]
        dv = pv - pa[:, v]
        depth = pa[:, axis] - (nn[:, u] * du + nn[:, v] * dv) / nn[:, axis]
        order = np.argsort(depth)
        # The depths AND how square each surface was to the ray, in the same
        # order, so the caller can pair them and reject grazes.
        out.append((depth[order], np.abs(nn[order][:, axis])))
    return out
def thinnest_wall(tris: np.ndarray, samples: int = 4000) -> dict:
    """An ESTIMATE of the thinnest solid span, by ray casting through the part.
    Deliberately not sold as a measurement. A rigorous minimum thickness needs a
    medial-axis transform; this fires rays along each axis, measures the solid
    runs between entry and exit hits, and reports the smallest. It reliably
    catches a thin rib, and it can miss a thin feature no ray happened to cross —
    which is why every caller says "about" and never "the minimum is".
    """
    if len(tris) == 0:
        return {"estimate_mm": None, "why": "no geometry"}
    try:
        flat = tris.reshape(-1, 3)
        lo, hi = flat.min(axis=0), flat.max(axis=0)
        rng = np.random.RandomState(0)
        kept: list = []
        crossed = 0
        per_axis = max(8, samples // 3)
        for axis in range(3):
            u, v = [i for i in range(3) if i != axis]
            # Inset slightly: a ray exactly on the boundary grazes the surface
            # and produces a spurious near-zero span.
            pad_u = (hi[u] - lo[u]) * 0.02
            pad_v = (hi[v] - lo[v]) * 0.02
            uv = np.stack([rng.uniform(lo[u] + pad_u, hi[u] - pad_u, per_axis),
                           rng.uniform(lo[v] + pad_v, hi[v] - pad_v, per_axis)], axis=1)
            for hits, square in _axis_hits(tris, axis, uv):
                if len(hits) < 2:
                    continue
                # Solid runs are entry->exit pairs. An odd count means the ray
                # grazed an edge; drop the stray rather than pairing it wrongly.
                n_pairs = len(hits) // 2
                enter, exit_ = hits[0:2 * n_pairs:2], hits[1:2 * n_pairs:2]
                spans = exit_ - enter
                # REJECT GRAZES. A span only measures a wall if the ray met both
                # surfaces reasonably square to them; a ray clipping a silhouette
                # tangentially enters and leaves almost at once and reports a
                # hundredth of a millimetre that is not there. On a smooth mesh
                # from TripoSR those grazes ARE the minimum — a real 60x46x20 mm
                # model came back as a 0.01 mm wall, which would have told him a
                # perfectly good part could not be printed.
                sq = np.minimum(square[0:2 * n_pairs:2], square[1:2 * n_pairs:2])
                keep = (spans > 1e-4) & (sq >= GRAZE_COS)
                spans = spans[keep]
                if len(spans):
                    crossed += 1
                    kept.append(spans)
        if not kept:
            return {"estimate_mm": None, "why": "no rays crossed solid material"}
        allspans = np.concatenate(kept)
        best = float(np.percentile(allspans, WALL_PERCENTILE))
        return {"estimate_mm": round(best, 2),
                "thinnest_seen_mm": round(float(allspans.min()), 3),
                "below_minimum": bool(best < MIN_WALL_MM),
                "below_functional": bool(best < FUNCTIONAL_WALL_MM),
                "rays_crossing": crossed,
                "why": f"sampled, not measured — this is the {WALL_PERCENTILE:.0f}th "
                       f"percentile of the spans found, so a hair-thin artefact "
                       f"cannot dominate and a thin feature almost no ray crossed "
                       f"can still be missed"}
    except Exception as e:
        log.debug("wall estimate failed", exc_info=True)
        return {"estimate_mm": None, "why": f"could not estimate ({e})"}
def integrity(tris: np.ndarray) -> dict:
    """Is this mesh something a slicer will accept, and can it be repaired?
    Reports rather than repairs. What gets fixed and what he is told about it is
    the caller's decision — silently changing a model he is about to print is
    not this function's business.
    """
    # Counted from the RAW triangles, before trimesh sees them. Constructing a
    # Trimesh with process=True quietly drops zero-area faces, so asking the
    # cleaned mesh how many it has always answered nought — a check that could
    # never fail and therefore said nothing. This is the number in his file.
    degenerate = int((areas(tris) <= 1e-12).sum())
    try:
        import trimesh
        mesh = trimesh.Trimesh(vertices=tris.reshape(-1, 3),
                               faces=np.arange(len(tris) * 3).reshape(-1, 3),
                               process=True)
        out = {
            "watertight": bool(mesh.is_watertight),
            "winding_consistent": bool(mesh.is_winding_consistent),
            "volume_mm3": round(float(mesh.volume), 1) if mesh.is_watertight else None,
            "degenerate_faces": degenerate,
        }
        try:
            # Edges used by exactly ONE face — the actual holes in the surface.
            # `facets_boundary` is not this: it is the outline of coplanar facet
            # groups, and it reported 6 "open edges" on a watertight cube, which
            # is the number of its faces. A wrong number here would send him
            # repairing a model that is already sound.
            import trimesh.grouping as _g
            singles = _g.group_rows(mesh.edges_sorted, require_count=1)
            out["open_edges"] = int(len(singles))
        except Exception:
            out["open_edges"] = None
        out["sliceable"] = bool(out["watertight"] and out["winding_consistent"])
        return out
    except Exception as e:
        log.debug("integrity check failed", exc_info=True)
        return {"sliceable": None, "degenerate_faces": degenerate,
                "why": f"could not check ({e})"}
def report(tris: np.ndarray, size_mm) -> dict:
    """Everything at once, for the tool and the hologram overlay.
    Counts and angles only — no geometry. The overhang TRIANGLES are built by
    the caller, which is the only place that knows where to centre them; having
    this build an uncentred copy first meant every check assembled thousands of
    floats that were thrown away a line later.
    """
    over = overhangs(tris, want_positions=False)
    r = {
        "size_mm": [round(float(v), 1) for v in size_mm],
        "bed": bed_fit(size_mm),
        "overhangs": {k: v for k, v in over.items() if k != "positions"},
        "wall": thinnest_wall(tris),
        "integrity": integrity(tris),
        "limits": {"overhang_deg": OVERHANG_LIMIT_DEG,
                   "min_wall_mm": MIN_WALL_MM,
                   "functional_wall_mm": FUNCTIONAL_WALL_MM},
    }
    return r
def _mm(v) -> str:
    """A number as it would be said. Nobody says "four hundred point oh"."""
    f = float(v)
    return str(int(round(f))) if abs(f - round(f)) < 0.05 else f"{f:.1f}"
def spoken(r: dict) -> str:
    """One or two sentences, in his voice. Facts first, worst news first."""
    bits: list[str] = []
    if not r["bed"]["fits"]:
        bits.append(f"it's {_mm(r['bed']['over_by_mm'])} millimetres too large for the bed"
                    + (", though it would fit turned" if r["bed"].get("fits_if_rotated") else ""))
    if r["bed"].get("too_tall"):
        bits.append(f"it's {_mm(r['bed']['height_mm'])} millimetres tall, past the "
                    f"{_mm(r['bed']['max_z_mm'])} the printer can reach")
    integ = r.get("integrity") or {}
    if integ.get("sliceable") is False:
        # Not "the slicer would refuse it". It usually will not: PrusaSlicer
        # repairs a leaky mesh by its own rules and prints something slightly
        # other than the model — which is the outcome worth warning him about,
        # and `slice_part` warns rather than refusing for exactly that reason.
        bits.append("the mesh isn't watertight, so the slicer would repair it "
                    "its own way")
    o = r["overhangs"]
    if o["faces"]:
        bits.append(f"the overhangs reach {o['worst_deg']:.0f} degrees, so it would want supports")
    w = r.get("wall") or {}
    if w.get("below_minimum"):
        bits.append(f"the thinnest wall is about {_mm(w['estimate_mm'])} millimetres, "
                    "which is under what the nozzle can lay down")
    elif w.get("below_functional"):
        bits.append(f"the thinnest wall is about {_mm(w['estimate_mm'])} millimetres — "
                    "printable, but not strong")
    if not bits:
        return "It should print as it is, sir."
    if r["bed"]["fits"]:
        return "It fits the bed, but " + "; ".join(bits) + ", sir."
    # Not `.capitalize()`: that lowercases everything after the first letter, so
    # the first millimetre figure that arrives with a capital in it would be
    # quietly mangled. Only the opening letter should change.
    line = "; ".join(bits)
    return line[:1].upper() + line[1:] + ", sir."
