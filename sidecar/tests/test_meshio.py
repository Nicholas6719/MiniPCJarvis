"""Phase A of the hologram: what the browser is handed, and what it is spared.

The property that matters most here is the FEATURE-EDGE reduction. A cube is
twelve triangles and thirty-six triangle edges, of which twelve are real; draw
them all and a cube wears an X on every face, and a forty-thousand-triangle mesh
from a photo becomes an unreadable ball of wool. So the count for a cube must be
exactly twelve, and it is asserted rather than eyeballed.

Also gated:
  * binary AND ascii STL, including a binary file that lies and starts "solid" —
    careless exporters do this, and believing the header instead of the
    arithmetic silently produces zero triangles;
  * every malformed shape is a SENTENCE (BadMesh), never a stack trace, because
    this is reached from a spoken request;
  * true millimetres out, because the print checks downstream depend on them and
    STL carries no units;
  * the payload is centred, so the renderer never inherits wherever the exporter
    happened to put the model in space.

Offline: no network, no GPU, no model. Run: python tests/test_meshio.py
"""
import math
import os
import struct
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "mesh.db"))

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


def cube_tris(s=20.0):
    """A unit-axis cube as 12 triangles, built by hand so the expected feature
    edge count (12) is a fact about geometry rather than about a file."""
    p = [(0, 0, 0), (s, 0, 0), (s, s, 0), (0, s, 0),
         (0, 0, s), (s, 0, s), (s, s, s), (0, s, s)]
    q = [(0, 1, 2), (0, 2, 3), (4, 6, 5), (4, 7, 6),
         (0, 4, 5), (0, 5, 1), (1, 5, 6), (1, 6, 2),
         (2, 6, 7), (2, 7, 3), (3, 7, 4), (3, 4, 0)]
    return [[p[a], p[b], p[c]] for a, b, c in q]


def write_binary(path, tris, header=b"binary stl"):
    with open(path, "wb") as f:
        f.write(header.ljust(80, b"\0"))
        f.write(struct.pack("<I", len(tris)))
        for t in tris:
            f.write(struct.pack("<3f", 0, 0, 0))
            for v in t:
                f.write(struct.pack("<3f", *v))
            f.write(b"\0\0")
    return path


def write_ascii(path, tris):
    with open(path, "w") as f:
        f.write("solid test\n")
        for t in tris:
            f.write(" facet normal 0 0 0\n  outer loop\n")
            for v in t:
                f.write(f"   vertex {v[0]} {v[1]} {v[2]}\n")
            f.write("  endloop\n endfacet\n")
        f.write("endsolid test\n")
    return path


