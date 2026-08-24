"""Guards for how JARVIS addresses him.

Measured from 97 JARVIS lines across the four films: 37% carry "sir", median 7
words per line, and it sits either at the front of something he raises himself
("Sir, the city is taking fire.") or at the end of an acknowledgement
("Very good, sir."). Reflex replies never pass through the language model, so the
system prompt alone cannot produce any of this — it has to be applied centrally.
"""
import os
import random
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import brain.skills as skills  # noqa: E402
from brain.skills import honorific, polish  # noqa: E402


def solo(*args, **kw):
    """One line in isolation — the 'never two running' latch off."""
    skills._last_honorific[0] = False
    return honorific(*args, **kw)

fails = []


def check(name, cond):
    print(("  PASS  " if cond else "  FAIL  ") + name)
    if not cond:
        fails.append(name)


def rate_of(lines, n=400):
    random.seed(11)
    out = [polish(random.choice(lines)) for _ in range(n)]
    return sum(1 for x in out if re.search(r"\bsir\b", x, re.I)) / len(out), out


ACKS = ["Muted.", "Done.", "Noted.", "Volume set to 40 percent.", "Opening Spotify.",
        "It's 11:22 AM.", "Locking.", "Unmuted."]

r, out = rate_of(ACKS)
check(f"honorific rate is film-like (~1 in 3, got {r:.0%})", 0.20 <= r <= 0.50)
check("honorific only ever sits at the end of an acknowledgement",
      all(re.search(r",\s+sir[.?!]$", x) for x in out if re.search(r"\bsir\b", x, re.I)))
check("never twice in one line", all(len(re.findall(r"\bsir\b", x, re.I)) <= 1 for x in out))

random.seed(5)
seq = [polish(ACKS[i % len(ACKS)]) for i in range(60)]
has = [bool(re.search(r"\bsir\b", x, re.I)) for x in seq]
check("never two lines running", not any(has[i] and has[i + 1] for i in range(len(has) - 1)))

check("an alert is addressed at the front, not the back",
      solo("The disk is nearly full.", kind="alert", rate=1.0) == "Sir, the disk is nearly full.")
check("a line that already says sir is left alone",
      solo("Very good, sir.", rate=1.0) == "Very good, sir.")
check("a long report is left alone (an honorific there reads as clutter)",
      solo("CPU is at 12 percent. Memory is at 61 percent. Disk C has 1.5 terabytes free.",
                rate=1.0).count("sir") == 0)
check("a question keeps its question mark",
      solo("Shall I open it?", rate=1.0) == "Shall I open it, sir?")

random.seed(2)
regrets = [polish("I couldn't open Spotify.") for _ in range(200)]
softened = sum(1 for x in regrets if x.startswith("I'm afraid"))
check(f"bad news is softened about half the time (got {softened / len(regrets):.0%})",
      0.30 <= softened / len(regrets) <= 0.70)
check("softening keeps the pronoun capitalised",
      not any(re.search(r"\bafraid i\b", x) for x in regrets))
check("bad news leans harder on the honorific than a plain acknowledgement",
      sum(1 for x in regrets if "sir" in x) / len(regrets) > r)

src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "llm", "prompts.py"),
           encoding="utf-8").read()
check("the system prompt no longer tells the model to be sparing with it",
      "sparingly" not in src)

# Frequency is decided in code and stated per turn — the model proved unable to pace
# itself (11% when asked to, 60% and seven-in-a-row when left to read its own replies).
from llm.prompts import turn_context  # noqa: E402

check("a turn can be told to use it", 'sir" exactly once' in turn_context("", True))
check("a turn can be told not to", 'do not use "sir"' in turn_context("", False))
check("with no decision the turn says nothing about it", "sir" not in turn_context(""))

random.seed(9)
skills._last_honorific[0] = False
draws = [skills.want_honorific() for _ in range(400)]
r2 = sum(draws) / len(draws)
check(f"the per-turn decision matches the reflex rate ({r2:.0%})", 0.20 <= r2 <= 0.50)
check("the per-turn decision never fires twice running",
      not any(draws[i] and draws[i + 1] for i in range(len(draws) - 1)))

check("history fed back to the model is scrubbed, so nothing snowballs",
      "without_honorific" in open(
          os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "orchestrator.py"),
          encoding="utf-8").read())

print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
