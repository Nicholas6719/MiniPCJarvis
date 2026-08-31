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
