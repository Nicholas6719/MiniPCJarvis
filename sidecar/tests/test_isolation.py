"""A wedged tool must not be able to make him deaf, mute or stupid.

Found in the 2026-09-01 audit. Every sync tool handler ran through
`asyncio.to_thread`, which uses the interpreter's default executor — 20 threads
on this machine — and 61 call sites shared it, including speech-to-text,
text-to-speech, the embedding calls on the turn path and the brain router.

Tool handlers are the dangerous tenants. Windows UI Automation blocks on an
unresponsive app; a subprocess hangs; a browser call sits forever. And
`asyncio.wait_for` cancels the FUTURE, never the thread: a tool that times out
keeps its thread for the life of the process. Enough of those and the pool that
STT and TTS need is gone, and JARVIS answers nothing — the exact failure he has
already lived through twice, reachable from any stuck tool.

Tools now have their own bounded pool. This test wedges more handlers than that
pool holds and asserts the turn path still runs.

Also here: `spawn()` used to discard a failed background task without ever
reading its exception, so a crashed background job was silent.

Offline: no tools are really called, nothing is opened.
Run: python tests/test_isolation.py
"""
import asyncio
import logging
import os
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "iso.db"))

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


def main() -> int:
    from tools.registry import _TOOL_THREADS, _run_in_tool_pool, _tool_executor

    release = threading.Event()

    def wedged(**kw):
        release.wait(30)          # a handler that will not come back
        return "eventually"

    # Enough wedged handlers to fill the DEFAULT executor, not merely the tool
    # pool. Wedging only `_TOOL_THREADS + 2` would prove nothing: the old code
    # shared a 20-thread default pool, so ten stuck tools still left ten free
    # and the turn path survived by luck. The number that matters is the one
    # that exhausts the pool STT and TTS were sharing.
    default_max = min(32, (os.cpu_count() or 1) + 4)
    n_wedged = default_max + 2

    async def scenario():
        stuck = [_run_in_tool_pool(wedged, {}) for _ in range(n_wedged)]
        await asyncio.sleep(0.6)

        # ...now the turn path must still work. This is the whole point: these
        # go to the DEFAULT executor, which the tools no longer share.
        t0 = time.time()
        out = await asyncio.gather(*[asyncio.to_thread(lambda: sum(range(1000)))
                                     for _ in range(6)])
        took = time.time() - t0
        return stuck, out, took

    stuck, out, took = asyncio.run(scenario())
    check("the turn path still runs while every tool thread is wedged",
          out == [499500] * 6, out)
    check("...and it is not merely slow", took < 2.0, f"{took:.2f}s")
    check("the tool pool is bounded, so the wedge cannot spread",
          _TOOL_THREADS <= 12, _TOOL_THREADS)

    # Let the wedged handlers finish. Do NOT cancel their futures — the loop
    # they belong to has already closed, and cancelling then raises.
    release.set()
    del stuck

    # --- a failed background task is reported, not swallowed ----------------
    from events import spawn

    seen = []

    class Catch(logging.Handler):
        def emit(self, record):
            seen.append(record.getMessage())

    handler = Catch()
    lg = logging.getLogger("jarvis.events")
    lg.addHandler(handler)

    async def boom():
        raise RuntimeError("the background job broke")

    async def run_spawn():
        t = spawn(boom(), name="unit-test-job")
        await asyncio.sleep(0.2)
        return t

    try:
        asyncio.run(run_spawn())
    finally:
        lg.removeHandler(handler)

    check("a background task that raises is logged",
          any("unit-test-job" in m for m in seen), seen)

    # ...and a cancelled one is not treated as a failure
    seen.clear()
    lg.addHandler(handler)

    async def run_cancel():
        async def forever():
            await asyncio.sleep(30)
        t = spawn(forever(), name="cancelled-job")
        await asyncio.sleep(0.1)
        t.cancel()
        await asyncio.sleep(0.1)

    try:
        asyncio.run(run_cancel())
    finally:
        lg.removeHandler(handler)
    check("a cancelled task is not reported as a failure", seen == [], seen)

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
