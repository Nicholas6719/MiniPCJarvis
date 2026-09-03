"""STL in, something a hologram can draw out.

Phase A of the hologram build. The browser never sees an STL: the sidecar parses
it and sends triangles plus a FEATURE-EDGE list, and that distinction is the
whole reason this file exists.

WHY FEATURE EDGES. A cube is twelve triangles and thirty-six triangle edges, of
which only twelve are real. Draw them all and a cube looks like a cube with an X
across every face; do it to a forty-thousand-triangle mesh from a photo and the
hologram is an unreadable ball of wool. So every edge shared by two faces is kept
only when the faces actually turn — the dihedral angle exceeds a threshold — which
is what three.js's EdgesGeometry does in the browser, done here instead because
the payload crossing the wire should already be small.

WHY IN PYTHON AT ALL. Keeping the loader out of the bundle is part of it, but the
real reason is that the same parse feeds the print checks: triangle count, bounding
box in true millimetres, bed fit, and later the overhang pass all want the mesh as
numbers, not as a WebGL buffer.

STL carries no units. Every slicer on earth treats the numbers as millimetres and
so does this.
"""
from __future__ import annotations

import logging
import math
import struct

import numpy as np

log = logging.getLogger("jarvis.meshio")

MAX_BYTES = 120_000_000          # a 120 MB STL is a mistake, not a part

# The most triangles worth sending to the stage. Everything we MAKE is well
# under this — a tier-4 reconstruction is 30-60k — but a model somebody
# sculpted and published is not: 274,902 for a helmet panel, 632,304 for an
# anatomical skull. Nine floats a triangle, so 632k is 22 MB of float32 and
# 30 MB base64, and it used to be uncapped because nothing had ever been that
# big. The file on disk keeps every triangle; only the projection is thinned.
MAX_PROJECT_TRIS = 150_000
FEATURE_ANGLE_DEG = 22.0         # below this the surface is flat enough to ignore


class BadMesh(Exception):
    """The file is not a mesh we can draw. Always carries a sentence for him."""


def _is_binary(head: bytes) -> bool:
    """ASCII STL starts 'solid', but so do some binary ones written by careless
    exporters — the reliable test is whether the declared triangle count matches
    the file length, so that is what decides it."""
    return not head.lstrip()[:5].lower().startswith(b"solid")


def _parse_binary(data: bytes) -> np.ndarray:
    if len(data) < 84:
        raise BadMesh("that STL is too short to contain anything")
    count = struct.unpack_from("<I", data, 80)[0]
    need = 84 + count * 50
    if count == 0:
        raise BadMesh("that STL contains no triangles")
    if need > len(data):
        raise BadMesh("that STL is truncated — it claims more triangles than it holds")
    # 50 bytes per facet: 12 floats (normal + 3 verts) then a 2-byte attribute.
    raw = np.frombuffer(data, dtype=np.uint8, count=count * 50, offset=84)
    raw = raw.reshape(count, 50)[:, 12:48].copy()          # drop normal + attr
    return raw.view("<f4").reshape(count, 3, 3).astype(np.float32)


def _parse_ascii(text: str) -> np.ndarray:
    verts: list[tuple[float, float, float]] = []
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith("vertex"):
            continue
        parts = s.split()
        if len(parts) < 4:
            raise BadMesh("that STL has a malformed vertex line")
        try:
            verts.append((float(parts[1]), float(parts[2]), float(parts[3])))
        except ValueError:
            raise BadMesh("that STL has a vertex that is not a number")
    if not verts:
        raise BadMesh("that STL contains no triangles")
    if len(verts) % 3:
        raise BadMesh("that STL has an incomplete triangle")
    return np.asarray(verts, dtype=np.float32).reshape(-1, 3, 3)


