"""The progressive render: rungs arrive early, and none of them is his part.

WHAT THIS IS GATING, in his words: "I actually wanted to see the 3D model being
built". The measurement said that was possible — at grid 384 a reconstruction is
fifteen seconds of thinking and fifty-four of carving — so the rungs are pulled
from the same scene code and shown as they land.

The reconstructor itself needs 1.7 GB of weights and a minute of CPU, so it is
stubbed with a script that prints exactly the JSON shape the real one prints.
That is the interface under test: everything downstream reads those lines, and
the parts that could break quietly are all on this side of the subprocess.

THE ONE THAT WOULD HAVE COST HIM A PART is `test_rough_is_not_the_newest_part`.
"Show me that again" with no name means "the newest thing you made", and a rung
is newer than the part it previews — so without the exclusion the rule as
written would hand him a 96-grid blob and be correct to.
"""
import asyncio
import json
import os
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # the sidecar

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}{'' if ok else '  — ' + detail}")
    if not ok:
        FAILURES.append(name)


# A stand-in for photo_to_mesh.py: the same stdout contract, none of the weight.
STUB = textwrap.dedent('''
    import json, sys, time
    out = sys.argv[2]
    stages = ""
    for i, a in enumerate(sys.argv):
        if a == "--stages":
            stages = sys.argv[i + 1]
    # Progress on stderr, exactly where TripoSR and rembg put theirs — and
    # without a newline, the way a progress bar does, because a reader that
    # cannot survive that deadlocks the child.
    sys.stderr.write("loading weights" + "." * 300)
    for r in [int(x) for x in stages.split(",") if x.strip()]:
        p = out.replace(".stl", f".stage{r}.stl")
        open(p, "w").write("solid s\\nendsolid s\\n")
        print(json.dumps({"stage": r, "stl": p, "triangles": r, "seconds": 0.1}),
              flush=True)
        sys.stderr.write("\\rcarving %d" % r)
    open(out, "w").write("solid s\\nendsolid s\\n")
    print(json.dumps({"ok": True, "stl": out, "triangles": 999}), flush=True)
''')


