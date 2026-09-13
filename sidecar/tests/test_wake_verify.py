"""The wake word must be HEARD, not just scored.

2026-09-13. In the five days after release 63 the wake model fired 22 times,
at 0.60 to 1.00, and almost none of them were him: "Yeah. But Daniel, he
isn't happy" at 1.00, "Did you click on it? Oh yeah." at 0.93, seven fires on
nothing at all. Each one lit the screen, played the chime and listened to a
room. *"I could be watching a show when it turns on."*

No threshold separates those from his voice - the scores overlap completely.
The recogniser does: after the model fires, Parakeet is asked whether the name
is in the pre-roll, and only then does anything happen. The strings below are
what Parakeet actually produced, out of his transcript and out of a 48-clip
probe; the live section runs the real recogniser when its files are here.

Run: python tests/test_wake_verify.py
"""
import os
import re
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "wakev.db"))

ROOT = Path(__file__).resolve().parent.parent
fails, skips = [], []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


def main() -> int:
    from audio.wake import _dl, name_in, strip_wake_name

    print("\n-- what Parakeet writes when he says the name --")
    for heard in ("Hey Jarvis.", "Jarvis.", "Hey Jarvis, what time is it?", "Hey Jarvis!",
                  "Jarvis?", "Hey Jarvis", "Jovis", "Travis.", "Travis, go to sleep.",
                  "Jarvis, turn off the camera.", "Thought you did me. Hey Jarvis, what time is it?",
                  "dn't have time to bounty. Hey Jarvis, what's the weather in?",
                  "Jarvi", "Jarvus", "Hey Jarvis's", "JARVIS"):
        check(f"{heard!r} has the name in it", name_in(heard))

    print("\n-- what it writes when the television is on --")
    # every one of these is a real capture after a wake he did not say
    for tv in ("Yeah.", "Yeah. Mm-hmm.", "Yeah. I really thought that.", "Did you click on it? Oh yeah.",
               "Yeah. But Daniel, he isn't happy.", "And well you've taken everything out.",
               "Ooh, an obstacle course. Aww I was finding", "I fucking remember fucking stupid.",
               "just had a chair literally right next to me and it ran away.",
               "Look at me, where are you? Oh I was", "But um To be honest, there were some moments",
               "If I don't know why she does it. Oh my god, no very fast.", "Circle.", "Stranger.",
               "Everyone will", "Hey, what you doing?", "Oh I see a check.", ""):
        check(f"{tv[:40]!r} does not", not name_in(tv))
    # names the television says that are two edits from his - and must not count
    for near in ("Harris", "Marvin", "Elvis", "Jarred", "service", "travel", "Mavis", "Jervais"):
        check(f"...and neither does {near!r}", not name_in(near), _dl(near.lower(), "jarvis"))

    print("\n-- the name comes out of the sentence, wherever it landed --")
    for said, want in (
            ("Hey Jarvis, what time is it?", "what time is it?"),
            ("Jarvis, turn off the camera.", "turn off the camera."),
            ("Thought you did me. Hey Jarvis, what time is it?", "what time is it?"),
            ("Travis, go to sleep.", "go to sleep."),
            ("Jarvis.", ""),
            ("Hey Jarvis", ""),
            ("um, Jarvis", ""),
            ("Thought you did me. Hey Jarvis", ""),
            ("what time is it, Jarvis?", "what time is it"),
            ("okay Jarvis what's the weather", "what's the weather"),
            ("remind me to call Travis tomorrow", "remind me to call Travis tomorrow"),
            ("open chrome", "open chrome"),
            ("", "")):
        got = strip_wake_name(said)
        check(f"{said!r} -> {want!r}", got == want, got)

    print("\n-- nothing happens until the name is heard --")
    orch = (ROOT / "orchestrator.py").read_text(encoding="utf-8")
    loop = orch[orch.index("wake word detected (%.2f)"):]
    block = loop[:loop.index("await self.play_sound(\"chime\")") + 40]
    i_check = block.index("await self._name_heard(")
    for what in ("await self.wake_if_sleeping()", "await self._surface()",
                 'await bus.emit("wake"', 'await self.play_sound("chime")', "self._listen_flag.set()"):
        check(f"{what} waits for the name", block.index(what) > i_check, (block.index(what), i_check))
    check("a rejection is counted", "self.wakes_rejected += 1" in block)
    check("...and shown, not just logged", 'reason="no name"' in block and "wake_suppressed" in block)
    check("...and takes the fresh wake score with it", "self._wake_score_fresh = None" in block[i_check:])
    check("the pre-roll is long enough to hold the name", "MIC_RATE * 2.5 / 1024" in orch)
    check("the queue is drained at the snapshot, so nothing said during the check is lost",
          block.index("mic.drain()") < i_check)
    cap = orch[orch.index("async def _capture_utterance_inner"):]
    cap = cap[:cap.index("while True:")]
    check("...and the capture does not drain again when it has a lead-in",
          "if lead_in is None or not len(lead_in):" in cap and cap.index("if lead_in is None") < cap.index("mic.drain()"))
    nh = orch[orch.index("async def _name_heard"):]
    nh = nh[:nh.index("async def _barge_in_watch")]
    check("the check fails OPEN when the recogniser errors", "return True, \"\"" in nh and "except Exception" in nh)
    check("...and has a deadline a real wake can afford", "timeout=1.5" in nh)
    check("...and says what it heard when it says no", "wake rejected (%.2f): heard %r" in nh)
    check("the voice paths strip the name wherever it is", orch.count("strip_wake_name(") >= 3)
    main_py = (ROOT / "main.py").read_text(encoding="utf-8")
    check("/health carries the count", '"false_wakes_rejected"' in main_py)
    store = (ROOT.parent / "src" / "state" / "store.ts").read_text(encoding="utf-8")
    check("the HUD says it plainly", "thought I heard my name; I hadn't" in store)

    print("\n-- the real recogniser, on real audio --")
    voices = Path(os.path.expandvars(r"%APPDATA%\JARVIS\voices\kokoro"))
    if not (voices / "kokoro-v1.0.onnx").exists():
        skips.append("the live recogniser (no kokoro voices to make clips with)")
        print("  SKIPPED - no kokoro voices here to synthesise clips with")
    else:
        import asyncio
        import numpy as np
        from kokoro_onnx import Kokoro
        from audio.stt import stt
        k = Kokoro(str(voices / "kokoro-v1.0.onnx"), str(voices / "voices-v1.0.bin"))

        def say(text, voice="am_michael", speed=1.0):
            s, sr = k.create(text, voice=voice, speed=speed)
            idx = np.linspace(0, len(s) - 1, int(len(s) * 16000 / sr)).astype(int)
            return s[idx].astype(np.float32)[-int(16000 * 2.5):]

        async def run():
            lat = []
            for v, ph in (("am_michael", "Hey Jarvis"), ("af_sarah", "Jarvis"), ("bm_george", "Hey Jarvis, what time is it"),
                          ("am_adam", "Jarvis, go to sleep"), ("af_bella", "Hey Jarvis")):
                t = time.perf_counter()
                heard = await stt.transcribe(say(ph, v))
                lat.append(time.perf_counter() - t)
                check(f"{v} saying {ph!r} is heard", name_in(heard), heard)
            for ph in ("Did you click on it? Oh yeah.", "Yeah, but Daniel, he isn't happy.",
                       "You meat puncher, got a speeding corvette over eighty."):
                heard = await stt.transcribe(say(ph, "am_michael"))
                check(f"{ph[:34]!r} is not", not name_in(heard), heard)
            med = sorted(lat)[len(lat) // 2]
            check("...and the check is faster than a chime", med < 0.6, f"{med * 1000:.0f} ms")
        asyncio.run(run())

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}"
          f"{f' ({len(skips)} skipped)' if skips else ''}")
    for s in skips:
        print("  skipped:", s)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
