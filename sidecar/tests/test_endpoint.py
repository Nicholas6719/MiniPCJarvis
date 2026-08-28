"""Semantic end-of-turn decisions. Offline and pure — no audio, no models.

The two failures that matter are asymmetric: cutting someone off mid-sentence
loses their words, while waiting too long only costs a beat. So a wrong PATIENT
is a nuisance and a wrong FAST is a bug, and the cases below are weighted that
way. Run: python tests/test_endpoint.py"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "gate.db"))
from audio.endpoint import FAST, NORMAL, PATIENT, budget_for  # noqa: E402

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


# Mid-thought: he must WAIT. Cutting any of these off loses what came next.
UNFINISHED = [
    "what is the weather in", "remind me to", "play the", "set a reminder for",
    "open the", "and then", "so maybe", "search for", "tell me about",
    "i want to", "can you", "um", "the", "put it on my", "send a message to",
    "what's the price of", "look up the", "how do i",
]

# Finished: waiting a full second here is the dead air people complain about.
FINISHED = [
    "what time is it", "how tall is the eiffel tower", "what's the date",
    "tell me about the history of rome in detail please",
    "what is the capital of australia", "show me pictures of mars",
    "how many legs does a spider have", "what did i tell you about my car",
]


def main() -> int:
    for t in UNFINISHED:
        secs, why = budget_for(t, False)
        check(f"waits on {t!r}", secs == PATIENT, f"{secs}s ({why})")
    for t in FINISHED:
        secs, why = budget_for(t, False)
        check(f"cuts fast on {t!r}", secs == FAST, f"{secs}s ({why})")

    # A recognised command outranks every surface cue
    check("a brain hit ends the turn immediately",
          budget_for("play", True)[0] == FAST)
    # ...but an unrecognised fragment still waits
    check("an unrecognised fragment still waits",
          budget_for("play the", False)[0] == PATIENT)
    # ...and a HALF-SPOKEN command waits even though the brain matched it: the
    # reminder skill answers to "remind me to" perfectly, and cutting in there
    # is exactly the rudeness this is meant to stop.
    for stem in ("remind me to", "set a timer for", "put a reminder in my", "open the"):
        check(f"a brain hit does not override {stem!r}",
              budget_for(stem, True)[0] == PATIENT, budget_for(stem, True))
    # Parakeet punctuates every clip it is handed, finished or not, so a full
    # stop after a preposition proves nothing.
    check("a full stop after a dangling word is still dangling",
          budget_for("Remind me to.", True)[0] == PATIENT)
    check("a question mark after a dangling word is still dangling",
          budget_for("What's the weather in?", True)[0] == PATIENT)
    check("a question mark after a real ending still ends it",
          budget_for("What's the weather in Boston?", False)[0] == FAST)
    check("a full stop after a real ending still ends it",
          budget_for("Remind me to stretch.", False)[0] == FAST)
    # Nothing heard yet falls back to the old fixed window
    check("silence alone keeps the fixed window", budget_for("", False)[0] == NORMAL)
    check("a shrug keeps the fixed window", budget_for("hello", False)[0] == NORMAL)
    # Punctuation carries the same signal
    check("a trailing comma waits", budget_for("first, ", False)[0] == PATIENT)
    check("a full stop ends it", budget_for("that's all.", False)[0] == FAST)
    # Never slower than the old default for a finished sentence
    check("FAST really is faster than the old window", FAST < NORMAL < PATIENT)
    # Genuinely ambiguous fragments keep the fixed window — neither guess is
    # defensible, and the old behaviour is the safe default.
    for t2 in ("turn the volume", "hello", "the meeting"):
        check(f"ambiguous {t2!r} keeps the fixed window",
              budget_for(t2, False)[0] in (NORMAL, PATIENT))

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
