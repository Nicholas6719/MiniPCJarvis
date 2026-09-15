"""A wake from a dark screen must be HEARD, not just written.

2026-09-15 08:21. He woke JARVIS from a dark screen: the wake was accepted,
the brief went to the screen, the greeting was synthesised (16.6 s of audio,
checked), and every write to the speaker succeeded. He heard nothing. His
speakers are the monitor's, over DisplayPort; the output stream had been
opened by the prewarm while the panel - and so the endpoint - was still
asleep, and a stream bound to a sleeping endpoint plays to nowhere. The old
failure was a write that BLOCKED (detected, abandoned, reopened, heard); this
one was a write that went through to nothing.

Run: python tests/test_speaker_wake.py
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "spk.db"))

ROOT = Path(__file__).resolve().parent.parent
fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


def main() -> int:
    print("\n-- the order of a wake from a dark screen --")
    orch = (ROOT / "orchestrator.py").read_text(encoding="utf-8")
    loop = orch[orch.index("wake word detected (%.2f)"):]
    block = loop[:loop.index('await self.play_sound("chime")') + 40]
    check("darkness is judged before anything is opened",
          "dark = self.sm.state == State.SLEEPING and _display_off()" in block
          and block.index("dark = ") < block.index("speaker.prewarm("))
    check("...and the speakers are NOT prewarmed against a sleeping endpoint",
          "if not dark:\n                        speaker.prewarm(" in block)
    i_wake = block.index("await self.wake_if_sleeping()")
    i_reopen = block.index("await speaker.reopen_when_ready(")
    i_chime = block.index('await self.play_sound("chime")')
    check("after the display is woken, the speakers are reopened", i_wake < i_reopen)
    check("...before the chime, which is the first sound", i_reopen < i_chime)
    check("...and a failure there cannot cost the wake", "except Exception:" in block[i_reopen:i_chime])

    print("\n-- the reopen itself --")
    from audio import io as A
    import audio.output_watch as OW
    check("the endpoint can be asked whether it is active", callable(getattr(OW, "endpoint_active", None)))
    check("...and answers None rather than raising when it cannot be",
          OW.endpoint_active() in (True, False, None), OW.endpoint_active())

    async def drill(states):
        """A fake endpoint that comes back after N polls; the stale stream is closed."""
        spk = A.Speaker.__new__(A.Speaker)
        spk._stream = object()
        spk._rate = 24000
        closed = []
        spk.close = lambda: closed.append(True) or setattr(spk, "_stream", None)
        warmed = []
        spk.prewarm = lambda rate: warmed.append(rate)
        seq = list(states)
        OW.endpoint_active = lambda: seq.pop(0) if seq else True
        waited = await spk.reopen_when_ready(24000, timeout=2.0)
        return closed, warmed, waited

    real = OW.endpoint_active
    try:
        closed, warmed, waited = asyncio.run(drill([False, False, True]))
        check("the stream opened while the endpoint slept is dropped", closed == [True])
        check("...it waits for the endpoint to report active", 0.3 <= waited < 1.5, waited)
        check("...then opens a fresh one", warmed == [24000], warmed)
        closed, warmed, waited = asyncio.run(drill([True]))
        check("an endpoint that is already active costs nothing", waited < 0.3, waited)
        closed, warmed, waited = asyncio.run(drill([False] * 50))
        check("...and one that never comes back is given up on at the deadline", 1.9 <= waited < 3.0, waited)
        closed, warmed, waited = asyncio.run(drill([None]))
        check("when WASAPI cannot be asked, a fixed moment is given instead", 1.4 <= waited < 2.0, waited)
    finally:
        OW.endpoint_active = real

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
