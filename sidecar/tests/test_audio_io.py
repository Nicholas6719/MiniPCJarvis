"""The audio output must never be able to freeze the assistant.

On 2026-08-30 at 19:40 it did. A dead output device left PortAudio's blocking
write stuck inside `Speaker._write`, which holds `_wlock`. The timeout handler
then called `abort()`, which did `with self._wlock:` - from the EVENT LOOP
THREAD. The writer never returned, so the lock never came free, and the whole
assistant stopped: no speech, no HTTP, no wake word, for forty minutes. The
process stayed alive the entire time, so the supervisor never restarted it and
Nicholas had a JARVIS that simply did not answer.

The comment on that line read "writer has returned; safe to close". It had not.

These tests hold the lock exactly the way a stuck writer does, and assert the
event loop gets to keep running. No audio device is opened and nothing is
played - a fake stream stands in, because the real failure needs hardware that
has stopped responding.

Run: python tests/test_audio_io.py
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


class StuckStream:
    """An output device that has stopped responding, as one really does."""

    def __init__(self, abort_works: bool = False):
        self.closed = False
        self.abort_works = abort_works
        self.aborted = False
        self.released = threading.Event()

    def abort(self):
        self.aborted = True
        if self.abort_works:
            self.released.set()      # a healthy driver lets the writer go

    def close(self):
        self.closed = True

    def stop(self):
        pass


def main() -> int:
    # Import late: audio.io pulls sounddevice, which must not be touched further.
    from audio.io import Speaker

    # --- abort() must not wait on a writer that is never coming back ----------
    spk = Speaker()
    stream = StuckStream(abort_works=False)
    spk._stream, spk._rate = stream, 24000

    holding = threading.Event()

    def stuck_writer():
        with spk._wlock:              # exactly what _write does
            holding.set()
            stream.released.wait(timeout=8)   # blocked in PortAudio

    t = threading.Thread(target=stuck_writer, daemon=True)
    t.start()
    holding.wait(timeout=3)

    started = time.time()
    spk.abort()
    took = time.time() - started

    check("abort() returns while the writer is still stuck", took < 3.0,
          f"took {took:.1f}s")
    check("...within its own wait budget",
          took < Speaker._LOCK_WAIT_S + 1.5, f"took {took:.1f}s")
    check("...and it did ask the driver to let go", stream.aborted)
    check("...and the dead stream is dropped, so the next play reopens",
          spk._stream is None and spk._rate is None, spk._stream)

    stream.released.set()
    t.join(timeout=3)

    # --- close() had the identical bug, reachable from _ensure() -------------
    spk2 = Speaker()
    stream2 = StuckStream(abort_works=False)
    spk2._stream, spk2._rate = stream2, 24000
    holding2 = threading.Event()

    def stuck_writer2():
        with spk2._wlock:
            holding2.set()
            stream2.released.wait(timeout=8)

    t2 = threading.Thread(target=stuck_writer2, daemon=True)
    t2.start()
    holding2.wait(timeout=3)

    started = time.time()
    spk2.close()
    took = time.time() - started
    check("close() does not block either", took < 3.0, f"took {took:.1f}s")
    check("...and also drops the stream", spk2._stream is None)
    stream2.released.set()
    t2.join(timeout=3)

    # --- and abandoned must MEAN abandoned -----------------------------------
    # The first version of the fix logged "abandoning the stream" and then called
    # stream.close() anyway, on both paths. Closing a PortAudio stream while
    # another thread is blocked inside write() on it frees a C resource out from
    # under that thread. On 2026-08-31 at 07:01 the device failed again, the
    # event loop stayed up exactly as designed - and the process then died 20
    # seconds later with no traceback. Not closing costs a handle; closing costs
    # the whole process.
    from audio.io import _ORPHANS
    spk5 = Speaker()
    stuck = StuckStream(abort_works=False)
    spk5._stream, spk5._rate = stuck, 24000
    holding5 = threading.Event()

    def writer5():
        with spk5._wlock:
            holding5.set()
            stuck.released.wait(timeout=8)

    t5 = threading.Thread(target=writer5, daemon=True)
    t5.start()
    holding5.wait(timeout=3)
    before = len(_ORPHANS)
    spk5.abort()
    check("a stream is NOT closed under a live writer", not stuck.closed,
          f"closed={stuck.closed}")
    check("...it is kept alive so the GC cannot close it either",
          len(_ORPHANS) == before + 1, len(_ORPHANS))
    check("...and the orphan is the very stream we abandoned",
          _ORPHANS[-1] is stuck)
    stuck.released.set()
    t5.join(timeout=3)

    # --- a stuck write must not eat the pool everything else uses ------------
    # asyncio.to_thread runs on the DEFAULT executor, which is also where all 58
    # sync tool handlers run. A write that never returns leaks its thread for
    # good, so a run of dead output devices would quietly starve the pool until
    # nothing could read a file or the clipboard - and nobody would connect that
    # to the speakers. The writer has its own small pool, replaced when its
    # threads are all lost.
    import audio.io as aio
    aio._writer_pool = None
    aio._writers_lost = 0

    first = aio._writer_executor()
    check("the writer has a pool of its own", first is not None)
    check("...which is not the default one",
          "jarvis-audio-write" in str(first._thread_name_prefix), first)
    check("...and is reused while healthy", aio._writer_executor() is first)

    aio._writer_lost()
    check("one lost thread does not throw the pool away",
          aio._writer_executor() is first, aio._writers_lost)

    aio._writer_lost()                     # now all of them are stuck
    replacement = aio._writer_executor()
    check("once every writer is stuck the pool is replaced",
          replacement is not first)
    check("...the stuck one is abandoned, never joined",
          first in aio._ORPHAN_POOLS)
    check("...and the count resets for the new pool", aio._writers_lost == 0)

    # --- the healthy path still closes properly ------------------------------
    spk3 = Speaker()
    good = StuckStream(abort_works=True)
    spk3._stream, spk3._rate = good, 24000
    spk3.abort()
    check("a healthy device is closed, not merely abandoned", good.closed, good.closed)
    check("...and its stream is cleared", spk3._stream is None)

    # --- and calling either on nothing is harmless ---------------------------
    spk4 = Speaker()
    spk4.abort()
    spk4.close()
    check("abort/close with no stream do nothing", spk4._stream is None)

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
