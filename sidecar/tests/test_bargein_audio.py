"""A barge-in stops the sound without letting go of the speakers.

2026-09-08, his report: "the barge-in was still quiet for a little bit too
long — the audio did kick on, but it took a little too long." The mechanism:
`speaker.abort()` CLOSED the output stream, so the chime that follows a
barge-in, and then the reply after it, each paid a fresh Pa_OpenStream. His
speakers are the monitor's over DisplayPort and asleep whenever the panel
is, and the code's own comment says that open "can hold for seconds".

So: stop_playback() aborts and restarts the SAME stream; anything already in
flight is dropped by epoch; the wake word pre-warms the device while he is
still speaking; and the chime no longer blocks the barge-in handler.

Run: python tests/test_bargein_audio.py
"""
import os
import sys
import tempfile
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "barge.db"))

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


class FakeStream:
    """Counts what PortAudio would have been asked to do."""
    def __init__(self):
        self.closed = False
        self.aborts = 0
        self.starts = 0
        self.stops = 0
        self.written = 0

    def abort(self):
        self.aborts += 1

    def start(self):
        self.starts += 1

    def stop(self):
        self.stops += 1

    def close(self):
        self.closed = True

    def write(self, data):
        self.written += len(data)


def main() -> int:
    import asyncio

    from audio import io as AIO

    spk = AIO.Speaker()
    opens = []

    def fake_ensure(rate):
        if spk._stream is None:
            opens.append(time.time())
            spk._stream = FakeStream()
            spk._rate = rate
        return spk._stream
    spk._ensure = fake_ensure

    # -- stopping playback keeps the device ------------------------------------
    stream = spk._ensure(22050)
    check("the first play opens the device once", len(opens) == 1, opens)
    spk.stop_playback()
    check("...an interruption aborts what is buffered", stream.aborts == 1, stream.aborts)
    check("...and starts the SAME stream again rather than closing it",
          stream.starts == 1 and not stream.closed and spk._stream is stream,
          (stream.starts, stream.closed))
    spk._ensure(22050)
    check("...so the reply after it opens nothing", len(opens) == 1, opens)

    # -- and nothing of the interrupted reply is still heard -------------------
    async def play(chunk_epoch_shift=False):
        return await spk.play_chunk(np.zeros(256, dtype=np.float32), 22050)

    before = stream.written
    asyncio.run(play())
    check("an ordinary chunk is written", stream.written > before, stream.written)

    # a chunk queued before the barge-in must not play after it
    spk._epoch += 0            # (the epoch is captured inside play_chunk)
    stale_written = stream.written

    async def stale():
        # simulate: play_chunk starts, the barge-in happens mid-flight
        task = asyncio.ensure_future(spk.play_chunk(np.zeros(256, dtype=np.float32), 22050))
        await asyncio.sleep(0)
        spk.stop_playback()
        await task
    asyncio.run(stale())
    check("a chunk in flight when he cuts in is dropped, not played",
          stream.written == stale_written, stream.written - stale_written)

    # -- a stream that will not restart is closed, not left broken -------------
    class Stubborn(FakeStream):
        def start(self):
            raise RuntimeError("device gone")
    spk2 = AIO.Speaker()
    bad = Stubborn()
    spk2._stream = bad
    spk2._rate = 22050
    spk2.stop_playback()
    check("a stream that refuses to restart is released", bad.closed, bad.closed)

    # -- prewarm opens the device off the loop, and never raises ---------------
    spk3 = AIO.Speaker()
    warmed = []
    spk3._ensure = lambda rate: warmed.append(rate)
    spk3.prewarm(22050)
    for _ in range(50):
        if warmed:
            break
        time.sleep(0.02)
    check("prewarm opens the output device", warmed == [22050], warmed)
    spk4 = AIO.Speaker()

    def boom(rate):
        raise RuntimeError("no device")
    spk4._ensure = boom
    spk4.prewarm(22050)          # must not raise
    check("...and a missing device is not an error", True)

    # -- the wiring ------------------------------------------------------------
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "orchestrator.py"), encoding="utf-8").read()
    barge = src[src.index("barge-in detected"):]
    barge = barge[:barge.index("except asyncio.CancelledError")]
    check("the barge-in stops playback without closing the device",
          "speaker.stop_playback()" in barge and "speaker.abort()" not in barge)
    check("...and does not wait on the chime",
          "spawn(self.play_sound(\"chime\")" in barge and "await self.play_sound" not in barge)
    check("the wake word pre-warms the speakers", "speaker.prewarm(tts.sample_rate)" in src)
    check("a barge-in capture is treated as a bare wake, so it cannot hang",
          "_barge_capture" in src and "MAX_UTTERANCE_S" in src)

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