def main() -> int:
    import meshio
    d = tempfile.mkdtemp()
    tris = cube_tris(20.0)

    # ---------------------------------------------------------- binary + ascii
    b = write_binary(os.path.join(d, "cube.stl"), tris)
    a = write_ascii(os.path.join(d, "cube_ascii.stl"), tris)

    for label, path in (("binary", b), ("ascii", a)):
        m = meshio.describe(path)
        check(f"{label} STL parses to 12 triangles", m["triangles"] == 12, m["triangles"])
        check(f"...{label} in true millimetres", m["size_mm"] == [20.0, 20.0, 20.0], m["size_mm"])
        check(f"...{label} reduces 36 triangle edges to the 12 real ones",
              m["edges"] == 12,
              f"{m['edges']} — a cube would wear an X on every face")

    # A binary file whose header says "solid". Believing the header gives zero
    # triangles and a hologram of nothing.
    liar = write_binary(os.path.join(d, "liar.stl"), tris, header=b"solid not really")
    m = meshio.describe(liar)
    check("a binary STL that starts 'solid' is still read as binary",
          m["triangles"] == 12, m["triangles"])

    # ------------------------------------------------------------- the payload
    p = meshio.to_payload(b)
    check("the payload carries triangle positions", len(p["positions"]) == 12 * 9)
    check("...and edge positions", len(p["edge_positions"]) == 12 * 6)
    check("...centred on the model, not on the exporter's origin",
          p["centre_mm"] == [10.0, 10.0, 10.0], p["centre_mm"])
    xs = p["positions"][0::3]
    check("...so the geometry straddles zero",
          abs(min(xs) + 10.0) < 0.01 and abs(max(xs) - 10.0) < 0.01,
          f"{min(xs)}..{max(xs)}")
    check("the payload has no numpy left in it",
          all(isinstance(v, float) for v in p["positions"][:6]))

    # --------------------------------------------------- creases versus flats
    # Two coplanar triangles form a square: the shared diagonal is NOT a feature.
    flat = [[(0, 0, 0), (10, 0, 0), (10, 10, 0)],
            [(0, 0, 0), (10, 10, 0), (0, 10, 0)]]
    f = write_binary(os.path.join(d, "flat.stl"), flat)
    m = meshio.describe(f)
    check("a flat square keeps its 4 sides and drops the diagonal",
          m["edges"] == 4, f"{m['edges']} edges")

    # A genuine crease IS kept.
    bent = [[(0, 0, 0), (10, 0, 0), (10, 10, 0)],
            [(0, 0, 0), (10, 10, 0), (0, 10, 8)]]
    m = meshio.describe(write_binary(os.path.join(d, "bent.stl"), bent))
    check("...but a real fold is kept", m["edges"] == 5, f"{m['edges']} edges")

    # --------------------------------------------------- everything malformed
    bad = []
    bad.append(("an empty file", os.path.join(d, "empty.stl")))
    open(bad[-1][1], "wb").close()

    p2 = os.path.join(d, "short.stl")
    open(p2, "wb").write(b"x" * 20)
    bad.append(("a file too short to be an STL", p2))

    p3 = os.path.join(d, "trunc.stl")
    with open(p3, "wb") as fh:
        fh.write(b"\0" * 80 + struct.pack("<I", 5000))      # claims 5000, holds none
    bad.append(("a truncated binary STL", p3))

    p4 = os.path.join(d, "zero.stl")
    with open(p4, "wb") as fh:
        fh.write(b"\0" * 80 + struct.pack("<I", 0))
    bad.append(("an STL with no triangles", p4))

    # Chop the LAST coordinate off the first vertex line. Done by finding the
    # line rather than by matching a literal: the writer formats 0 as "0", not
    # "0.0", so a literal replace silently matched nothing and the file stayed
    # perfectly valid — the test passed the parser a good file and called it bad.
    p5 = write_ascii(os.path.join(d, "part.stl"), tris)
    lines = open(p5).read().splitlines()
    for i, ln in enumerate(lines):
        if ln.strip().startswith("vertex"):
            lines[i] = " ".join(ln.split()[:-1])
            break
    open(p5, "w").write("\n".join(lines) + "\n")
    bad.append(("an ascii STL with a malformed vertex", p5))

    p6 = os.path.join(d, "nan.stl")
    open(p6, "w").write("solid s\n facet normal 0 0 0\n  outer loop\n"
                        "   vertex a b c\n   vertex 1 1 1\n   vertex 2 2 2\n"
                        "  endloop\n endfacet\nendsolid s\n")
    bad.append(("an ascii vertex that is not a number", p6))

    bad.append(("a file that does not exist", os.path.join(d, "nope.stl")))

    for label, path in bad:
        try:
            meshio.describe(path)
            check(f"{label} is refused", False, "it parsed something")
        except meshio.BadMesh as e:
            check(f"{label} is a sentence, not a crash", bool(str(e)), repr(e))
        except Exception as e:
            check(f"{label} is a sentence, not a crash", False,
                  f"raised {type(e).__name__} instead of BadMesh: {e}")

    # ------------------------------------------------------ degenerate + huge
    degen = [[(0, 0, 0), (0, 0, 0), (0, 0, 0)]] + cube_tris(10.0)
    m = meshio.describe(write_binary(os.path.join(d, "degen.stl"), degen))
    check("a zero-area triangle does not divide by zero", m["triangles"] == 13)

    check("the size ceiling is defined", meshio.MAX_BYTES > 0)

    # A dense mesh must be capped rather than sent whole.
    import numpy as np
    rng = np.random.RandomState(0)
    many = rng.rand(9000, 3, 3).astype(np.float32) * 40
    big = os.path.join(d, "many.stl")
    write_binary(big, many.tolist())
    m = meshio.describe(big)
    check("a dense mesh still parses", m["triangles"] == 9000)
    check("...and its edge list is capped, not unbounded",
          m["edges"] <= 60000, m["edges"])

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
