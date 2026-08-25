"""How many natural ways of dismissing him actually reach sleep mode.

Most of these are deliberately NOT seed phrasings — the point is to check that the brain
generalises, not that it memorised a list. The negatives matter just as much: "put the
COMPUTER to sleep" must stay with power_action, and asking about sleep as a topic must
not dismiss him.
Run: python tests/sleep_coverage.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brain.router import brain  # noqa: E402

SHOULD_SLEEP = [
    "go to sleep", "jarvis go to sleep", "you can go to sleep now", "enter sleep mode",
    "go into sleep mode", "sleep mode", "activate sleep mode", "time to sleep",
    "that's all for now", "that's all", "that's it for now", "that's everything",
    "that will be all", "that'll be all for now", "that's all i need",
    "that's all i needed thanks", "ok that's all for now", "nothing else for now",
    "nothing more for now", "i'm done for now", "we're done here", "i'm all set",
    "stand down", "stand by", "dismissed", "you're dismissed", "you can go now",
    "take a break", "you can rest now", "go rest", "take five",
    "goodnight jarvis", "good night jarvis", "night jarvis", "goodnight",
    "minimize yourself", "hide yourself", "make yourself scarce", "get out of the way",
    "go away for now", "step aside", "leave me be", "i don't need you right now",
    "out of sight for now", "see you later jarvis", "bye for now", "later jarvis",
    "go dormant", "power down", "go quiet", "that's enough for now",
]

SHOULD_NOT_SLEEP = [
    # the machine, not him
    ("put the computer to sleep", None), ("put my pc to sleep", None),
    ("sleep the computer", None), ("shut down the computer", None),
    ("hibernate the pc", None),
    # sleep as a subject
    ("give me a tip for sleeping better", None),
    ("how many hours should i sleep", None),
    ("what time should i go to bed", None),
    # no time given, so the reminder slots can't fill and it goes to the LLM — the point
    # here is only that it must not dismiss him
    ("set a reminder for bedtime", None),
    # unrelated commands that share words
    ("close notepad", "close_app"), ("lock the computer", "lock"),
    ("what time is it", "time"), ("minimize chrome", None),
]


# Deliberately NOT seeded, and written after the seeds were fixed. This is the honest
# measure: once a phrasing becomes a seed, matching it proves memory, not understanding.
# The brain also learns from real use, so live phrasings keep widening this over time.
HELD_OUT = [
    "alright that's enough", "ok you can go", "i'm good for now", "we're all done",
    "nothing else right now", "that's a wrap", "go to standby", "enter standby mode",
    "back to standby", "take the night off", "you're off duty", "sleep now",
    "get some sleep", "rest now jarvis", "goodbye for now", "i'm done talking",
    "no more questions for now", "disappear for now", "tuck yourself away",
    "that's it thanks", "thanks that's all", "wrap it up for now",
    "you can switch off", "no need for you right now", "give it a rest",
]


async def main() -> int:
    await brain.load()
    hits, misses = 0, []
    for phrase in SHOULD_SLEEP:
        d = await brain.decide(phrase)
        name = d[0].name if d else None
        if name == "sleep":
            hits += 1
        else:
            misses.append((phrase, name, brain.last_match["confidence"]))

    wrong = []
    for phrase, want in SHOULD_NOT_SLEEP:
        d = await brain.decide(phrase)
        name = d[0].name if d else None
        if name == "sleep" or (want is not None and name != want):
            wrong.append((phrase, name, want))

    print(f"  dismissals reaching sleep mode : {hits}/{len(SHOULD_SLEEP)}"
          f" = {100 * hits / len(SHOULD_SLEEP):.0f}%")
    for p, got, conf in misses:
        print(f"      MISS  {p:34} -> {got} ({conf})")
    print(f"  things that must NOT sleep     : {len(SHOULD_NOT_SLEEP) - len(wrong)}/{len(SHOULD_NOT_SLEEP)}")
    for p, got, want in wrong:
        print(f"      WRONG {p:34} -> {got} (want {want})")

    held = 0
    held_misses = []
    for phrase in HELD_OUT:
        d = await brain.decide(phrase)
        name = d[0].name if d else None
        if name == "sleep":
            held += 1
        else:
            held_misses.append((phrase, name, brain.last_match["confidence"]))
    print(f"  held-out (never seeded)        : {held}/{len(HELD_OUT)}"
          f" = {100 * held / len(HELD_OUT):.0f}%")
    for p, got, conf in held_misses:
        print(f"      miss  {p:34} -> {got} ({conf})")

    # Held-out is the generalisation measure and is allowed to be imperfect — anything it
    # misses simply falls through to the LLM, which still understands and does it.
    ok = (hits >= int(0.9 * len(SHOULD_SLEEP)) and not wrong
          and held >= int(0.7 * len(HELD_OUT)))
    print("\n" + ("ALL PASS" if ok else "FAILED"))
    return 0 if ok else 1

sys.exit(asyncio.run(main()))