def load_stl(path: str) -> np.ndarray:
    """-> (n, 3, 3) float32 triangles. Raises BadMesh with a speakable reason."""
    try:
        with open(path, "rb") as fh:
            data = fh.read(MAX_BYTES + 1)
    except OSError as e:
        raise BadMesh(f"I couldn't read that file: {e}")
    if len(data) > MAX_BYTES:
        raise BadMesh("that model is far too large to project")
    if not data:
        raise BadMesh("that file is empty")

    if _is_binary(data[:80]):
        return _parse_binary(data)
    # It SAYS ascii. Believe the arithmetic instead: if the binary triangle count
    # explains the file length exactly, it is binary wearing an ascii hat.
    if len(data) >= 84:
        count = struct.unpack_from("<I", data, 80)[0]
        if count and 84 + count * 50 == len(data):
            return _parse_binary(data)
    try:
        return _parse_ascii(data.decode("utf-8", errors="replace"))
    except BadMesh:
        raise
    except Exception as e:
        raise BadMesh(f"I couldn't make sense of that STL: {e}")




def _parse_obj(text: str) -> np.ndarray:
    """OBJ -> (n, 3, 3) triangles. Vertices and faces only.

    An OBJ face may be a polygon rather than a triangle and may reference
    vertices from the end of the file with negative indices, and both are common
    enough in real exports that ignoring either gives a mesh full of holes.
    Everything else in the format — normals, texture coordinates, materials,
    groups, smoothing — is dropped: the stage draws translucent faces and bright
    edges and has no use for any of it.
    """
    verts: list = []
    faces: list = []
    for line in text.splitlines():
        if not line or line[0] not in "vf":
            continue
        if line[0] == "v":
            # `v` is a vertex; `vt`, `vn` and `vp` are not, and they outnumber it.
            if line[1:2] not in (" ", "\t"):
                continue
            p = line.split()
            if len(p) < 4:
                continue
            try:
                verts.append((float(p[1]), float(p[2]), float(p[3])))
            except ValueError:
                continue
        else:
            if line[1:2] not in (" ", "\t"):
                continue
            idx: list = []
            for tok in line.split()[1:]:
                head = tok.split("/", 1)[0]
                if not head:
                    continue
                try:
                    i = int(head)
                except ValueError:
                    continue
                # 1-based, and negative counts back from the vertices seen SO
                # FAR — which is why this cannot be done after the fact.
                idx.append(i - 1 if i > 0 else len(verts) + i)
            # A fan, so a quad or an n-gon becomes triangles rather than being
            # dropped. Most sculpted exports are quads.
            for k in range(1, len(idx) - 1):
                faces.append((idx[0], idx[k], idx[k + 1]))

    if not verts:
        raise BadMesh("there are no vertices in that file")
    if not faces:
        raise BadMesh("that file has vertices but no faces, so there is "
                      "nothing to draw")
    V = np.asarray(verts, dtype=np.float32)
    F = np.asarray(faces, dtype=np.int64)
    if F.min() < 0 or F.max() >= len(V):
        # A file that indexes past its own vertex list is corrupt, and letting
        # numpy wrap the index would silently draw a different shape.
        raise BadMesh("that file points at vertices it doesn't contain")
    return V[F]


# What we can actually parse. Kept next to the loader so the download filter and
# the loader cannot disagree about it — `fetch` advertised OBJ for a while and
# then refused it, which is the same bug in two places.
READABLE = (".stl", ".obj")


def load(path: str) -> np.ndarray:
    """-> (n, 3, 3) float32 triangles, from whichever format this is."""
    if str(path).lower().endswith(".obj"):
        return load_obj(path)
    return load_stl(path)


def load_obj(path: str) -> np.ndarray:
    """-> (n, 3, 3) float32 triangles. Raises BadMesh with a speakable reason."""
    try:
        with open(path, "rb") as fh:
            data = fh.read(MAX_BYTES + 1)
    except OSError as e:
        raise BadMesh(f"I couldn't read that file: {e}")
    if len(data) > MAX_BYTES:
        raise BadMesh("that model is far too large to project")
    if not data:
        raise BadMesh("that file is empty")
    try:
        return _parse_obj(data.decode("utf-8", errors="replace"))
    except BadMesh:
        raise
    except Exception as e:
        raise BadMesh(f"I couldn't make sense of that OBJ: {e}")

