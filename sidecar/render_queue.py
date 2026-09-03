"""One render at a time, in the background, cancellable, and never silent.

His requirement, verbatim: every render runs in the background — he keeps
talking, JARVIS keeps working, and asking the CPU load while a mesh is
generating must answer immediately.

THE FOUR RULES THIS FILE EXISTS TO KEEP.

ONE AT A TIME. Tiers 3 and 4 want the GPU that llama-server is already holding
9.6 GB of. Two renders at once would not be twice as fast; they would be two
renders that both miss their estimates and a JARVIS that cannot answer while
they run. A second request queues.

NOTHING TOUCHES THE EVENT LOOP. The work is handed to a thread or a subprocess
and awaited. The forty-minute freeze was an audio lock held on the loop, and a
render is a far bigger block than audio ever was — so `submit` takes a plain
blocking callable and the queue is the only thing that knows about threads.

IT CAN BE STOPPED. "Stop that" cancels the running job and drops the queue. A
render that cannot be cancelled is a machine holding its user hostage, and this
one can run for minutes.

IT SAYS SO WHEN IT IS WRONG. If a job passes its estimate by enough to notice,
JARVIS says so and offers to stop — "this is running longer than I said, sir,
about four minutes more. Shall I carry on?" Being wrong about an estimate is
forgivable; going quiet about it is not.

Completion goes through `delivery`, which already decides speak-versus-send by
whether he is at the machine, and already carries the dedup and hourly ceiling
from the 2,600-message night.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field

import render_estimates as est
from events import bus, spawn

log = logging.getLogger("jarvis.render.queue")

# How far past the estimate before he is told. 1.6x and at least 20 seconds, so
# a 6-second tier-2 job that takes 10 does not interrupt him to say so.
OVERRUN_FACTOR = 1.6
OVERRUN_FLOOR_S = 20.0
MAX_QUEUED = 4


@dataclass
class Job:
    id: str
    tier: int
    label: str                       # "a dragon", for the sentence
    run: object                      # a BLOCKING callable -> dict; run off the loop
    estimate_s: float = 0.0
    submitted: float = field(default_factory=time.time)
    started: float = 0.0
    result: dict | None = None
    state: str = "queued"            # queued | running | done | failed | cancelled


class RenderQueue:
    def __init__(self) -> None:
        self._jobs: list[Job] = []
        self._current: Job | None = None
        self._task: asyncio.Task | None = None
        self._pump: asyncio.Task | None = None
        self._draining = False
        self._warned = False

    # ---- what he can ask about ------------------------------------------
    @property
    def busy(self) -> bool:
        """Running OR waiting to run.

        These were two different answers to the same word: `status()` learned to
        count a queued job as busy — because telling him "nothing's rendering"
        one second after he asked for something is a lie — while this property
        still meant "a job is executing". Anything waiting on `not busy` for the
        work to be over therefore stopped waiting before it had started, which is
        exactly how a calibration check concluded a job had never run.
        """
        return self._current is not None or bool(self._jobs)

    def status(self) -> dict:
        cur = self._current
        if cur is None:
            # QUEUED IS STILL BUSY. There is a moment between submitting a job
            # and the pump picking it up, and reporting "nothing is rendering"
            # inside it is a lie he would catch immediately — he asked for the
            # thing one second ago. It also made a test wait on the wrong
            # condition and conclude a render had failed when it had not started.
            if self._jobs:
                nxt = self._jobs[0]
                return {"busy": True, "starting": True, "label": nxt.label,
                        "tier": nxt.tier, "elapsed_s": 0.0,
                        "remaining_s": round(nxt.estimate_s, 1),
                        "remaining_spoken": est.spoken(nxt.estimate_s),
                        "queued": len(self._jobs)}
            return {"busy": False, "queued": 0}
        elapsed = time.time() - cur.started
        left = cur.estimate_s - elapsed
        # PAST THE ESTIMATE IS NOT "ALMOST DONE". This clamped `left` at zero,
        # so once a job ran over, every status answer became "any moment now" —
        # and stayed that way however long was really left. On 2026-09-03 he
        # asked where his duck was 90s into a 175s job and was told it was
        # nearly finished, twice, because tier 5 had fallen back to tier 4 and
        # the estimate was still the 45s of the tier that no longer applied.
        # Saying "I don't know" is allowed; sounding certain and being wrong is
        # the thing this file exists to prevent.
        if left > 1:
            spoken = est.spoken(left)
        elif left > -15:
            spoken = "any moment now"
        else:
            spoken = (f"longer than I said, sir — it's been "
                      f"{est.spoken(elapsed)} and I don't have a good estimate "
                      f"left. Shall I keep going?")
        return {"busy": True, "label": cur.label, "tier": cur.tier,
                "elapsed_s": round(elapsed, 1),
                "remaining_s": round(max(0.0, left), 1),
                "overdue_s": round(max(0.0, -left), 1),
                "overdue": left <= -15,
                "remaining_spoken": spoken,
                "queued": len(self._jobs)}

    # ---- putting work in -------------------------------------------------
    def submit(self, tier: int, label: str, run) -> dict:
        """Queue a render. Returns immediately — that is the entire point."""
        if len(self._jobs) >= MAX_QUEUED:
            return {"error": "I've got too much queued already, sir"}
        job = Job(id=uuid.uuid4().hex[:8], tier=int(tier), label=label, run=run,
                  estimate_s=est.estimate(tier))
        self._jobs.append(job)
        spawn(self._announce("queued", job), name=f"render:queued:{job.id}")
        self._ensure_pump()
        return {"job": job.id, "tier": job.tier,
                "estimate_s": round(job.estimate_s, 1),
                "estimate_spoken": est.spoken(job.estimate_s),
                "queued_behind": max(0, len(self._jobs) - 1)}

    # ---- stopping it -----------------------------------------------------
    def cancel(self) -> dict:
        """Stop what is running and drop what is waiting."""
        dropped = len(self._jobs)
        self._jobs.clear()
        cur = self._current
        if cur is None:
            return {"cancelled": False, "dropped": dropped,
                    "why": "nothing was running"}
        cur.state = "cancelled"
        if self._task and not self._task.done():
            self._task.cancel()
        return {"cancelled": True, "label": cur.label, "dropped": dropped}

    # ---- the pump --------------------------------------------------------
    def _ensure_pump(self) -> None:
        """Start the drain loop unless one is already going.

        NOT `self._pump.done()`, which had a real race and stranded jobs: the
        drain coroutine can pass its `while self._jobs` check, find nothing, and
        begin returning — and during that window the task is not yet `done()`, so
        a job submitted right then was appended, saw a live pump, and sat in the
        queue forever. It showed up as a render that never started and a "stop
        that" which reported nothing was running while a job was plainly queued.

        A plain flag closes it, because `_drain` clears the flag and re-checks
        the queue with no await in between — so nothing can slip past.
        """
        if self._draining:
            return
        self._draining = True
        self._pump = spawn(self._drain(), name="render:pump")

    async def _drain(self) -> None:
        try:
            await self._drain_loop()
        finally:
            self._draining = False
            if self._jobs:              # arrived while we were finishing
                self._ensure_pump()

    async def _drain_loop(self) -> None:
        while self._jobs:
            job = self._jobs.pop(0)
            self._current = job
            self._warned = False
            job.started = time.time()
            job.state = "running"
            await self._announce("started", job)
            timer = est.Timer(job.tier)
            watch = spawn(self._watch_overrun(job), name=f"render:watch:{job.id}")
            try:
                # OFF THE LOOP, whichever kind of callable it is. A tier written
                # around async subprocesses (OpenSCAD, PrusaSlicer) is awaited
                # directly because it already yields; a tier that grinds in numpy
                # or blocks on a model goes to a thread. Getting this wrong in
                # either direction blocks the loop, and the loop is where he
                # waits for answers.
                if asyncio.iscoroutinefunction(job.run):
                    self._task = asyncio.ensure_future(job.run())
                else:
                    self._task = asyncio.ensure_future(asyncio.to_thread(job.run))
                job.result = await self._task
                job.state = "failed" if (job.result or {}).get("error") else "done"
                if job.state == "done":
                    # Calibrate the tier that ACTUALLY RAN, not the one that was
                    # predicted. They usually agree, but a parametric template
                    # that fails to build falls through to the model — and a
                    # 27-second run filed under tier 0 would drag its median from
                    # a fifth of a second to a wait he would then be asked about.
                    ran = int((job.result or {}).get("tier", job.tier))
                    timer.tier = ran
                    timer.done()          # only a real success calibrates
            except asyncio.CancelledError:
                job.state = "cancelled"
                job.result = {"cancelled": True}
            except Exception as e:
                log.exception("render failed")
                job.state = "failed"
                job.result = {"error": str(e)}
            finally:
                watch.cancel()
                self._task = None
                self._current = None
            await self._announce(job.state, job)

    async def _watch_overrun(self, job: Job) -> None:
        """Say so if it runs long. Once — a job that is late is not more useful
        for being mentioned every minute."""
        limit = max(job.estimate_s * OVERRUN_FACTOR, job.estimate_s + OVERRUN_FLOOR_S)
        try:
            await asyncio.sleep(limit)
        except asyncio.CancelledError:
            return
        if self._current is not job or self._warned:
            return
        self._warned = True
        over = time.time() - job.started
        await self._say(
            f"That {job.label} is running longer than I said, sir — "
            f"it's been {est.spoken(over)}. Shall I carry on?",
            key=f"render-overrun:{job.id}")

    # ---- what he hears ---------------------------------------------------
    async def _announce(self, what: str, job: Job) -> None:
        await bus.emit("render", action=what, job=job.id, tier=job.tier,
                       label=job.label, estimate_s=round(job.estimate_s, 1),
                       **({"result": {k: v for k, v in (job.result or {}).items()
                                      if k != "source"}} if job.result else {}))
        if what == "done":
            took = time.time() - job.started
            r = job.result or {}
            line = f"The {job.label} is ready, sir — {est.spoken(took)}."
            if r.get("spoken_size"):
                line = f"The {job.label} is ready, sir — {r['spoken_size']}."
            # A part that came out wrong must say so in the SAME breath as
            # "ready" — and so must a number we chose for him. The background
            # path is the one he actually hears, and announcing a 0.4 mm sliver
            # as finished is how he finds out at the printer instead of here.
            try:
                import create3d
                extra = create3d.spoken_caveats(r)
            except Exception:
                extra = f"Though {r['mesh_warning']}." if r.get("mesh_warning") else ""
            if extra:
                line += " " + extra
            # A PICTURE OF WHAT WAS MADE, for the case where he is not here to
            # look at the stage. Drawn only when there is a model to draw.
            shot = ""
            if r.get("stl"):
                try:
                    import meshshot
                    shot = await meshshot.shot_async(r["stl"])
                except Exception:
                    log.debug("could not draw the finished model", exc_info=True)
            await self._say(line, key=f"render-done:{job.id}", image=shot)
        elif what == "failed":
            why = (job.result or {}).get("error") or "it didn't come out"
            await self._say(f"I couldn't make the {job.label}, sir — {why}.",
                            key=f"render-failed:{job.id}")
        # A cancellation is NOT announced: he is the one who cancelled it, and
        # the skill that took the instruction has already answered him.

    async def _say(self, line: str, key: str, image: str = "") -> None:
        """Through delivery, so it is spoken if he is here and sent if he is not
        — and so it is subject to the dedup and the hourly ceiling like
        everything else JARVIS says on its own initiative."""
        try:
            from delivery import ALERT, delivery
            await delivery.deliver(line, ALERT, key=key, image=image)
        except Exception:
            log.exception("could not announce a render")


queue = RenderQueue()
