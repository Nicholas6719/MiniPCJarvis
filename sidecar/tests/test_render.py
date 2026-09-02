"""Phase D: the render queue, the estimate, and the question before a long job.

Offline. The heavy tiers are not installed on this machine and must therefore be
SKIPPED LOUDLY rather than quietly passing — the Evolution's phase 5 skip hid a
real parser bug, and a green tick for something that never ran teaches the suite
means more than it does.

WHAT THIS IS REALLY GUARDING.

  * THE ESTIMATE IS HONEST. A seed until it has been measured, the MEDIAN of real
    runs afterwards, and it says so while it is still a guess. He plans around
    these numbers; one invented at the moment of asking is worse than none.
  * HE IS ASKED BEFORE A LONG JOB, AND NOT BEFORE A SHORT ONE. His correction:
    "maybe I don't want to do it if it's going to take over an hour". Asking
    about six seconds is friction, so tier 2 — a traced contour, no model
    involved — stays under the threshold, and that is asserted in both
    directions.
  * DECLINING RUNS NOTHING. The "leave it" branch has no tool at all, so there is
    no half-written file to tidy up.
  * A TURN STILL ANSWERS WHILE A RENDER RUNS. The whole point of the queue. If
    the work ever lands on the event loop this is what goes red.

Run: python tests/test_render.py
"""
import asyncio
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "render.db"))

fails = []
skips = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


def skip(name, why):
    print(f"  SKIP  {name}  ({why})")
    skips.append((name, why))