def feature_edges(tris: np.ndarray, angle_deg: float = FEATURE_ANGLE_DEG,
                  max_edges: int = 60_000) -> np.ndarray:
    """Edges worth drawing: boundaries, and creases sharper than `angle_deg`.

    Vertices are welded by rounded position first — STL stores every triangle
    independently, so the same corner appears three times with identical bytes and
    nothing is shared until you make it so.
    """
    if tris.size == 0:
        return np.zeros((0, 2, 3), dtype=np.float32)

    flat = tris.reshape(-1, 3)
    # Weld: round to a tolerance relative to the model's own size, so a 200 mm
    # part and a 2 mm part both weld sensibly.
    span = float(np.max(np.ptp(flat, axis=0))) or 1.0
    quant = np.round(flat / (span * 1e-5)).astype(np.int64)
    _, inverse = np.unique(quant, axis=0, return_inverse=True)
    idx = inverse.reshape(-1, 3)

    # Face normals, for the dihedral test.
    a, b, c = tris[:, 0], tris[:, 1], tris[:, 2]
    n = np.cross(b - a, c - a)
    ln = np.linalg.norm(n, axis=1, keepdims=True)
    ln[ln == 0] = 1.0                                   # degenerate face
    n = n / ln

    e = np.concatenate([idx[:, [0, 1]], idx[:, [1, 2]], idx[:, [2, 0]]], axis=0)
    e.sort(axis=1)
    face_of = np.tile(np.arange(len(tris)), 3)
    order = np.lexsort((e[:, 1], e[:, 0]))
    e, face_of = e[order], face_of[order]

    keep: list[int] = []
    cos_lim = math.cos(math.radians(angle_deg))
    i, total = 0, len(e)
    while i < total:
        j = i + 1
        while j < total and e[j, 0] == e[i, 0] and e[j, 1] == e[i, 1]:
            j += 1
        run = j - i
        if run == 1:
            keep.append(i)                              # boundary: always a real edge
        elif run == 2:
            if float(np.dot(n[face_of[i]], n[face_of[i + 1]])) < cos_lim:
                keep.append(i)                          # a crease
        else:
            keep.append(i)                              # non-manifold: show it
        i = j

    if not keep:
        return np.zeros((0, 2, 3), dtype=np.float32)
    if len(keep) > max_edges:
        # Never silently truncate: the caller reports it, and a mesh this dense
        # is a photo-derived one where the silhouette matters more than creases.
        log.warning("mesh has %d feature edges, keeping %d", len(keep), max_edges)
        keep = keep[:max_edges]

    pairs = e[keep]
    verts = np.zeros((len(pairs), 2, 3), dtype=np.float32)
    # Map welded indices back to a representative coordinate.
    rep = np.zeros((int(idx.max()) + 1, 3), dtype=np.float32)
    rep[idx.reshape(-1)] = flat
    verts[:, 0] = rep[pairs[:, 0]]
    verts[:, 1] = rep[pairs[:, 1]]
    return verts


