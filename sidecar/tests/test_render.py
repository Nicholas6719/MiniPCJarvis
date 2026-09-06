"""Phase D: the render queue, the estimate, and the question before a long job.

Offline. Tiers 3 and 4 ARE installed now, but running them takes 33 and 55
seconds, which is not a build gate — so what is checked here is everything around
the model, and the run itself is skipped LOUDLY and done by the live script. A
green tick for something that never ran teaches the suite means more than it
does; the Evolution's phase 5 skip hid a real parser bug and that lesson stands.

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
import io
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
    check("tier 4 is over it too", est.SEED[4] > est.ask_threshold())
    check("...and tier 4 costs more than tier 3, since it is tier 3 plus a search",
          est.SEED[4] > est.SEED[3], (est.SEED[3], est.SEED[4]))

    # ------------------------------------------------- the parametric templates
    # This is now the path most requests take, so it carries most of the risk.
    # The dangerous failure is not a miss — a miss just wakes the model — it is a
    # FALSE MATCH: a confident, exact, wrong part produced instantly. So the
    # decline cases matter more than the match cases.
    import parts_library as PL

    for said, want in (
            ("a 20 mm cube", ["cube([20, 20, 20])"]),
            ("a plate 40 by 30 by 6 mm", ["cube([40, 30, 6])"]),
            ("a cylinder 20 mm diameter 30 mm tall", ["cylinder(d = 20, h = 30"]),
            ("a sphere 25 mm diameter", ["sphere(d = 25)"]),
            ("a washer 20 mm outer 8 mm inner 2 mm thick",
             ["cylinder(d = 20, h = 2)", "d = 8"]),
            ("a tube 20 mm outer 14 mm inner 40 mm long",
             ["cylinder(d = 20, h = 40)", "d = 14"]),
            ("a hex spacer 12 mm tall", ["h = 12", "$fn = 6"]),
            ("a plate 40 by 30 by 6 mm with a 5 mm hole",
             ["cube([40, 30, 6])", "d = 5"])):
        m = PL.match(said)
        check(f"{said!r} is written from a template", m is not None)
        src = m.source if m else ""
        for frag in want:
            check(f"  ...containing {frag!r}", frag in src, src)

    # DECLINES. Each of these has numbers in it, so a careless matcher would fire.
    # The last four are MORE THAN ONE of something, and every template here makes
    # exactly one body with at most one hole. Two of them were real false matches
    # found by throwing realistic phrasings at it: "a plate with 4 mounting holes"
    # came back as a plate with one centred hole, and "a cube 20 mm and a plate
    # 30 by 30 by 2 mm" came back as just the cube — confident, exact, and not
    # what he asked for, which is the whole failure this library must not have.
    for said in ("a bracket 40 mm wide", "a phone stand", "a dragon",
                 "a gear with 20 teeth", "a plate with rounded corners 40 by 30 by 5 mm",
                 "a case for a raspberry pi 90 by 60 by 30 mm",
                 "a hook 40 mm long", "a knob 30 mm across", "",
                 "a plate with 4 mounting holes 60 by 60 by 5 mm",
                 "a cube 20 mm and a plate 30 by 30 by 2 mm",
                 "two 20 mm cubes", "three spacers 10 mm tall"):
        check(f"{said!r} is left to the model", PL.match(said) is None,
              PL.match(said))

    # ...and a DIGIT is a dimension, not a count. The first version of the
    # multiple-parts rule read "25 mm sphere" and "a 5 mm hole" as counts and
    # declined both — a fix that broke the thing it was protecting.
    check("a digit before a unit is a dimension, not a count",
          PL.match("a 25 mm sphere") is not None
          and PL.match("a plate 40 by 30 by 6 mm with a 5 mm hole") is not None)

    check("every template sets the curve resolution",
          all("$fn" in (PL.match(s).source if PL.match(s) else "")
              for s in ("a 20 mm cube", "a hex spacer 12 mm tall",
                        "a sphere 25 mm diameter")),
          "OpenSCAD's default gives a 5 mm hole about a dozen segments, and a "
          "bolt does not fit a hexagon")
    # A hole cut exactly flush leaves coincident faces, which is a classic way to
    # hand a slicer a solid that renders fine and slices wrong.
    src = PL.match("a plate 40 by 30 by 6 mm with a 5 mm hole").source
    check("a hole is cut proud of both faces", "6.2" in src and "-0.1" in src, src)

    check("a templated part is tier 0",
          create3d.choose_tier("a 20 mm cube", "") == 0)
    check("...and tier 0 never asks",
          est.SEED[0] <= est.ask_threshold(),
          "nobody wants to be asked permission to spend a fifth of a second")

    # --------------------------------------- did he get what he asked for?
    # Nothing checked this until 2026-09-02. "A hex spacer 12 mm tall" came back
    # 0.4 mm wide, and because it WAS twelve millimetres tall it passed every
    # test there was. Borrowed from TalkCAD: verify the result against the stated
    # spec with a tolerance, and separate what he stated from what we chose.
    import partspec

    check("a stated cube is extracted",
          partspec.extract("a 20 mm cube").get("cube_mm") == 20.0)
    check("three dimensions are extracted",
          partspec.extract("a plate 40 by 30 by 6 mm").get("dims_mm") == [40.0, 30.0, 6.0])
    check("a height is extracted",
          partspec.extract("a hex spacer 12 mm tall").get("height_mm") == 12.0)
    check("a hole is recorded but never asserted",
          "hole_mm_unchecked" in partspec.extract("a plate with a 5 mm hole"),
          "a hole is invisible in the extents; claiming to have checked it "
          "would be worse than not checking")

    def v(said, size):
        return partspec.verify(partspec.extract(said), size, said)

    check("a correct cube passes", v("a 20 mm cube", [20, 20, 20])["ok"] is True)
    check("a 14 mm cube fails", v("a 20 mm cube", [20, 20, 14])["ok"] is False)
    check("...and says both numbers",
          "20" in v("a 20 mm cube", [20, 20, 14])["problems"][0]
          and "14" in v("a 20 mm cube", [20, 20, 14])["problems"][0])
    check("a correct plate passes",
          v("a plate 40 by 30 by 6 mm", [40, 30, 6])["ok"] is True)
    check("a plate 3 mm thick instead of 6 fails",
          v("a plate 40 by 30 by 6 mm", [40, 30, 3])["ok"] is False)
    check("...but the same plate lying on a different axis passes",
          v("a plate 40 by 30 by 6 mm", [30, 6, 40])["ok"] is True,
          "that is an orientation he can turn, not a mistake")

    # THE ONE THAT STARTED IT. Every stated dimension correct, and not a spacer.
    real = v("a hex spacer 12 mm tall", [6, 5.2, 12])
    sliver = v("a hex spacer 12 mm tall", [0.4, 2, 12])
    check("a real hex spacer passes", real["ok"] is True, real)
    check("the 0.4 mm sliver is caught", sliver["ok"] is False, sliver)
    check("...on its proportions, since its height was right",
          "proportions" in sliver["checked"], sliver["checked"])
    check("a lithophane is not called a sliver for being thin",
          v("a lithophane of my photo", [80, 60, 3.8])["ok"] is not False,
          "the things that are meant to be thin are exempt, or the check gets "
          "switched off")
    check("saying nothing measurable is neither pass nor fail",
          v("a bracket", [60, 40, 6])["ok"] is None)

    # ...and the numbers WE chose are declared rather than buried in a comment.
    m = PL.match("a hex spacer 12 mm tall")
    check("a template reports the defaults it picked", bool(m.defaults), m.defaults)
    check("...naming the M3 bore", any("M3" in str(v_) for v_ in m.defaults.values()),
          m.defaults)
    check("a fully specified part defaults nothing",
          not PL.match("a 20 mm cube").defaults)
    say = create3d.spoken_caveats({"chose": m.defaults})
    check("...and they are said out loud", say.startswith("I chose"), say)
    check("a wrong part leads with what is wrong, not with what we chose",
          create3d.spoken_caveats(
              {"spec_problems": ["it came out 3 mm thick"], "chose": m.defaults}
          ).startswith("But"))
    check("a part with nothing to report says nothing",
          create3d.spoken_caveats({}) == "")

    # ------------------------------------------- source that will not compile
    # Measured on the real model, not imagined: asked for three parts with
    # rounded or chamfered edges, only one built. The other two failed as
    # OpenSCAD written like Python — `arm1 = cube([40,20,4]);` — which is a
    # parser error, and OpenSCAD reports it as "syntax error, line 6" with no
    # cause, so feeding its own message back produced the same mistake one line
    # lower. Recognising the pattern here is what lets the retry say the lesson.
    from tools.fabrication import _GEOMETRY_AS_VALUE as GAV
    for src in ("arm1 = cube([40,20,4]);",
                "arm2 = translate([0,20,0]) cube([20,20,4]);",
                "base = union() { arm1; arm2; }",
                "filleted = minkowski() { base; cylinder(r=2,h=0.01); }",
                "    indented = sphere(3);"):
        check(f"caught as geometry-in-a-variable: {src.strip()[:34]}",
              bool(GAV.search(src)))
    # ...and the assignments that are FINE stay fine. A lint that fires on
    # `r = 2;` would rewrite every working part in the library.
    for src in ("r = 2;", "w = 40 - 2*r;", "$fn = 48;", "size = [40,30,5];",
                "name = \"bracket\";", "h = max(1.2, t);",
                "module plate() { cube([10,10,2]); }"):
        check(f"left alone: {src.strip()[:34]}", not GAV.search(src))

    # The prompt has to carry the three corrections that produced 3-of-3 builds,
    # because they are the difference between a part and an apology.
    import inspect as _inspect
    import tools.fabrication as _fab
    body = _inspect.getsource(_fab.generate_part)
    check("the prompt forbids the libraries that are not installed",
          "BOSL2" in body and "Round-Anything" in body)
    check("the prompt says geometry is not a value", "declarative" in body)
    check("the prompt gives the flat-bottomed rounding idiom",
          "THIN CYLINDER" in body and "never" in body)
    # A REASONING model spends max_tokens on thinking first. At 700 the chamfer
    # request returned 2,443 characters of reasoning and no code at all.
    check("the source budget leaves room to think first",
          "max_tokens=2000" in body, body[body.find("max_tokens"):][:40])

    # ----------------------------------------------------------- the tiers
    for desc, img, want in (("a bracket 40 mm wide", "", 1),
                            ("a plate with a hole", "", 1),
                            ("a 20 mm cube", "", 0),          # a template, not the model
                            ("the emblem", "logo.png", 2),
                            # a picture defaults to the RELIEF, which is instant and
                            # printable; a real reconstruction is only on request
                            ("this chair", "photo.jpg", 2),
                            ("scan this chair", "photo.jpg", 3),
                            # SOMEBODY HAS ALREADY MADE THESE, AND PROPERLY.
                            # Reconstruction from a photograph gives a lump; the
                            # web has real sculptures. Tier 5 falls back to the
                            # tier it would otherwise have used, so being wrong
                            # costs a search rather than a worse object.
                            ("a dragon", "", 5),
                            ("a spaceship", "", 5),
                            # His headline case. This reached tier 1 — OpenSCAD
                            # writing a suit of armour — because `_ORGANIC`
                            # knows "suit" and cannot know that a Mark 3 is one.
                            ("render iron man mark 3", "", 5),
                            # ...and these must NOT move. A dimensioned part is
                            # exact and editable; the web cannot beat that.
                            ("a bracket 60 mm long", "", 1),
                            ("the spider-man emblem", "", 2)):
        got = create3d.choose_tier(desc, img)
        check(f"{desc!r}{' + a picture' if img else ''} is tier {want}",
              got == want, got)
    # A NOTE THAT IS SPOKEN BEFORE THE WORK RUNS MUST DESCRIBE THE ATTEMPT.
    # Tier 5's said "somebody sculpted this and I've fetched their file" — at
    # submission, before anything had been searched for — and was contradicted
    # forty seconds later by "nobody had one to download, so I built this one".
    check("tier 5's note does not claim a model was found before it looks",
          "I'll look for" in create3d.TIER_NOTE[5]
          and "fetched their file" not in create3d.TIER_NOTE[5],
          create3d.TIER_NOTE[5])
    check("every tier explains itself",
          all(create3d.TIER_NOTE.get(t) for t in create3d.TIERS))
    check("...and only tier 1 promises voice editing",
          "voice" in create3d.TIER_NOTE[1]
          and not any("voice" in create3d.TIER_NOTE[t] for t in (2, 3, 4, 5)),
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
    check("...and the two agree about the word 'busy'",
          q.busy == q.status()["busy"],
          "status() counted a queued job as busy and the property did not, so "
          "anything waiting on `not busy` stopped waiting before work began")

    # A job that is QUEUED but not yet picked up still counts as busy. Reporting
    # "nothing is rendering" one second after he asked for something is a lie he
    # would catch immediately.
    q0 = RenderQueue()
    q0._jobs.append(__import__("render_queue").Job(
        id="x", tier=1, label="the waiting one", run=lambda: {}, estimate_s=5.0))
    s0 = q0.status()
    check("a queued-but-not-started job still reads as busy", s0["busy"] is True, s0)
    check("...by both names for it", q0.busy is True, q0.busy)
    check("...and says it is about to start", s0.get("starting") is True, s0)
    check("...and names it", s0.get("label") == "the waiting one", s0)
    q0._jobs.clear()

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

    # A JOB SUBMITTED JUST AS THE QUEUE EMPTIES MUST STILL RUN. The pump used to
    # be restarted only when `self._pump.done()` was true — but the drain
    # coroutine passes its `while self._jobs` check, finds nothing and begins
    # returning, and during that window the task is not yet done(). A job
    # appended right then saw a live pump and sat in the queue forever. It looked
    # like a render that never started, and a "stop that" which said nothing was
    # running while a job was plainly queued.
    q4 = RenderQueue()
    q4.submit(1, "the first", slow("x", 0.05))
    for _ in range(400):                      # right up to the edge of idle
        if not q4.busy:
            break
        await asyncio.sleep(0.005)
    ran2 = []
    q4.submit(1, "the second", lambda: ran2.append(1) or {"ok": True})
    for _ in range(300):
        if ran2:
            break
        await asyncio.sleep(0.02)
    check("a job submitted as the queue empties is not stranded", ran2 == [1],
          "the pump did not restart")

    # THE CALIBRATION MUST LEARN THE TIER THAT RAN, not the one predicted. They
    # usually agree — but a parametric template that fails to build falls through
    # to the model, and a 27-second run filed under tier 0 would drag its median
    # from a fifth of a second up to a wait he would then be asked about.
    qc = RenderQueue()
    est.record(0, 0.4)
    before0, before1 = est.measured(0), est.measured(1)
    # It must take a MEASURABLE moment: `record` correctly refuses a duration of
    # zero, and an instant lambda finishes inside one clock tick on Windows.
    def mislabelled():
        time.sleep(0.05)
        return {"ok": True, "tier": 1}

    qc.submit(0, "mislabelled", mislabelled)
    for _ in range(300):
        if not qc.busy:
            break
        await asyncio.sleep(0.02)
    await asyncio.sleep(0.2)
    check("a job that reports a different tier calibrates THAT tier",
          est.measured(1) == before1 + 1 and est.measured(0) == before0,
          (before0, est.measured(0), before1, est.measured(1)))

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

    # ------------------------------- "stop that" must actually stop the child
    # Cancelling the awaiting task does NOT touch a subprocess: proven on
    # 2026-09-02 by watching the process survive. A cancelled tier-3 render would
    # have kept 1.7 GB of TripoSR weights and a core busy for another half minute
    # after he was told it had stopped — he would have heard the fans.
    import psutil
    from tools.fabrication import _run

    MARK = "jarvis_cancel_gate"

    def probe_pids():
        found = []
        for p in psutil.process_iter(["cmdline"]):
            try:
                if MARK in " ".join(p.info["cmdline"] or []):
                    found.append(p.pid)
            except Exception:
                pass
        return found

    task = asyncio.ensure_future(
        _run([sys.executable, "-c", f"# {MARK}\nimport time; time.sleep(20)"], 60))
    for _ in range(100):
        if probe_pids():
            break
        await asyncio.sleep(0.05)
    check("the child process really started", bool(probe_pids()))
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    for _ in range(100):
        if not probe_pids():
            break
        await asyncio.sleep(0.05)
    left = probe_pids()
    check("cancelling a render kills the process, not just the wait",
          not left, f"{len(left)} orphan(s) left running")
    for pid in left:
        try:
            psutil.Process(pid).kill()
        except Exception:
            pass

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
    check("...and did not silently become tier 0",
          r.get("tier") != 0,
          "0 is a real tier now; -1 is the sentinel for 'he did not say'")
    await render_tools.cancel_render()

    # An UNINSTALLED tier is refused before he is asked to wait for it. Being
    # asked "about a minute, shall I?" and only then told the model is not
    # installed is the worst possible order for those two sentences.
    #
    # Forced rather than assumed: tiers 3 and 4 are installed on this machine
    # now, so pointing the directory at nowhere is the only way to exercise the
    # refusal deliberately instead of depending on what happens to be present.
    _real_dir = create3d.model3d_dir
    create3d.model3d_dir = lambda: __import__("pathlib").Path(
        os.path.join(tempfile.mkdtemp(), "not-installed"))
    try:
        # NAMED, not inferred. "A dragon" used to land on tier 4 and does not
        # any more — it goes to the web first — so leaving this to the router
        # would have quietly stopped testing the refusal at all. The tier that
        # depends on the outside install is asked for by number.
        r = await render_tools.make_hologram(description="a dragon", tier=4)
        check("an uninstalled tier is refused rather than asked about",
              r.get("unavailable") is True and r.get("_ask") is None, r)
        check("...naming where it would live", "not-installed" in (r.get("error") or ""),
              r.get("error"))
    finally:
        create3d.model3d_dir = _real_dir

    # ...and when it IS installed, the same request asks instead, with a number.
    if create3d.available().get(4):
        # HERMETIC: with nothing stubbed this ran the scout for real on every
        # build — a Brave search, a GitHub search, and a stranger's dragon JPEG
        # written into his real work folder ("a-dragon-ref.jpg" was there).
        import netcheck as _net
        import scout as _scout
        _real = (_scout.look, _net.online)

        async def nothing(desc):
            return {}
        _scout.look, _net.online = nothing, lambda force=False: True
        try:
            r = await render_tools.make_hologram(description="a dragon")
        finally:
            _scout.look, _net.online = _real
        check("an installed tier asks, with an estimate", bool(r.get("_ask")), r)
        check("...and does not start until he answers", not r.get("started"), r)

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
    check("tier 4 needs exactly what tier 3 needs",
          avail.get(3) == avail.get(4),
          "tier 4 IS tier 3 with a reference picture in front of it")
    if avail.get(3):
        # INSTALLED. The model itself is not run here — 33 seconds is not a build
        # gate — but everything around it is, and a missing input must be refused
        # before a subprocess is started rather than after it fails.
        check("the worker script is where the tool expects it",
              (create3d.model3d_dir() / "photo_to_mesh.py").exists())
        check("the interpreter is its own, not the sidecar's",
              "model3d" in (create3d.model3d_python() or ""),
              create3d.model3d_python())
        r = await create3d.from_photo(os.path.join(tempfile.mkdtemp(), "nope.png"))
        check("a missing picture is refused before anything heavy starts",
              bool(r.get("error")) and not r.get("stl"), r)
        check("...and it is not reported as unavailable, because it is available",
              r.get("unavailable") is not True, r)
        r = await create3d.from_text("")
        check("an empty description is refused too", bool(r.get("error")), r)
        skip("tiers 3 and 4 producing a real mesh",
             "33 s and 55 s — run by .agent/scripts/render_live.py, not by a gate")
    else:
        for tier in (3, 4):
            r = await (create3d.from_photo("x.png") if tier == 3
                       else create3d.from_text("a dragon"))
            check(f"tier {tier} says it is not installed", r.get("unavailable") is True, r)
            check("...naming where it would live", "model3d" in (r.get("error") or ""),
                  r.get("error"))
            check("...and does NOT fall back to another tier",
                  r.get("tier") == tier and not r.get("stl"),
                  "handing him a different technique's output would look like "
                  "success and be wrong")
        skip("tiers 3 and 4 producing a mesh",
             f"not installed at {create3d.model3d_dir()}")

    # ------------------------------------------------- a photograph, as a relief
    # The fast, printable answer to "make something 3D out of this picture" —
    # a lithophane. No model of any kind, about a tenth of a second, and it must
    # come out WATERTIGHT or a slicer will refuse it.
    try:
        import cv2
        import numpy as np

        d_img = tempfile.mkdtemp()
        face = np.zeros((300, 400), np.uint8)
        cv2.circle(face, (200, 150), 110, 200, -1)
        cv2.circle(face, (160, 110), 30, 60, -1)
        cv2.circle(face, (240, 110), 30, 60, -1)
        photo = os.path.join(d_img, "face.png")
        cv2.imwrite(photo, face)
        out = os.path.join(d_img, "relief.stl")

        t0 = time.time()
        info = create3d.relief_stl(photo, out)
        took = time.time() - t0
        check("a photograph becomes a relief", info is not None, info)
        check("...quickly", took < 3.0, f"{took:.2f}s")

        import meshio
        import printcheck
        integ = printcheck.integrity(meshio.load_stl(out))
        check("...and it is watertight, so a slicer will take it",
              integ.get("watertight") is True, integ)
        check("...with consistent winding", integ.get("winding_consistent") is True, integ)
        size = meshio.describe(out)["size_mm"]
        check("...at a sensible size", 70 < size[0] < 90 and 2 < size[2] < 6, size)

        # DARK IS THICK. Backwards, this prints a photographic negative — it
        # looks like a bug because it is one. A black patch must be taller than
        # a white one.
        contrast = np.zeros((40, 80), np.uint8)
        contrast[:, 40:] = 255                       # left black, right white
        cpath = os.path.join(d_img, "half.png")
        cv2.imwrite(cpath, contrast)
        cout = os.path.join(d_img, "half.stl")
        create3d.relief_stl(cpath, cout)
        tris = meshio.load_stl(cout)
        pts = tris.reshape(-1, 3)
        w_mm = pts[:, 0].max()
        dark_h = pts[pts[:, 0] < w_mm * 0.25][:, 2].max()
        light_h = pts[pts[:, 0] > w_mm * 0.75][:, 2].max()
        check("dark is thick and light is thin", dark_h > light_h + 1.0,
              f"dark {dark_h:.2f} vs light {light_h:.2f} — backwards prints a negative")

        check("a picture with no reconstruction asked for stays fast",
              create3d.choose_tier("make this 3d", "photo.jpg") == 2)
        check("...and a real scan is only on request",
              create3d.choose_tier("scan this object", "photo.jpg") == 3)
        check("...and a logo is still an extruded outline",
              create3d.choose_tier("the logo", "logo.png") == 2)
        check("an unreadable picture makes no relief",
              create3d.relief_stl(os.path.join(d_img, "nope.png"),
                                  os.path.join(d_img, "x.stl")) is None)
    except ImportError:
        skip("the photo relief", "opencv is not importable here")

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
        # --- an emblem is a figure WITH HOLES, not a silhouette --------------
        # "Create me a 3D image of the Spider-Man emblem" came back as a plain oval
        # disc: RETR_EXTERNAL threw away everything inside the outer boundary, and
        # max(contourArea) then kept only that boundary. A logo is a figure with
        # holes and often several parts.
        ring = os.path.join(tempfile.mkdtemp(), "ring.png")
        a = np.zeros((400, 400), np.uint8)
        cv2.circle(a, (180, 200), 120, 255, -1)
        cv2.circle(a, (180, 200), 60, 0, -1)        # the hole
        cv2.circle(a, (340, 60), 25, 255, -1)       # a separate part
        cv2.imwrite(ring, a)
        shapes = create3d.trace_shapes(ring)
        check("both parts of the figure are traced", shapes and len(shapes) == 2,
              shapes and len(shapes))
        check("...and the hole inside it survives",
              shapes and any(len(sh["holes"]) == 1 for sh in shapes),
              [len(sh["holes"]) for sh in (shapes or [])])
        check("...where the old tracer kept one outline and no holes",
              len(create3d.trace_outline(ring) or []) >= 3)
        scad = create3d._shapes_scad(shapes, 3.0, 60.0)
        check("the hole is cut, not drawn over", "paths = [[" in scad, scad[:120])
        check("...and every part is extruded together", scad.count("polygon(") == 2,
              scad.count("polygon("))
        # A speck of noise is not a leg.
        b = a.copy()
        cv2.circle(b, (10, 390), 2, 255, -1)
        spk = os.path.join(tempfile.mkdtemp(), "speck.png")
        cv2.imwrite(spk, b)
        check("a speck of noise is not treated as a part",
              len(create3d.trace_shapes(spk) or []) == 2,
              len(create3d.trace_shapes(spk) or []))

        check("an unreadable file traces nothing",
              create3d.trace_outline(
                  os.path.join(tempfile.mkdtemp(), "nope.png")) is None)
    except ImportError:
        skip("tier 2 outline tracing", "opencv is not importable here")

    # ------------------------------------------------- tier 5: found, not made
    # EVERY CASE BELOW IS A REAL REPO, READ IN FULL. The failures in this tier
    # were wrong answers that looked like right ones, so the fixtures are what
    # GitHub actually contains rather than what a plausible repo would contain.
    REPOS = {
        # Six parts of an arc reactor. NONE of them names it — the repo is the
        # object. Requiring the filename to say "arc reactor" rejected this.
        "crashworks3d/crashworks3d_arc_reactor": [
            ("stl/lower_grid_and_caps.stl", 2347), ("stl/crystal_ring.stl", 889),
            ("stl/main_crystal_top.stl", 274), ("stl/upper_grid.stl", 165)],
        # A dice-detection rig. The repo says "d20"; every file is camera
        # hardware, and the biggest is a calibration card. This was returned as
        # a d20 — 512 triangles, 142 x 143 x 3 mm.
        "rsandrini/D20-IRL-detection": [
            ("3d_model/stl/dice-IRL-webcam-plate.stl", 135),
            ("3d_model/stl/dice-IRL-webcam-cup.stl", 81)],
        # An actual d20.
        "yannickbattail/openscad-models": [("animal_d20/d20.stl", 4051)],
        # A helmet published as panels, all of them behind Git LFS. Skipped
        # entirely at first because every mesh was a 133-byte pointer.
        "Poesghost/mandalorian_helmet": [
            ("prints/v1/helmet_front_top_right1.stl", 13423),
            ("prints/v1/helmet_back1.stl", 9000),
            ("prints/v1/helmet_attachments1.stl", 400)],
        # Mandalorian armour with no helmet in it. Returned a keyslot bracket.
        "valinkrai/mandalorian-armor": [
            ("accessories/hazard_ops_brigade/Food/cookie-cutter-set.stl", 625),
            ("helmet/foreman_v1/boba_style_keyslot_for_foreman.stl", 141),
            ("helmet/foreman_v1/helmet_arduino_case_2_in_1.stl", 31)],
        # Two helmet CLASPS, not a helmet.
        "marcojunarta/Mark-85-Helmet": [
            ("helmetclasppartb.stl", 146), ("helmetclaspparta.stl", 106)],
        "SimonWaldherr/openscad-examples": [("11_mug.scad.stl", 7131)],
    }

    def pick(desc, repo):
        got, others = create3d._pick_mesh(
            [{"repo": repo, "path": p, "bytes": kb * 1024}
             for p, kb in REPOS[repo]], desc)
        return (got["path"].split("/")[-1] if got else None), got, others

    name, got, others = pick("an arc reactor",
                             "crashworks3d/crashworks3d_arc_reactor")
    check("a repo that IS the object carries files that don't name it",
          name == "lower_grid_and_caps.stl", name)
    check("...and says it is one part of several",
          bool(got and got["is_piece"]) and len(others) == 3,
          "six parts of an arc reactor handed over as an arc reactor is the "
          "quarter-of-a-helmet failure again")

    name, _, _ = pick("a d20 dice", "rsandrini/D20-IRL-detection")
    check("a repo that only MENTIONS the subject gives nothing",
          name is None,
          "this returned dice-IRL-webcam-plate.stl — a 512-triangle "
          "calibration card, 142 x 143 x 3 mm — as a d20")
    name, _, _ = pick("a d20 dice", "yannickbattail/openscad-models")
    check("...while a real d20 comes through", name == "d20.stl", name)

    name, got, others = pick("a mandalorian helmet", "Poesghost/mandalorian_helmet")
    check("the largest matching panel is the pick", name == "helmet_front_top_right1.stl",
          name)
    check("...and it is reported as one of the parts",
          bool(got and got["is_piece"]) and len(others) == 2,
          "handed over silently, this is a quarter of a helmet called a helmet")
    check("a trailing version digit still reads as a piece",
          all(o["is_piece"] for o in others if "back" in o["path"]),
          "`helmet_back1` split to 'back1', matched nothing, and the back "
          "panel outranked the front one as a whole helmet")

    for desc, repo, why in (
            ("a mandalorian helmet", "valinkrai/mandalorian-armor",
             "returned boba_style_keyslot_for_foreman.stl — a bracket"),
            ("a mandalorian helmet", "marcojunarta/Mark-85-Helmet",
             "the repo has clasps in it, not a helmet")):
        name, _, _ = pick(desc, repo)
        check(f"supporting hardware is not the object ({repo.split('/')[-1]})",
              name is None, why)

    # A SUBJECT WORD BURIED IN A LONGER ONE IS A DIFFERENT WORD. Live on the
    # installed build, "a duck" was offered `00_Microduck_...stl`, 38 MB, from
    # `fanhao375/microduck-replica` — a robot kit — because "duck" is inside
    # "microduck". Whole-token matching alone is not the fix either: 3DBenchy
    # and IronManMark41 concatenate everything. What separates them is how much
    # longer the token is.
    check("a word inside a token barely longer than it is that word",
          create3d._names("benchy", ["3dbenchy"])
          and create3d._names("helmet", ["helmet", "front"]))
    check("...and a word buried in a much longer one is not",
          not create3d._names("duck", ["00", "microduck"])
          and not create3d._names("cat", ["catalogue"]),
          "a robot kit called microduck was offered as a duck")
    check("...and a name that is mostly digits still matches itself",
          create3d._names("d20", ["animal", "d20"]),
          "the piece-detection tokens strip trailing digits, which turns d20 "
          "into d — so name matching has to use the token as written")

    name, got, _ = pick("a coffee mug", "SimonWaldherr/openscad-examples")
    check("a single whole object is not called a piece",
          name == "11_mug.scad.stl" and not got["is_piece"], name)

    # Measured bounding boxes from four real downloads.
    check("a print plate is not the object",
          create3d._too_flat([15.0, 30.33, 3.0]),
          "395,174 triangles of perforated forearm shell laid flat on a bed, "
          "fetched and announced for 'render iron man mark 3'")
    check("...and a helmet panel, a mug and a d20 are",
          not any(create3d._too_flat(s) for s in
                  ([119.8, 128.3, 158.1], [135.0, 100.0, 100.0],
                   [21.4, 20.5, 23.6])))

    check("a downloaded mesh that isn't in millimetres says so",
          create3d._unit_doubt([2.1, 2.0, 2.4]).get("unit_guess") == "inches",
          "an STL carries no units, and the bed check, the wall check and the "
          "sliver guard all believe the number")
    check("...and a plausible one is left alone",
          not create3d._unit_doubt([119.8, 128.3, 158.1]))

    check("the number in 'mark 3' is part of the subject",
          "3" in create3d._subject_words("iron man mark 3"),
          "dropped as too short, which let IronManMark41 match perfectly")

    # HIS RULE: "There is no limitation to this." Say "render Iron Man Mark 3"
    # and it renders — "take an image from the web and then create that into
    # 3D". So when nothing can be downloaded the answer is the reconstruction,
    # not a page to open and not OpenSCAD writing code for a suit of armour.
    check("nothing findable still gets rendered, from a picture",
          create3d._fallback_tier("iron man mark 3") == 4
          and create3d._fallback_tier("a duck") == 4)
    check("...and never falls back to a tier that is not installed",
          create3d.available().get(4) or create3d._fallback_tier("a duck") == 1,
          "a tier that answers 'I don't have that installed' is not a fallback")

    check("tier 5's estimate covers what it actually does", est.SEED.get(5, 0) >= 40,
          "seeded at 20 s on the reasoning that a search and a download are "
          "quick. Measured: the search is ~14 s, and most character subjects "
          "are not fetchable and fall through to a ~25-40 s reconstruction, so "
          "'twenty seconds, sir' was followed by three quarters of a minute "
          "of silence")
    import model_find as _mf
    check("tier 5 looks for the formats we can actually read",
          ".obj" in _mf._FETCHABLE_EXT and ".stl" in _mf._FETCHABLE_EXT,
          "the scan searched only for .stl while `fetch` advertised .obj and "
          "then refused it")

    import model_find
    check("a Git LFS pointer is recognised rather than skipped as too small",
          model_find._LFS_POINTER_BYTES[0] <= 133 <= model_find._LFS_POINTER_BYTES[1]
          and model_find.MIN_MODEL_BYTES > 133,
          "every mesh in the one repo holding real Mandalorian helmet shells "
          "is a 133-byte pointer")
    check("...and the pointer's stated size is read from it",
          model_find._LFS_SIZE.search(
              "version https://git-lfs.github.com/spec/v1\n"
              "oid sha256:cde71c\nsize 11266984\n").group(1) == "11266984")
    # THE HONESTY HAS TO LAND IN THE LINE HE HEARS, not in the result dict.
    # `spoken_caveats` is what the background announcement appends, and without
    # this a stranger's sculpture finishes as "ready, sir, 120 by 128 by 158
    # millimetres" — presented as ours, and he might repeat that to someone.
    said = create3d.spoken_caveats(
        {"found_not_made": True, "credit": "Poesghost/mandalorian_helmet",
         "in_pieces": True, "part_count": 3})
    check("a downloaded model says whose it is, out loud",
          "found this rather than made it" in said
          and "Poesghost/mandalorian_helmet" in said, said)
    check("...and that it is one part of several", "one of 3 parts" in said, said)
    check("a faceted model says so",
          "faceted" in create3d.spoken_caveats(
              {"found_not_made": True, "credit": "x/y", "coarse": True}),
          "206 triangles is a real chess knight and a visibly faceted one")
    check("...and a mesh of unknown units carries its warning into the line",
          "no units" in create3d.spoken_caveats(
              {"unit_note": "that file has no units in it"}))
    # "Don't worry about an account. Find an alternative to that." A locked
    # website was being given as the OUTCOME of a request to render something.
    said_back = create3d.spoken_caveats(
        {"fell_back_from": 5, "pages_found": [{"host": "printables.com"}]})
    check("building instead of finding is mentioned, briefly",
          "built this one" in said_back, said_back)
    check("...and a website needing an account is never the answer",
          "account" not in said_back, said_back)
    check("a part we actually made carries none of this",
          create3d.spoken_caveats({"tier": 1, "stl": "x.stl"}) == "")

    # AND THE REASON HAS TO BE THE TRUE ONE. Live, "a duck" hit nine GitHub
    # repos, none of which contains a duck, and JARVIS announced that GitHub
    # needed an account. It does not; it hands over anything it has.
    mt = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "tools", "model_tools.py"),
        encoding="utf-8").read()
    check("'that site needs an account' is never said about GitHub",
          "locked = [c for c in cands" in mt and "robot kit" in mt,
          "he told me to stop treating a locked website as the answer; saying "
          "it about a site that is not locked is worse")
    check("...and the honest alternative is offered instead",
          "I can build you one instead" in mt)

    check("pages get room of their own alongside the fetchable results",
          model_find._KEEP_PAGES >= 2,
          "eight GitHub repos filled the list, so 'Printables has real ones "
          "but they need an account' — the honest answer for Iron Man — could "
          "not be said")

    # ------------------------------------------- the reference picture, tier 4
    # TripoSR builds what it can see, so the picture IS the ceiling. Five ways
    # of choosing it wrongly, each of which produced a mesh that measured
    # perfectly and looked like nothing.
    check("a reconstruction asks for the whole object",
          "full body" in create3d.reference_image.__doc__.lower()
          or True)  # the query itself is asserted below
    import inspect
    src_ref = inspect.getsource(create3d.reference_image)
    check("the reconstruction query asks for one whole object on a plain ground",
          "full body single object on white background" in src_ref,
          "plain 'a duck' returned a mallard half under water and a close-up "
          "of two webbed feet; the mesh was a lump")
    check("...and the tracing query still asks for a flat silhouette",
          "logo silhouette black on white transparent png" in src_ref,
          "learned from the Spider-Man emblem that traced as a disc")

    # A site icon is not a search result.
    check("DuckDuckGo's favicon endpoint is not a reference",
          create3d._bigger_first("https://external-content.duckduckgo.com/ip3/x.ico")
          is not None)
    got = await create3d._fetch_reference(
        {"src": "https://external-content.duckduckgo.com/ip3/www.turbosquid.com.ico"})
    check("...and is refused before it is downloaded", got == b"",
          "this returned TurboSquid's orange SQUID logo at 869 x 1017, beat "
          "every real photograph on size, and was reconstructed into a tangle "
          "of tentacles labelled 'iron man mark 3'")

    # The thumbnail is upgraded to something worth reconstructing.
    urls = create3d._bigger_first(
        "https://external-content.duckduckgo.com/iu/?u=https%3A%2F%2F"
        "tse3.mm.bing.net%2Fth%2Fid%2FOIP.abc%3Fr%3D0%26pid%3DApi&f=1")
    check("a 474-pixel thumbnail is asked for at full size first",
          len(urls) == 2 and "h=1200" in urls[0] and "bing.net" in urls[0], urls)
    check("...with the height constrained, not squared off",
          "w=1200" not in urls[0],
          "w=1200&h=1200 pads the picture into a square")
    check("...and the thumbnail is still there to fall back on",
          urls[-1].startswith("https://external-content.duckduckgo.com/iu/"))
    check("a link that is not a thumbnail service is left alone",
          create3d._bigger_first("https://example.com/a.jpg")
          == ["https://example.com/a.jpg"])

    # Framing, against pictures built to be exactly the failures that happened.
    try:
        from PIL import Image, ImageDraw

        def pic(draw_it, size=(400, 400), bg=(255, 255, 255)):
            im = Image.new("RGB", size, bg)
            draw_it(ImageDraw.Draw(im))
            b = io.BytesIO()
            im.save(b, format="PNG")
            return b.getvalue()

        whole = pic(lambda d: d.rectangle((150, 40, 250, 399), fill=(20, 20, 20)))
        cut = pic(lambda d: d.rectangle((0, 40, 399, 399), fill=(20, 20, 20)))
        two = pic(lambda d: [d.rectangle((60, 40, 150, 399), fill=(20, 20, 20)),
                             d.rectangle((250, 40, 340, 399), fill=(20, 20, 20))])

        w1, s1, f1 = create3d._framing(whole)
        check("an object standing in shot is whole", w1 and s1, (w1, s1))
        check("...even though it touches the bottom edge", f1 > 0.05,
              "measured on eight real candidates, EVERY one touched the "
              "bottom — things stand on the ground. A rule that called that a "
              "crop rejected the two best pictures in the set")
        w2, s2, _ = create3d._framing(cut)
        check("an object running off the left and right is not whole",
              not w2 and s2, (w2, s2))
        w3, s3, _ = create3d._framing(two)
        check("two of the thing is not one of the thing", not s3,
              "a catalogue picture of the Mark III from the front AND the back "
              "was reconstructed into two Iron Men lying side by side")
        busy = pic(lambda d: [d.rectangle((0, 0, 399, 399), fill=(90, 30, 140)),
                              d.ellipse((80, 80, 300, 300), fill=(240, 200, 60))],
                   bg=(90, 30, 140))
        wb, sb, _ = create3d._framing(busy)
        check("a busy background gets no verdict rather than a wrong one", sb,
              "False here would throw away every photograph not taken in a "
              "studio")
    except ImportError:
        skip("reference framing", "PIL is not importable here")

    check("a standing figure's portrait is not mistaken for a banner",
          create3d.MAX_REFERENCE_ASPECT >= 3.0,
          "at 2.2 this rejected both 474 x 1159 full-body Mark III pictures "
          "and left only the catalogue shots with two figures in them")

    # ------------------------------------------------------------ OBJ, for the
    # hologram rather than the printer. STL is a printing format; anything an
    # artist sculpts is exported as OBJ.
    import meshio
    check("OBJ is a format we say we can read", ".obj" in meshio.READABLE)
    obj = ("v 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\n"
           "vt 0 0\nvn 0 0 1\ng thing\nusemtl x\n"
           "f 1/1/1 2/1/1 3/1/1 4/1/1\n"
           "v 0 0 1\nv 1 0 1\nv 1 1 1\nf -3 -2 -1\n")
    d = tempfile.mkdtemp()
    p = os.path.join(d, "t.obj")
    open(p, "w", encoding="utf-8").write(obj)
    tris = meshio.load(p)
    check("a quad becomes two triangles and a triangle stays one",
          len(tris) == 3, len(tris))
    check("...and negative indices count back from the vertices seen so far",
          float(tris.reshape(-1, 3)[:, 2].max()) == 1.0,
          "the last face uses -3 -2 -1 and must reach the second group")
    check("vt, vn, g and usemtl are not vertices", len(tris) == 3)
    for bad, why in ((b"v 0 0 0\nf 1 2 3\n", "points past its own vertex list"),
                     (b"v 0 0 0\n", "has vertices but no faces"),
                     (b"", "is empty")):
        q = os.path.join(d, "bad.obj")
        open(q, "wb").write(bad)
        try:
            meshio.load(q)
            check(f"an OBJ that {why} is refused", False, "it was accepted")
        except meshio.BadMesh as e:
            check(f"an OBJ that {why} is refused, in words", bool(str(e)), e)

    print()
    if skips:
        print(f"  {len(skips)} case(s) SKIPPED — phase D's gate was NOT fully exercised:")
        for name, why in skips:
            print(f"     - {name}: {why}")
    print("\n-- a job past its estimate does not claim to be nearly done --")
    # 2026-09-03, from his own testing. "Render a duck" is tier 5; no publishable
    # duck exists, so it falls back to tier 4 and takes ~175s. The estimate stays
    # at tier 5's 45s. He asked where it was 90 seconds in and was told it was
    # almost finished — because `left` was clamped at zero, so every status
    # answer past the estimate became "any moment now" and stayed there for the
    # remaining minute and a half. Sounding certain and being wrong is the exact
    # thing render_queue's own header forbids.
    import render_queue as _rq

    def _status_at(elapsed, estimate=45.0):
        j = _rq.Job(id="t", tier=5, label="a duck", run=lambda: {},
                    estimate_s=estimate)
        j.started = time.time() - elapsed
        q = _rq.RenderQueue()
        q._current = j
        return q.status()

    s = _status_at(90)
    check("90s into a 45s estimate is reported as overdue",
          s.get("overdue") is True, str(s.get("remaining_spoken"))[:80])
    check("...and it does NOT say almost done",
          "any moment" not in s["remaining_spoken"].lower(),
          s["remaining_spoken"][:90])
    check("...and it says how long it has actually been",
          "longer than i said" in s["remaining_spoken"].lower(),
          s["remaining_spoken"][:90])
    # NOT a question mark. It used to ask "Shall I keep going?" with nothing
    # listening for the reply - he answered "yes please go ahead and finish the
    # render" and got a web search. It is a notice now, and what it has to
    # carry is the way OUT.
    check("...and it tells him how to stop it",
          "stop" in s["remaining_spoken"].lower(), s["remaining_spoken"][:90])
    check("...and does not ask a question nothing is listening for",
          "?" not in s["remaining_spoken"], s["remaining_spoken"][:90])
    check("...and the overdue amount is real",
          40 <= s.get("overdue_s", 0) <= 50, str(s.get("overdue_s")))

    on_time = _status_at(40)
    check("a job still inside its estimate is unchanged",
          on_time.get("overdue") is False and "longer than" not in
          on_time["remaining_spoken"].lower(), on_time["remaining_spoken"][:60])
    grace = _status_at(50)
    check("...and a few seconds over is still 'any moment now', not an alarm",
          "any moment" in grace["remaining_spoken"].lower(),
          grace["remaining_spoken"][:60])


    print("\n-- a render does not put a command prompt on his screen --")
    # 2026-09-03, while he was testing: a duck render opened a console window.
    # The tier-4 reconstructor is a python.exe, so without CREATE_NO_WINDOW
    # Windows gives it its own conhost and a visible window. Every other
    # launcher in the sidecar already passed the flag; fabrication._run, which
    # runs OpenSCAD, PrusaSlicer AND the reconstruction scripts, never did.
    fab_src = open(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "tools", "fabrication.py"), encoding="utf-8").read()
    run_body = fab_src[fab_src.index("async def _run("):]
    run_body = run_body[:run_body.index("# ---")] if "# ---" in run_body else run_body[:3000]
    check("_run launches its children with no console window",
          "CREATE_NO_WINDOW" in run_body,
          "openscad, prusaslicer and the reconstructor all go through here")


    # -- the queue keeps the clock the QUESTION used (2026-09-06) -------------
    # A detailed render is asked about under key 8 (minutes) and was
    # submitted under tier 3 (150 s): "starting now, a couple of minutes"
    # right after he agreed to seven, and an overrun notice a third of the
    # way through.
    import render_estimates as _est
    qk = RenderQueue()
    subk = qk.submit(3, "a detailed mug", lambda: {"ok": True}, estimate_key=8)
    check("the estimate follows the key it was asked under",
          abs(subk.get("estimate_s", 0) - _est.estimate(8)) < 1e-6
          and subk.get("estimate_s", 0) > _est.estimate(3),
          (subk.get("estimate_s"), _est.estimate(8), _est.estimate(3)))
    check("...and the tool passes it", "estimate_key=est_key" in
          open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "tools", "render_tools.py"), encoding="utf-8").read())
    await asyncio.sleep(0.2)

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}"
          f"{f' ({len(skips)} skipped)' if skips else ''}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
