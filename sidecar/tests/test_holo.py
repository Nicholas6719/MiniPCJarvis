"""The holographic stage's wiring: what opens it, and what it refuses.

Phase A. The parser has its own gate (test_meshio.py); this covers the tool and
the boundary around it:

  * showing a model reports MILLIMETRES and a triangle count, because those are
    the words JARVIS speaks and the numbers phase B's print checks build on;
  * the geometry endpoint serves only what `show_hologram` has already opened —
    it is not a file reader the HUD can point anywhere;
  * a missing, unreadable or non-STL model is a sentence, never a stack trace;
  * `show_hologram` is SAFE and stays SAFE — it opens a panel, and the risk tier
    has to describe what the handler does. `face_confirm` sat at SAFE while able
    to switch the webcam on, and that is the mistake this assertion exists for.

Offline: no GPU, no browser. Run: python tests/test_holo.py
"""
import asyncio
import os
import struct
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "holo.db"))

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


def write_cube(path, s=20.0):
    p = [(0, 0, 0), (s, 0, 0), (s, s, 0), (0, s, 0),
         (0, 0, s), (s, 0, s), (s, s, s), (0, s, s)]
    q = [(0, 1, 2), (0, 2, 3), (4, 6, 5), (4, 7, 6), (0, 4, 5), (0, 5, 1),
         (1, 5, 6), (1, 6, 2), (2, 6, 7), (2, 7, 3), (3, 7, 4), (3, 4, 0)]
    with open(path, "wb") as f:
        f.write(b"cube".ljust(80, b"\0"))
        f.write(struct.pack("<I", len(q)))
        for a, b, c in q:
            f.write(struct.pack("<3f", 0, 0, 0))
            for v in (p[a], p[b], p[c]):
                f.write(struct.pack("<3f", *v))
            f.write(b"\0\0")
    return path


async def main() -> int:
    import events
    from tools import fabrication as F
    from tools import holo_tools as H

    seen = []
    real_emit = events.bus.emit

    async def spy(kind, **kw):
        seen.append((kind, kw))
        return await real_emit(kind, **kw)
    events.bus.emit = spy

    # A TEMP FOLDER, NOT HIS. This wrote gatetest-cube.stl into the real
    # %APPDATA%\JARVIS\fabrication on every build — and `_pick()` with no name
    # means "the newest thing you made", so for the length of the gate his
    # newest part was a test cube. The gate's own cleanup was the only thing
    # standing between a failed run and a cube he could not explain.
    import tempfile
    from pathlib import Path as _P
    _real_work_dir = F.work_dir
    _tmp_work = _P(tempfile.mkdtemp(prefix="jarvis-holo-gate-"))
    F.work_dir = lambda: _tmp_work
    work = F.work_dir()
    cube = write_cube(os.path.join(str(work), "gatetest-cube.stl"))

    try:
        # ------------------------------------------------------------- showing
        r = await H.show_hologram(path=cube)
        check("showing a model reports its triangle count", r.get("triangles") == 12, r)
        check("...and its size in millimetres", r.get("size_mm") == [20.0, 20.0, 20.0], r)
        check("...in words he can say", r.get("spoken_size") == "20 by 20 by 20 millimetres", r)
        check("...and says it is on the stage", r.get("on_stage") is True, r)
        check("the HUD is told to open the stage",
              any(k == "hologram" and kw.get("action") == "show" for k, kw in seen), seen)

        # ------------------------------------------------- the geometry boundary
        cur = H.current()
        check("the current model is remembered", cur.get("path", "").endswith(".stl"), cur)
        import meshio
        p = meshio.to_payload(cur["path"])
        # base64 float32 rather than a JSON list of numbers — 7.5 MB against
        # 2.0 MB on a tier-3 mesh, and exact either way.
        import base64
        _pos = base64.b64decode(p["positions_b64"])
        check("the payload is a real mesh", len(_pos) == 12 * 9 * 4, len(_pos))
        check("...with the 12 real edges, not 36", p["edges"] == 12, p["edges"])

        seen.clear()
        r = await H.hide_hologram()
        check("hiding clears it", r.get("on_stage") is False)
        check("...and tells the HUD",
              any(k == "hologram" and kw.get("action") == "hide" for k, kw in seen), seen)
        check("...and forgets the model, so the endpoint serves nothing",
              not H.current().get("path"), H.current())

        # ----------------------------------------------------------- refusals
        d = tempfile.mkdtemp()
        notmesh = os.path.join(d, "notes.txt")
        open(notmesh, "w").write("hello")
        for label, kwargs in [
            ("a path that does not exist", {"path": os.path.join(d, "nope.stl")}),
            ("a file that is not an STL", {"path": notmesh}),
        ]:
            out = await H.show_hologram(**kwargs)
            check(f"{label} is a sentence", isinstance(out.get("error"), str) and out["error"], out)

        # A NAME nothing matches is different from a PATH that does not exist,
        # and stopped being a plain error in phase D. "I don't have a model to
        # project" is true and useless when he has just named a thing; the
        # obvious next move is to offer to make one, with the estimate attached.
        # It is still a sentence when there is no technique that could make it.
        out = await H.show_hologram(name="no-such-part")
        asked = out.get("_ask")
        check("a name nothing matches offers to make it, or says so",
              bool(asked) or isinstance(out.get("error"), str), out)
        if asked:
            check("...saying how long it would take",
                  "second" in asked["question"] or "minute" in asked["question"],
                  asked["question"])
            check("...and asking rather than starting",
                  asked["question"].rstrip().endswith("?"), asked["question"])
            check("...and nothing was made yet", not out.get("on_stage"), out)

        broken = os.path.join(str(work), "gatetest-broken.stl")
        with open(broken, "wb") as fh:
            fh.write(b"\0" * 80 + struct.pack("<I", 9999))     # claims 9999, holds none
        out = await H.show_hologram(path=broken)
        check("a truncated STL is a sentence, not a crash",
              isinstance(out.get("error"), str) and out["error"], out)

        # --------------------------------------------------------- the tier
        from tools.registry import registry
        H.register_all()
        t = registry._tools.get("show_hologram")
        check("show_hologram is registered", t is not None)
        check("...at SAFE, because opening a panel has no side effect",
              t is not None and t.risk.value == "safe",
              "the tier must describe what the handler DOES")
        check("...and its description steers 'show me X' away from it",
              t is not None and "show_images" in t.description,
              "without this the model reaches for a hologram when he wanted pictures")
    finally:
        events.bus.emit = real_emit
        F.work_dir = _real_work_dir
        for f in ("gatetest-cube.stl", "gatetest-broken.stl"):
            try:
                os.remove(os.path.join(str(work), f))
            except OSError:
                pass

    # THE STAGE MUST READ EVERYTHING THE PARSER READS. `meshio` learned OBJ and
    # tier 5 started fetching OBJ, and this still tested `suffix == ".stl"` in
    # three places — so the chain was search, find, download, parse, measure,
    # succeed, and then "I don't have a model to project, sir". A guard firing
    # on something the rest of the system already accepted, reported as though
    # nothing existed.
    import meshio as _mi
    import tools.holo_tools as _ht
    _src = open(_ht.__file__, encoding="utf-8").read()
    check("the stage accepts every format the parser does",
          '.suffix.lower() != ".stl"' not in _src
          and "meshio.READABLE" in _src)
    check("...and looks for a downloaded model by every one of them",
          _src.count("for ext in meshio.READABLE") >= 1
          and ".obj" in _mi.READABLE)

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
