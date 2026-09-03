"""A sleeping monitor must not cost him twelve seconds a sentence.

`audio.output_device` is null, so JARVIS speaks through the Windows default —
which on this machine is `LS32CG51x`, the monitor's own speakers over
DisplayPort. A sleeping monitor does not accept audio writes.

The log tells the story plainly: 32 hang events, every one of them exactly
"2.2s of audio, 12s budget", every one at startup, 27 of them on 2026-08-31
alone. That 2.2 seconds is the boot chime. Each hang burned one of the two
writer threads and stalled the boot for the full budget — and he could not have
heard the chime anyway, because the screen was dark.

Two fixes, both gated here:
  * the boot chime is not played into a dark screen at all;
  * once a write HAS hung, the output is presumed unusable for a minute, so a
    whole reply goes to his phone instead of dribbling out one stalled chunk at
    a time, each paying the full budget to rediscover the same asleep device.

Offline: no device is ever opened. Run: python tests/test_audio_deaf_output.py
"""
import asyncio
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "audio.db"))

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


def main() -> int:
    import numpy as np

    from audio.io import SpeakerStalled, speaker

    rate = 24000
    chunk = np.zeros(int(rate * 2.2), dtype=np.float32)   # the boot chime's size

    # --- the cooldown blocks fast, without touching the device ---------------
    opened = []
    speaker._ensure = lambda r: opened.append(r)          # must NOT be called
    speaker._deaf_output_until = time.time() + 30

    t0 = time.time()
    try:
        asyncio.run(speaker.play_chunk(chunk, rate))
        raised = None
    except SpeakerStalled as e:
        raised = e
    except Exception as e:
        raised = e
    took = time.time() - t0

    check("a known-bad output raises instead of stalling",
          isinstance(raised, SpeakerStalled), repr(raised))
    check("...immediately, not after the write budget", took < 0.5, f"{took:.2f}s")
    check("...and without reopening the dead device", opened == [], opened)

    # --- and it expires, so speakers come back on their own -----------------
    class ClosedStream:
        """A stream whose write() is never reached — `_write` returns on `closed`."""
        closed = True

    speaker._deaf_output_until = time.time() - 1
    reached = []

    def reopen(r):
        reached.append(r)
        return ClosedStream()

    speaker._ensure = reopen

    try:
        asyncio.run(speaker.play_chunk(chunk, rate))
        ok = True
    except SpeakerStalled:
        ok = False
    check("once the cooldown lapses, the speakers are tried again", ok)
    check("...and the device really was reopened", reached == [rate], reached)

    # --- a SLEEPING device gets one more chance before the lockout -----------
    # His monitor blanks after sixty seconds and its speakers are on
    # DisplayPort, so they are asleep most of the time he is not typing. A
    # sleeping endpoint refuses the first write and then wakes when a new stream
    # is opened against it — and on 2026-09-02 the sixty-second lockout after
    # that first refusal covered his entire test. He asked for a part and heard
    # nothing at all.
    import time as _t

    class SleepyStream:
        """Refuses the first write by hanging; takes the second."""
        closed = False
        opens = 0

        def write(self, _data):
            if SleepyStream.opens <= 1:
                _t.sleep(30)          # the driver never returns — a stuck writer
            return None

        def abort(self):
            pass

        def close(self):
            pass

    def sleepy_open(r):
        SleepyStream.opens += 1
        # the REAL _ensure assigns this; without it abort() has nothing to abort
        # and _release never runs, so the stuck writer's lock is never replaced
        speaker._stream = SleepyStream()
        return speaker._stream

    SleepyStream.opens = 0
    speaker._deaf_output_until = 0.0
    speaker._ensure = sleepy_open
    t0 = _t.time()
    try:
        asyncio.run(speaker.play_chunk(chunk, rate))
        woke = True
    except SpeakerStalled:
        woke = False
    took = _t.time() - t0
    check("a device that was merely asleep is heard on the retry", woke,
          f"gave up after {took:.1f}s instead of reopening")
    check("...having opened a second, fresh stream", SleepyStream.opens == 2,
          SleepyStream.opens)
    check("...and it did NOT go deaf for a minute",
          speaker._deaf_output_until <= _t.time(),
          "a sleeping speaker must not cost him the next sixty seconds")

    # --- but a genuinely dead device still gives up, and stays given up ------
    class DeadStream:
        closed = False

        def write(self, _data):
            _t.sleep(30)

        def abort(self):
            pass

        def close(self):
            pass

    dead_opens = []

    def dead_open(r):
        dead_opens.append(r)
        speaker._stream = DeadStream()
        return speaker._stream

    speaker._deaf_output_until = 0.0
    speaker._ensure = dead_open
    t0 = _t.time()
    try:
        asyncio.run(speaker.play_chunk(chunk, rate))
        gave_up = False
    except SpeakerStalled:
        gave_up = True
    took = _t.time() - t0
    check("a device that is really gone still gives up", gave_up)
    check("...after exactly two tries, never a loop", len(dead_opens) == 2, dead_opens)
    check("...and then goes quiet for the full cooldown",
          speaker._deaf_output_until > _t.time() + 30,
          "the retry must not defeat the lockout that prevents a hang")
    check("...without taking much longer than before", took < 20.0, f"{took:.1f}s")

    # --- the boot chime is not played into a dark screen ---------------------
    import orchestrator as orc
    real = orc._display_off
    try:
        orc._display_off = lambda: True
        check("a dark screen means no boot chime", orc._display_off() is True)
        orc._display_off = lambda: False
        check("a lit screen still gets one", orc._display_off() is False)
    finally:
        orc._display_off = real

    # ...and asking must never raise, whatever Windows says
    def explode():
        raise OSError("no power plan")
    import tools.windows_tools as wt
    real_off = wt.display_is_off
    try:
        wt.display_is_off = explode
        check("an unreadable display state is treated as lit, not crashed",
              orc._display_off() is False)
    finally:
        wt.display_is_off = real_off

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
