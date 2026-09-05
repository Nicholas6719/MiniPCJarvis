"""The microphone must reopen from a worker thread, and say so honestly.

The audit of 2026-09-04 moved every mic reopen off the event loop, which was
right: a stop() joins the callback and a start() talks to the driver. But
Microphone.start() asked for the RUNNING loop, and a worker thread has none.
Seven times in the following day the self-heal fired and failed before it
touched the device - "no running event loop" - so the one path meant to bring
the mic back never could. The device watch then re-fired on "no stream open",
which is also true for the few seconds a debug utterance is being fed.

Run: python tests/test_mic_offloop.py
"""
import asyncio
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


class FakeStream:
    """A device that delivers one block from its own thread, as PortAudio does."""
    opened = []

    def __init__(self, callback, fail=False):
        if fail:
            raise RuntimeError("device unavailable")
        self.cb = callback
        FakeStream.opened.append(self)

    def start(self):
        import numpy as np
        threading.Thread(target=self.cb, args=(np.zeros((1024, 1), dtype="float32"), 1024, None, None),
                         daemon=True).start()

    def stop(self):
        pass

    def close(self):
        pass


def main() -> int:
    import audio.io as aio

    fail_next = {"on": False}

    def fake_input_stream(**kw):
        return FakeStream(kw["callback"], fail=fail_next["on"])

    aio.sd.InputStream = fake_input_stream
    aio.sd.query_hostapis = lambda: [{"name": "Fake"}]
    aio.sd.query_devices = lambda: [{"hostapi": 0}]
    aio.resolve_input_device = lambda: (0, "fake mic", True)

    async def run():
        mic = aio.Microphone()
        q = mic.subscribe()
        mic.start()
        got = await asyncio.wait_for(q.get(), 2)
        check("on the loop: a frame arrives", got is not None)
        check("...and start() reports no failure", mic.failed is False)

        # the audit's reopen: stop + start on a worker thread
        def work():
            mic.stop()
            mic.start()
        try:
            await asyncio.to_thread(work)
            ok = True
        except Exception as e:
            ok = False
            print("   ", e)
        check("off the loop: start() does not need a running loop", ok)
        got = await asyncio.wait_for(q.get(), 2)
        check("...and its frames still land on the loop", got is not None)

        # a device that cannot open is a FAILURE, and stays one until it opens
        fail_next["on"] = True
        try:
            await asyncio.to_thread(work)
            raised = False
        except Exception:
            raised = True
        check("a device that will not open raises", raised)
        check("...and the mic says it failed", mic.failed is True and mic._stream is None)
        fail_next["on"] = False
        await asyncio.to_thread(mic.start)
        check("...until the next start succeeds", mic.failed is False)

        # a deliberate stop is not a failure - the device watch must not
        # 'heal' a mic that a debug utterance has closed on purpose
        mic.stop()
        check("a deliberate stop is not a failure", mic.failed is False and mic._stream is None)

        # a fresh microphone started on a thread with no loop ever bound: an
        # honest error, not a silent one
        fresh = aio.Microphone.__new__(aio.Microphone)
        fresh._stream = None
        fresh._loop = None
        fresh.failed = False
        fresh._subs = set()
        try:
            await asyncio.to_thread(fresh.start)
            raised = False
        except RuntimeError:
            raised = True
        check("a never-bound mic started off-loop still refuses", raised)

    asyncio.run(run())
    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
