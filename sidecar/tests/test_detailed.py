"""A DETAILED render: the slow volumetric reconstruction, only when he asks.

Offline. What is gated: the adjective is read out of his sentence and kept
out of the object's name; the estimate uses its own key (minutes, not a
minute) so the cost question is honest; the backend is chosen by
installation, and its absence is said rather than hidden.

Run: python tests/test_detailed.py
"""
import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "detailed.db"))

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


async def main() -> int:
    from brain.skills import slots_holo_make
    import create3d
    import render_estimates as est

    print("\n-- the adjective is the whole signal --")
    for said, want_desc in (
            ("make me a detailed model of a dragon", "a dragon"),
            ("create a high quality 3d model of a rubber duck", "a rubber duck"),
            ("make a proper hologram of the arc reactor", "the arc reactor"),
            ("render a mug in full detail", "a mug"),
            ("take your time and make me a dragon", "a dragon")):
        s = slots_holo_make(said)
        check(f"{said!r} is detailed", s.get("detailed") is True, s)
        # The article may go with the asking verb ("render a mug" -> "mug");
        # what matters is that the adjective did not stay in the name.
        got = (s.get("description") or "").lower()
        check(f"...and the object is {want_desc!r}",
              got in (want_desc, want_desc.split(" ", 1)[-1]), got)
    for said in ("make me a model of a dragon", "create a 3d model of a rubber duck",
                 "make me a 20 millimetre cube"):
        s = slots_holo_make(said)
        check(f"{said!r} is not detailed", "detailed" not in s, s)

    print("\n-- its own clock --")
    check("the detailed key has a seed of minutes", est.SEED.get(8, 0) >= 240, est.SEED.get(8))
    check("...and the ordinary photo tier is under three", est.SEED[3] < 180)

    print("\n-- the backend follows the install --")
    tmp = tempfile.mkdtemp()
    pic = os.path.join(tmp, "p.png")
    open(pic, "wb").write(b"\x89PNG\r\n\x1a\n")
    calls = []

    async def fake_run(script, args, timeout, on_stage=None, python=None):
        calls.append((script, python, args))
        return {"ok": True, "stl": args[1], "triangles": 10, "size_mm": [1, 1, 1]}

    real_run, real_avail, real_det = create3d._run_model3d, create3d.available, create3d.detailed_python
    real_wd = None
    try:
        from tools import fabrication
        real_wd = fabrication.work_dir
        fabrication.work_dir = lambda: __import__("pathlib").Path(tmp)
        create3d._run_model3d = fake_run
        create3d.available = lambda: {**real_avail(), 3: True, 4: True, "detailed": False}
        create3d.detailed_python = lambda: None
        r = await create3d.from_photo(pic, "p", detailed=True)
        check("not installed: the ordinary reconstruction runs",
              calls and calls[-1][0] == "photo_to_mesh.py", calls)
        check("...and he is told it was the ordinary one", "instruction" in r, r)
        calls.clear()
        create3d.detailed_python = lambda: r"C:\fake\hy3d\python.exe"
        r = await create3d.from_photo(pic, "p", detailed=True, progressive=True)
        check("installed: the Hunyuan backend runs", calls and calls[-1][0] == "hy3d_to_mesh.py", calls)
        check("...under its own interpreter", calls and calls[-1][1] == r"C:\fake\hy3d\python.exe")
        check("...with rough rungs first", calls and "64,128" in calls[-1][2], calls[-1][2] if calls else None)
        check("...and the result says so", r.get("detailed") is True and r.get("backend"), r)
        calls.clear()
        r = await create3d.from_photo(pic, "p")
        check("plain request: TripoSR as before", calls and calls[-1][0] == "photo_to_mesh.py", calls)
    finally:
        create3d._run_model3d, create3d.available, create3d.detailed_python = real_run, real_avail, real_det
        if real_wd is not None:
            fabrication.work_dir = real_wd

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
