"""What JARVIS actually SAYS, verified by ear rather than by eye.

Synthesizes a line with the real TTS, transcribes the audio back with the real STT, and
checks the words that come out. Reading clean_for_speech's output cannot catch this class
of bug: "1.7 terabytes" looks perfect on screen and Kokoro voices it "one seven terabytes",
because it does not sound the decimal point at all. JARVIS reports free disk space on
every status question, so he was mis-stating it out loud constantly.

Run: python tests/speech_symbols.py
"""
import asyncio
import os
import re
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audio.speech_text import clean_for_speech  # noqa: E402
from audio.stt import stt  # noqa: E402
from audio.tts import tts  # noqa: E402

# (text JARVIS would produce, what the transcription must contain, what it must NOT)
CASES = [
    ("The drive has 1.7 terabytes free.", r"1\.7|one point seven|1 point 7", r"\bone seven\b"),
    ("It is 2.5 gigahertz.", r"2\.5|two point five|2 point 5", r"\btwo five\b"),
    ("The value is 0.5.", r"0\.5|zero point five|point five", None),
    # "$40" / "£25" in the transcript is the STT writing back what it heard as
    # money — only "forty dollars" normalises to that; "dollar forty" cannot.
    ("I have $40.", r"\$40|40 dollars|forty dollars", r"dollar forty"),
    ("Around £25 total.", r"£25|25 pounds|twenty.five pounds", r"pound twenty"),
    ("It is 32°F outside.", r"fahrenheit", None),
    ("It is 20°C outside.", r"celsius", None),
    ("Memory at 73%.", r"73|seventy.three", None),
    ("CPU is at 12 percent.", r"12|twelve", None),
]

fails = []


async def spoken(text: str) -> str:
    cancel = asyncio.Event()
    parts = []
    async for chunk in tts.synthesize_stream(clean_for_speech(text), cancel):
        parts.append(chunk)
    if not parts:
        return ""
    audio = np.concatenate(parts)
    idx = (np.arange(int(len(audio) * 16000 / 24000)) * 24000 / 16000).astype(int)
    return (await stt.transcribe(audio[idx])).strip()


# Clock times can't be checked by transcription: "two oh four" and "two hundred four"
# both come back as "204". So compare the synthesized duration against the two spellings
# and require the RIGHT one to be the closer match.
CLOCKS = [
    ("It's 2:04 PM.", "It's two oh four PM.", "It's two hundred four PM."),
    ("It's 12:05 AM.", "It's twelve oh five AM.", "It's twelve hundred five AM."),
    ("It's 9:07 AM.", "It's nine oh seven AM.", "It's nine hundred seven AM."),
]


async def duration(text: str) -> float:
    cancel = asyncio.Event()
    n = 0
    async for chunk in tts.synthesize_stream(clean_for_speech(text), cancel):
        n += len(chunk)
    return n / 24000.0


async def main() -> int:
    await tts.warmup()
    await stt.warmup()
    for text, must, must_not in CASES:
        heard = await spoken(text)
        ok = bool(re.search(must, heard, re.I))
        if ok and must_not and re.search(must_not, heard, re.I):
            ok = False
        print(("  PASS  " if ok else "  FAIL  ") + f"{text:34} heard: {heard}")
        if not ok:
            fails.append(text)

    # THE CLOCK RULE, BY THE ENGINE IT WAS WRITTEN FOR. The duration comparison
    # relies on Kokoro synthesising the same text to the same length every time;
    # Pocket TTS samples its timing (seeded, but a different text is a different
    # draw), so "2 oh 4" and "2 hundred 4" land within 80 ms of each other and
    # the comparison is a coin toss. Kokoro is still the fallback voice, so the
    # rule is checked there — and the ACTIVE engine gets the one check that
    # does survive transcription: the wrong reading comes back as
    # "hundred"/"under", the right one never does.
    from config import config
    if not isinstance(tts._active(), type(tts.kokoro)):
        for text, _right, _wrong in CLOCKS:
            heard = await spoken(text)
            ok = not re.search(r"\bhundred\b|\bunder\b", heard, re.I)
            print(("  PASS  " if ok else "  FAIL  ") + f"{text:34} heard: {heard}")
            if not ok:
                fails.append(text)
    prev_voice = config.get("tts", "voice")
    config.data.setdefault("tts", {})["voice"] = str(
        config.get("tts", "kokoro_voice", default="bm_george") or "bm_george")
    if not config.data["tts"]["voice"].startswith(("bm_", "bf_", "am_", "af_")):
        config.data["tts"]["voice"] = "bm_george"
    tts.reload()
    try:
        for text, right, wrong in CLOCKS:
            a, g, b = await duration(text), await duration(right), await duration(wrong)
            ok = abs(a - g) < abs(a - b)
            print(("  PASS  " if ok else "  FAIL  ")
                  + f"{text:34} {a:.2f}s vs correct {g:.2f}s / wrong {b:.2f}s  (kokoro)")
            if not ok:
                fails.append(text)
    finally:
        config.data["tts"]["voice"] = prev_voice
        tts.reload()

    print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}"))
    return 1 if fails else 0

sys.exit(asyncio.run(main()))