async def main() -> int:
    import create3d
    import render_estimates as est
    from render_queue import RenderQueue

    # ------------------------------------------------------------ estimates
    est._PATH = __import__("pathlib").Path(tempfile.mkdtemp()) / "times.json"

    check("an unmeasured tier uses its seed",
          est.estimate(4) == est.SEED[4], est.estimate(4))
    check("...and says it has never done one",
          "not done one" in est.confidence_note(4), est.confidence_note(4))
    for s in (30.0, 40.0, 300.0):        # one outlier, deliberately
        est.record(4, s)
    check("a measured tier uses the MEDIAN, not the mean",
          est.estimate(4) == 40.0,
          f"{est.estimate(4)} — the mean would be 123 and one cold start would "
          f"poison every estimate for the rest of the day")
    check("...and stops saying it has never done one", est.confidence_note(4) == "")
    check("only the last few runs count", est.KEEP <= 12)
    est.record(4, -5)
    check("a nonsense duration is not recorded", est.measured(4) == 3)

    check("a short job is said as a person says it", est.spoken(3) == "a few seconds")
    check("a minute is a minute", est.spoken(62) == "about a minute")
    check("an hour is not '3600 seconds'",
          "hour" in est.spoken(3600), est.spoken(3600))
    check("no estimate is ever spoken to the decimal",
          "." not in est.spoken(74.3), est.spoken(74.3))

    # THE THRESHOLD, in both directions. Tier 2 is the one that must not ask —
    # tracing a contour and extruding it involves no model at all. Tier 1 was
    # seeded under the threshold on a guess and MEASURED at 28 seconds on the
    # first real run, because the slow part is llama-server writing the source
    # rather than OpenSCAD building it; half a minute deserves a heads-up.
    check("tier 2 is under the ask threshold",
          est.SEED[2] <= est.ask_threshold(),
          f"{est.SEED[2]} vs {est.ask_threshold()} — asking to spend six seconds "
          f"is friction, not courtesy")
    check("tier 1 is over it, as measured", est.SEED[1] > est.ask_threshold(),
          f"{est.SEED[1]}")
    check("tier 3 is over it", est.SEED[3] > est.ask_threshold())
    check("tier 4 is well over it", est.SEED[4] > est.ask_threshold() * 5)

    # ----------------------------------------------------------- the tiers
    for desc, img, want in (("a bracket 40 mm wide", "", 1),
                            ("a plate with a hole", "", 1),
                            ("a 20 mm cube", "", 1),
                            ("the emblem", "logo.png", 2),
                            ("this chair", "photo.jpg", 3),
                            ("a dragon", "", 4),
                            ("a spaceship", "", 4)):
        got = create3d.choose_tier(desc, img)
        check(f"{desc!r}{' + a picture' if img else ''} is tier {want}",
              got == want, got)
    check("every tier explains itself",
          all(create3d.TIER_NOTE.get(t) for t in create3d.TIERS))
    check("...and only tier 1 promises voice editing",
          "voice" in create3d.TIER_NOTE[1]
          and not any("voice" in create3d.TIER_NOTE[t] for t in (2, 3, 4)),
          "a tier-3 mesh has no parameters to change, and saying otherwise "
          "wastes his time on a request that cannot be honoured")

    # ------------------------------------------------------------ the queue
    q = RenderQueue()
    order = []

    def slow(tag, secs=0.25):
        def run():
            time.sleep(secs)             # BLOCKING on purpose: it must not be awaited
            order.append(tag)
            return {"ok": True, "tag": tag}
        return run

    check("nothing is running to start with", q.busy is False)
    check("...and status says so", q.status()["busy"] is False)

    t0 = time.time()
    a = q.submit(1, "the first", slow("a"))
    b = q.submit(1, "the second", slow("b"))
    check("submitting returns immediately", time.time() - t0 < 0.05,
          f"{time.time() - t0:.3f}s — the whole point is that he keeps talking")
    check("...with an estimate attached", a.get("estimate_s", 0) > 0, a)
    check("...spoken, not in seconds", "second" in a.get("estimate_spoken", ""), a)
    check("the second one queues behind the first", b.get("queued_behind") == 1, b)

    # A TURN MUST STILL ANSWER. This is the assertion that goes red if the work
    # ever lands on the event loop: a status call while a render runs has to come
    # back in microseconds, not when the render finishes.
    await asyncio.sleep(0.05)
    t0 = time.time()
    s = q.status()
    answered_in = time.time() - t0
    check("a question is answered while a render runs", answered_in < 0.02,
          f"{answered_in:.4f}s")
    check("...and it knows what it is doing", s.get("busy") is True, s)
    check("...and how much longer", "remaining_spoken" in s, s)

    # Wait on the QUEUE going idle, not on the jobs appending: a job records
    # itself from inside its own thread, before the queue has finished with it,
    # so `len(order) == 2` is true a moment before `busy` goes false. Waiting on
    # the wrong one of those made this fail about half the time.
    for _ in range(300):
        if not q.busy and len(order) == 2:
            break
        await asyncio.sleep(0.02)
    check("both jobs ran", order == ["a", "b"], order)
    check("...one at a time, in order", q.busy is False)

    # ---------------------------------------------------------- cancelling
    q2 = RenderQueue()
    q2.submit(1, "a long one", slow("long", 5.0))
    await asyncio.sleep(0.1)
    r = q2.cancel()
    check("a running render can be stopped", r.get("cancelled") is True, r)
    check("...and is named in the answer", r.get("label") == "a long one", r)
    for _ in range(100):
        if not q2.busy:
            break
        await asyncio.sleep(0.02)
    check("...and the queue is free afterwards", q2.busy is False)
    check("cancelling nothing is a sentence, not an error",
          RenderQueue().cancel().get("cancelled") is False)

    # A cancelled job must NOT calibrate the estimate: it did not finish, so its
    # duration says nothing about how long the work takes.
    before = est.measured(1)
    q3 = RenderQueue()
    q3.submit(1, "doomed", slow("doomed", 3.0))
    await asyncio.sleep(0.1)
    q3.cancel()
    await asyncio.sleep(0.2)
    check("a cancelled render does not poison the estimate",
          est.measured(1) == before, (before, est.measured(1)))

    # ------------------------------------------------ the question, and no-ing it
    import clarify
    from tools import fabrication, holo_tools, render_tools
    fabrication.register_all()
    holo_tools.register_all()
    render_tools.register_all()

    # Tier 2 is the short one, so it is the one that must not ask. (Tier 1 is a
    # llama-server round trip and measured 28 s, so it asks — see the seed.)
    r = await render_tools.make_hologram(description="the emblem",
                                         image_path=__file__)
    check("a short job does not ask, it just starts",
          r.get("_ask") is None and r.get("started") is True, r)
    check("...and reports which tier made it", r.get("tier") == 2, r)
    await render_tools.cancel_render()

    # An UNINSTALLED tier is refused before he is asked to wait for it. Being
    # asked "about three minutes, shall I?" and only then told the model is not
    # installed is the worst possible order for those two sentences.
    r = await render_tools.make_hologram(description="a dragon")
    check("an uninstalled tier is refused rather than asked about",
          r.get("unavailable") is True and r.get("_ask") is None, r)

    # The ask itself, on a tier that IS installed. Measured slow here on purpose:
    # this is exactly how the estimate is meant to move — on a machine where the
    # work really did take a minute, JARVIS starts asking without anyone touching
    # a constant.
    for _ in range(3):
        est.record(2, 60.0)
    r = await render_tools.make_hologram(description="the emblem",
                                         image_path=__file__)
    ask = r.get("_ask")
    check("a job that has MEASURED slow now asks first", ask is not None, r)
    if ask:
        check("...saying how long", "minute" in ask["question"] or "second" in ask["question"],
              ask["question"])
        check("...and asking, not announcing", ask["question"].rstrip().endswith("?"),
              ask["question"])
        check("...and answering yes runs the same tool, already confirmed",
              ask["tool"] == "make_hologram" and ask["args"].get("confirmed") is True, ask)

        amb = clarify.approval(ask["subject"], ask["question"], ask["tool"],
                               ask["args"], render=lambda a, r: "ok")
        check("the question has a yes and a no", len(amb.branches) == 2)
        check("...neither of which runs before he answers",
              all(not b.speculative for b in amb.branches))
        no = [b for b in amb.branches if b.label == "leave it"][0]
        check("...and declining runs NOTHING at all", no.tool == "",
              "nothing half-written is left in the work folder")
        check("a branch with no tool does not make the question unsafe",
              clarify.validate(amb, lambda t: False) is True)
        picked = clarify.choose(clarify.Pending(amb), "no thanks")
        check("'no thanks' is understood as an answer", picked is not None, picked)

    r = await render_tools.make_hologram()
    check("nothing to make is a question back, not a crash", bool(r.get("error")), r)

    # ------------------------------------------------- the heavy tiers, honestly
    avail = create3d.available()
    for tier, what in ((3, "photo-to-mesh"), (4, "text-to-mesh")):
        if avail.get(tier):
            skip(f"tier {tier} live run", "installed — exercised by the live script")
        else:
            r = await (create3d.from_photo("x.png") if tier == 3
                       else create3d.from_text("a dragon"))
            check(f"tier {tier} says it is not installed", r.get("unavailable") is True, r)
            check("...naming where it would live", "model3d" in (r.get("error") or ""),
                  r.get("error"))
            check("...and does NOT fall back to another tier",
                  r.get("tier") == tier and not r.get("stl"),
                  "handing him a different technique's output would look like "
                  "success and be wrong")
            skip(f"tier {tier} produces a mesh",
                 f"{what} is not installed at {create3d.model3d_dir()}")

    # ------------------------------------------------- a generation that failed
    # Asked for "a hex spacer 12 mm tall" the local model produced something
    # 0.4 mm wide, and the pipeline measured it, projected it and reported
    # success — the only clue was a dimension rounding to zero in the HUD. A
    # sliver is a failed generation, not a part, and it has to say so in the same
    # breath as "ready".
    check("half a millimetre is the line", create3d.MIN_SENSIBLE_MM == 0.5)
    check("...which is under the minimum printable wall",
          create3d.MIN_SENSIBLE_MM < 0.8,
          "a legitimately thin plate must never be called degenerate")

    # ...and the condition actually fires on a mesh shaped like the one that got
    # through. Checking the constant alone would pass even if nothing ever read it.
    import meshio

    def write_stl(path, sx, sy, sz):
        a, b, c, d = (0, 0, 0), (sx, 0, 0), (sx, sy, 0), (0, sy, 0)
        e, f, g, h = (0, 0, sz), (sx, 0, sz), (sx, sy, sz), (0, sy, sz)
        tris = [(a, c, b), (a, d, c), (e, f, g), (e, g, h), (a, b, f), (a, f, e),
                (d, g, c), (d, h, g), (a, e, h), (a, h, d), (b, c, g), (b, g, f)]
        with open(path, "w", encoding="ascii") as fh:
            fh.write("solid s\n")
            for t in tris:
                fh.write("facet normal 0 0 0\n outer loop\n")
                for v in t:
                    fh.write(f"  vertex {v[0]:.4f} {v[1]:.4f} {v[2]:.4f}\n")
                fh.write(" endloop\nendfacet\n")
            fh.write("endsolid s\n")
        return path

    d_tmp = tempfile.mkdtemp()
    sliver = write_stl(os.path.join(d_tmp, "sliver.stl"), 0.4, 2.0, 12.0)
    size = meshio.describe(sliver)["size_mm"]
    check("a 0.4 mm sliver is recognised as one",
          min(size) < create3d.MIN_SENSIBLE_MM, size)
    thin = write_stl(os.path.join(d_tmp, "thin.stl"), 40.0, 30.0, 0.6)
    check("...and a legitimate 0.6 mm plate is not",
          min(meshio.describe(thin)["size_mm"]) >= create3d.MIN_SENSIBLE_MM,
          meshio.describe(thin)["size_mm"])

    # ------------------------------------------------------------- tier 2, real
    try:
        import cv2
        import numpy as np
        img = np.zeros((200, 200), dtype="uint8")
        cv2.circle(img, (100, 100), 70, 255, -1)
        p = os.path.join(tempfile.mkdtemp(), "disc.png")
        cv2.imwrite(p, img)
        pts = create3d.trace_outline(p)
        check("a shape in a picture is traced", pts is not None and len(pts) > 6,
              None if pts is None else len(pts))
        check("...and simplified rather than traced pixel by pixel",
              pts is not None and len(pts) < 200, None if pts is None else len(pts))
        blank = os.path.join(tempfile.mkdtemp(), "blank.png")
        cv2.imwrite(blank, np.zeros((50, 50), dtype="uint8"))
        check("a picture with nothing in it traces nothing, rather than inventing a shape",
              create3d.trace_outline(blank) is None)
        check("an unreadable file traces nothing",
              create3d.trace_outline(os.path.join(tempfile.mkdtemp(), "nope.png")) is None)
    except ImportError:
        skip("tier 2 outline tracing", "opencv is not importable here")

    print()
    if skips:
        print(f"  {len(skips)} case(s) SKIPPED — phase D's gate was NOT fully exercised:")
        for name, why in skips:
            print(f"     - {name}: {why}")
    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}"
          f"{f' ({len(skips)} skipped)' if skips else ''}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
