"""A background task nobody holds can be collected mid-await - and take a
lock with it.

2026-09-13 20:02:22, one minute after release 64 booted:

    Task was destroyed but it is pending! TTSRouter.warm_phrases() running at
    audio/tts.py:434

Started with a bare asyncio.create_task(), which the loop holds only weakly;
the collector took it while it sat inside `async with self._synth_lock`. A
destroyed task never runs its exit, the lock stayed held, and every phrase
not already cached waited on it forever. "What time is it" answered; anything
that needed the model or a new sentence sat in PROCESSING until the stuck
watchdog killed it, all evening, on every channel. events.spawn() has kept
references since release 50; thirteen sites never used it.

Run: python tests/test_background_tasks.py
"""
import asyncio
import gc
import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "bg.db"))

ROOT = Path(__file__).resolve().parent.parent
fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


def main() -> int:
    print("\n-- no task is started without somebody holding it --")
    bare = re.compile(r"^\s*(?:asyncio|_a|loop)\.create_task\(", re.M)
    offenders = []
    for f in ROOT.rglob("*.py"):
        if any(part in (".venv", "dist", "tests", "__pycache__", "build") for part in f.parts):
            continue
        if f.name == "events.py":
            continue            # spawn() itself is allowed to call create_task
        for m in bare.finditer(f.read_text(encoding="utf-8", errors="replace")):
            line = f.read_text(encoding="utf-8", errors="replace").count("\n", 0, m.start()) + 1
            offenders.append(f"{f.relative_to(ROOT)}:{line}")
    check("every fire-and-forget task goes through events.spawn()", not offenders,
          "; ".join(offenders[:8]))

    print("\n-- and spawn() really does hold it --")
    import events

    async def prove():
        gate = asyncio.Event()
        seen = []

        async def job():
            await gate.wait()
            seen.append("ran")

        t = events.spawn(job())
        weak = [t]
        del t
        for _ in range(3):
            gc.collect()
            await asyncio.sleep(0.01)
        gate.set()
        await asyncio.sleep(0.05)
        return seen, weak[0].done()

    seen, done = asyncio.run(prove())
    check("a spawned task survives a collection and finishes", seen == ["ran"] and done, (seen, done))

    print("\n-- the synthesis lock has a deadline --")
    tts = (ROOT / "audio" / "tts.py").read_text(encoding="utf-8")
    seg = tts[tts.index("ONE SYNTHESIS AT A TIME - with a deadline"):][:1400]
    check("the lock is taken with wait_for", "wait_for(self._synth_lock.acquire(), timeout=8.0)" in seg)
    check("...and replaced, loudly, when its holder is gone",
          "self._synth_lock = asyncio.Lock()" in seg and "log.error(" in seg)
    check("...and released in a finally, whatever happened", "finally:" in seg and "_synth_lock.release()" in seg)

    async def wedge():
        """A lock whose holder died: a second taker must get through."""
        from audio.tts import TTSRouter
        r = TTSRouter.__new__(TTSRouter)
        r._synth_lock = asyncio.Lock()
        await r._synth_lock.acquire()            # held by a task that will never release
        started = asyncio.get_running_loop().time()
        try:
            await asyncio.wait_for(r._synth_lock.acquire(), timeout=0.2)
            return "took it (?)", 0.0
        except asyncio.TimeoutError:
            r._synth_lock = asyncio.Lock()
            await r._synth_lock.acquire()
            return "replaced", asyncio.get_running_loop().time() - started
    how, secs = asyncio.run(wedge())
    check("a wedged lock is recoverable by replacement", how == "replaced" and secs < 1.0, (how, secs))

    print("\n-- a test mute keeps the room dark --")
    wt = (ROOT / "tools" / "windows_tools.py").read_text(encoding="utf-8")
    fn = wt[wt.index("def exit_sleep_mode"):]
    fn = fn[:fn.index("\ndef ", 10)]
    check("exit_sleep_mode stands down under a test mute",
          "speaker.silent_until > _t.time()" in fn and "left alone" in fn)
    check("...before it touches the display", fn.index("silent_until") < fn.index("wake_display"))
    orch = (ROOT / "orchestrator.py").read_text(encoding="utf-8")
    tl = orch[orch.index("async def toggle_listen"):][:1800]
    check("the hotkey lifts a mute like his voice does", "self._voice_heard()" in tl)

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
