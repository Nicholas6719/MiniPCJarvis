"""A voice note from his phone, decoded.

Telegram sends OGG/Opus at 48 kHz; nothing else in JARVIS produces or reads
that, and the recogniser wants 16 kHz mono float32. The fixture is a real
Opus file in exactly the shape Telegram delivers.

No model is loaded here — this is the DECODE, which is the part that is easy to
get silently wrong (a wrong sample rate transcribes as gibberish rather than
failing). The recogniser end of it is covered by tests/telegram_e2e.py.

Run: python tests/test_voice_note.py
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from audio.decode import RATE, Undecodable, to_pcm16k  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "fixtures", "voice_note.oga")

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


def main() -> int:
    raw = open(FIXTURE, "rb").read()
    a = to_pcm16k(raw)

    check("an Opus voice note decodes at all", isinstance(a, np.ndarray) and len(a) > 0)
    check("...to float32, which is what the recogniser takes", a.dtype == np.float32,
          a.dtype)
    check("...and to one channel", a.ndim == 1, a.shape)
    # The fixture is a ~2.2 s clip. A wrong sample rate is the silent failure
    # here: it decodes fine and transcribes as nonsense, so check the DURATION,
    # which is what actually catches it.
    secs = len(a) / RATE
    check("...at the right rate, so it is the right length", 1.6 < secs < 2.8,
          f"{secs:.2f}s at {RATE} Hz")
    peak = float(np.abs(a).max())
    check("nothing clips", peak <= 1.0, peak)
    check("a quiet phone recording is brought up to a usable level", peak > 0.5, peak)

    # --- rubbish must be refused, not fed to the recogniser -------------------
    for bad, label in ((b"", "nothing at all"),
                       (b"this is not audio", "junk bytes"),
                       (b"\x00" * 8000, "a block of zeros"),
                       (raw[:40], "a truncated file")):
        try:
            to_pcm16k(bad)
            check(f"{label} is refused", False, "it returned audio")
        except Undecodable:
            check(f"{label} is refused", True)
        except Exception as e:                        # noqa: BLE001
            check(f"{label} is refused", False, f"raised {type(e).__name__} instead")

    # silence is not a recording, and asking the recogniser about it wastes a turn
    import io

    import soundfile as sf
    buf = io.BytesIO()
    sf.write(buf, np.zeros(16000, dtype=np.float32), 16000, format="WAV")
    try:
        to_pcm16k(buf.getvalue())
        check("a silent recording is refused", False, "it returned audio")
    except Undecodable:
        check("a silent recording is refused", True)

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