def bodies(tris: np.ndarray) -> np.ndarray:
    """Which separate body each triangle belongs to. One int per triangle.

    "Explode it" only means something if there is more than one thing to pull
    apart, and an STL is a bag of triangles with no notion of parts — so the
    parts have to be found: weld the vertices, then union-find triangles that
    share one. Two bodies touching but not welded are correctly separate, which
    is exactly the case an exploded view exists for.

    Iterative union-find with path halving. Recursion would blow the stack on a
    photo-derived mesh, which is precisely the kind with the most components.
    """
    if tris.size == 0:
        return np.zeros(0, dtype=np.int32)

    flat = tris.reshape(-1, 3)
    span = float(np.max(np.ptp(flat, axis=0))) or 1.0
    quant = np.round(flat / (span * 1e-5)).astype(np.int64)
    _, inverse = np.unique(quant, axis=0, return_inverse=True)
    idx = inverse.reshape(-1, 3)

    parent = np.arange(len(tris), dtype=np.int64)

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = int(parent[i])
        return i

    # Group faces by welded vertex, then union every face touching that vertex.
    order = np.argsort(idx.reshape(-1), kind="stable")
    verts = idx.reshape(-1)[order]
    faces = (order // 3).astype(np.int64)
    starts = np.flatnonzero(np.r_[True, verts[1:] != verts[:-1]])
    for s, e in zip(starts, np.r_[starts[1:], len(verts)]):
        group = faces[s:e]
        root = find(int(group[0]))
        for f in group[1:]:
            r = find(int(f))
            if r != root:
                parent[r] = root

    roots = np.array([find(int(i)) for i in range(len(tris))])
    _, labels = np.unique(roots, return_inverse=True)
    return labels.astype(np.int32)


def describe(path: str, angle_deg: float = FEATURE_ANGLE_DEG) -> dict:
    """Everything the hologram and the print checks need, from one parse."""
    tris = load(path)
    lo = tris.reshape(-1, 3).min(axis=0)
    hi = tris.reshape(-1, 3).max(axis=0)
    size = (hi - lo)
    edges = feature_edges(tris, angle_deg)
    lab = bodies(tris)
    return {
        "path": path,
        "triangles": int(len(tris)),
        "edges": int(len(edges)),
        # Counted here so `holo_control explode` can refuse honestly on a single
        # body without re-parsing the file to find out.
        "body_count": int(lab.max()) + 1 if len(lab) else 0,
        "size_mm": [round(float(v), 2) for v in size],
        "min_mm": [round(float(v), 2) for v in lo],
        "max_mm": [round(float(v), 2) for v in hi],
        "_tris": tris,
        "_edges": edges,
    }


def _f32(arr) -> str:
    """A float array as base64 float32, little-endian.

    NOT a JSON list of numbers. A 38k-triangle mesh from tier 3 is 344,556
    coordinates, and as JSON that is a 7.5 MB response the browser then has to
    parse number by number. The same floats as base64 are 1.8 MB and arrive as
    one typed array — and it is EXACT, because the renderer wanted float32 all
    along: `Float32BufferAttribute` converts to it either way.

    Rounding the JSON was tried first and saved almost nothing: numpy's float32
    widened back to float64 on `tolist()`, so `round(x, 2)` produced
    12.34000015258789 rather than 12.34.
    """
    import base64
    return base64.b64encode(np.ascontiguousarray(arr, dtype="<f4").tobytes()).decode("ascii")


def to_payload(path: str, angle_deg: float = FEATURE_ANGLE_DEG) -> dict:
    """The wire format: flat float32 arrays, centred on the model's own middle so
    the renderer never has to know where in space the exporter happened to put it."""
    d = describe(path, angle_deg)
    tris, edges = d.pop("_tris"), d.pop("_edges")

    # THIN A HUGE SCULPTURE FOR THE STAGE, and say that is what happened. A
    # stride is even across the surface, which is what makes it survive being
    # drawn translucent with its feature edges over the top — and those edges
    # are computed from the FULL mesh above, so the silhouette and the creases
    # stay exactly right however much of the interior is dropped.
    d["simplified"] = False
    if len(tris) > MAX_PROJECT_TRIS:
        d["projected_triangles"] = int(MAX_PROJECT_TRIS)
        d["simplified"] = True
        tris = tris[::int(np.ceil(len(tris) / MAX_PROJECT_TRIS))]

    centre = (np.asarray(d["min_mm"]) + np.asarray(d["max_mm"])) / 2.0
    d["positions_b64"] = _f32((tris.reshape(-1, 3) - centre).ravel())
    d["edge_positions_b64"] = _f32((edges.reshape(-1, 3) - centre).ravel()) if len(edges) else ""
    d["centre_mm"] = [round(float(v), 2) for v in centre]

    # Separate bodies, for the exploded view — and ONLY when there is more than
    # one, because a per-triangle label array on every single-body part is
    # kilobytes over the wire to say "there is nothing to explode".
    lab = bodies(tris)
    n_bodies = int(lab.max()) + 1 if len(lab) else 0
    d["body_count"] = n_bodies
    # ...and NOT as a third of a million JSON integers. A downloaded sculpture
    # is one shell: there is nothing to explode, and saying so should not cost
    # megabytes.
    if n_bodies > 1 and not d["simplified"]:
        d["bodies"] = lab.astype(int).tolist()
        # Where each body sits relative to the model's middle, so the renderer
        # knows which way to push it. Computed here because it is the same
        # arithmetic as the centring above and must not disagree with it.
        cent = []
        mids = tris.reshape(len(tris), 3, 3).mean(axis=1) - centre
        for i in range(n_bodies):
            cent.append([round(float(v), 3) for v in mids[lab == i].mean(axis=0)])
        d["body_centres"] = cent
    return d
