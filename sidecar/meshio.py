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


def describe(path: str, angle_deg: float = FEATURE_ANGLE_DEG) -> dict:
    """Everything the hologram and the print checks need, from one parse."""
    tris = load_stl(path)
    lo = tris.reshape(-1, 3).min(axis=0)
    hi = tris.reshape(-1, 3).max(axis=0)
    size = (hi - lo)
    edges = feature_edges(tris, angle_deg)
    return {
        "path": path,
        "triangles": int(len(tris)),
        "edges": int(len(edges)),
        "size_mm": [round(float(v), 2) for v in size],
        "min_mm": [round(float(v), 2) for v in lo],
        "max_mm": [round(float(v), 2) for v in hi],
        "_tris": tris,
        "_edges": edges,
    }


def to_payload(path: str, angle_deg: float = FEATURE_ANGLE_DEG) -> dict:
    """The wire format: flat float lists, centred on the model's own middle so the
    renderer never has to know where in space the exporter happened to put it."""
    d = describe(path, angle_deg)
    tris, edges = d.pop("_tris"), d.pop("_edges")
    centre = (np.asarray(d["min_mm"]) + np.asarray(d["max_mm"])) / 2.0
    d["positions"] = (tris.reshape(-1, 3) - centre).astype(np.float32).round(4).ravel().tolist()
    d["edge_positions"] = ((edges.reshape(-1, 3) - centre).astype(np.float32)
                           .round(4).ravel().tolist()) if len(edges) else []
    d["centre_mm"] = [round(float(v), 2) for v in centre]
    return d