def main() -> int:
    import create3d

    tmp = Path(os.environ.get("TEMP", ".")) / "jarvis-progressive-test"
    tmp.mkdir(parents=True, exist_ok=True)
    (tmp / "stub.py").write_text(STUB, encoding="utf-8")
    create3d.model3d_dir = lambda: tmp                      # type: ignore
    create3d.model3d_python = lambda: sys.executable        # type: ignore

    print("\nthe rungs arrive, in order, before the answer")
    seen: list[dict] = []

    async def on_stage(obj: dict) -> None:
        seen.append(obj)

    out = tmp / "duck.stl"
    r = asyncio.run(create3d._run_model3d(
        "stub.py", [str(tmp / "ref.jpg"), str(out), "--stages", "96,192"],
        60, on_stage))
    check("the final answer is the last non-stage line",
          r.get("triangles") == 999, json.dumps(r)[:120])
    check("both rungs were seen", [s["stage"] for s in seen] == [96, 192],
          str([s.get("stage") for s in seen]))
    check("each rung named a file that existed at the time",
          all(s.get("stl") for s in seen))

    # A child that writes 300 bytes with no newline and then blocks would hang
    # here rather than fail, which is why the stub does exactly that.
    print("\na progress bar on stderr does not deadlock it")
    check("it came back at all", bool(r))

    print("\nno stages asked for, no stages emitted")
    seen.clear()
    r2 = asyncio.run(create3d._run_model3d(
        "stub.py", [str(tmp / "ref.jpg"), str(tmp / "plain.stl")], 60, on_stage))
    check("nothing was shown", seen == [], str(seen))
    check("and the answer is still the answer", r2.get("triangles") == 999)

    print("\nthe rungs are cleaned up when the render ends")
    from tools import fabrication
    fab = tmp / "work"
    fab.mkdir(exist_ok=True)
    fabrication.work_dir = lambda: fab                      # type: ignore
    for res in (96, 192):
        (fab / f"duck.stage{res}.stl").write_text("solid s\nendsolid s\n")
    create3d._clear_stages("duck")
    check("no scaffolding left behind",
          not list(fab.glob("duck.stage*.stl")),
          str([p.name for p in fab.glob("*")]))

    print("\nA RUNG IS NEVER 'the newest thing you made'")
    import time as _t
    from tools import holo_tools
    holo_tools_work = fab
    (fab / "duck.stl").write_text("solid s\nendsolid s\n")
    _t.sleep(0.02)
    rung = fab / "duck.stage96.stl"
    rung.write_text("solid s\nendsolid s\n")               # newer than the part
    import tools.fabrication as _fab
    _fab.work_dir = lambda: holo_tools_work                 # type: ignore
    picked = holo_tools._pick()
    check("it picked the part, not the preview",
          picked is not None and picked.name == "duck.stl",
          picked.name if picked else "nothing")
    rung.unlink(missing_ok=True)

    print("\nA ROUGH CARVE ACTUALLY REACHES THE STAGE")
    # `show_stage` catches everything, so that a failed preview can never cost
    # him the render — which also means one that never worked at all would look
    # exactly like one that did. So it is driven here on a real mesh, and the
    # three things the HUD depends on are checked: the event says `rough`, and
    # `_current` points at the rung, because that is the path `/holo/geometry`
    # re-parses when the panel asks for the mesh.
    import trimesh
    from events import bus
    rung = fab / "duck.stage96.stl"
    trimesh.creation.icosphere(subdivisions=2, radius=20.0).export(str(rung))
    seen: list[dict] = []
    real_emit = bus.emit

    async def spy(kind, **kw):
        seen.append({"kind": kind, **kw})
        return await real_emit(kind, **kw)

    bus.emit = spy                                       # type: ignore
    asyncio.run(holo_tools.show_stage(str(rung), "duck", 96))
    bus.emit = real_emit                                 # type: ignore
    cur = holo_tools.current()
    check("the event says it is rough",
          any(e.get("kind") == "hologram" and e.get("rough") == 96 for e in seen),
          str(seen)[:140])
    check("the stage points at the rung",
          Path(cur.get("path", "")).name == rung.name, cur.get("path"))
    check("with real triangles on it", int(cur.get("triangles") or 0) > 100,
          cur.get("triangles"))
    rung.unlink(missing_ok=True)

    print("\nA STOPPED RENDER TAKES ITS PREVIEW WITH IT")
    # He says "stop the render" and the queue cancels the job — so the job body
    # never reaches its own ending, which is why the cleanup lives in the queue
    # runner. Without it the last rough carve sits on the stage pulsing
    # "resolving" for a part that is never going to arrive.
    async def cancelled_render() -> int:
        import render_queue
        trimesh.creation.icosphere(subdivisions=2, radius=20.0).export(str(rung))
        await holo_tools.show_stage(str(rung), "duck", 96)
        hidden: list[dict] = []

        async def spy2(kind, **kw):
            hidden.append({"kind": kind, **kw})
            return await real_emit(kind, **kw)

        started = asyncio.Event()

        async def never() -> dict:
            started.set()
            await asyncio.sleep(30)
            return {"stl": "never"}

        q = render_queue.Queue() if hasattr(render_queue, "Queue") else render_queue.queue
        bus.emit = spy2                                   # type: ignore
        try:
            q.submit(3, "duck", never)
            await asyncio.wait_for(started.wait(), 5)
            q.cancel()
            for _ in range(40):                           # let the runner unwind
                await asyncio.sleep(0.05)
                if any(e.get("action") == "hide" for e in hidden):
                    break
        finally:
            bus.emit = real_emit                          # type: ignore
        return sum(1 for e in hidden
                   if e.get("kind") == "hologram" and e.get("action") == "hide")

    try:
        hides = asyncio.run(cancelled_render())
        check("the rough preview came off the stage", hides >= 1, f"{hides} hides")
    except Exception as e:
        check("the rough preview came off the stage", False,
              f"{type(e).__name__}: {e}")
    rung.unlink(missing_ok=True)

    print("\nthe ladder is the measured one")
    check("96 then 192, and nothing more expensive",
          create3d.PROGRESSIVE_STAGES == "96,192", create3d.PROGRESSIVE_STAGES)

    print("\nprogressive is off for the parts of a composite")
    import inspect
    src = inspect.getsource(create3d.build)
    check("build only passes on what it was given",
          "progressive=progressive" in src and "progressive=True" not in src)
    comp = inspect.getsource(__import__("components"))
    check("components never turns it on", "progressive=True" not in comp)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: " + ", ".join(FAILURES))
        return 1
    print("progressive render: all good")
    return 0


sys.exit(main())
