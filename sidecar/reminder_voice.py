"""A reminder, in his own words, plainly.

History, because it explains the rule. On 2026-09-01 he asked for his 9 p.m.
retainer reminder to come back in JARVIS's voice, "something different or
similar almost every night", so it went through the LLM at temperature 0.9. Ten
days of his phone (10-27..11-06) show what that became: "ingest your daily
supplements", "lace up your retainers", "don your retainers", "strap on your
retainers", and once, cut off mid-sentence, "Sir, now is the appropriate time
to put on your." Twenty-two reminders, a thesaurus, and one that said nothing.

The rule since 2026-11-07: a reminder is HIS words, said plainly - "Time for
your retainers, sir." - and no model or wit ever rephrases it. The same
sentence every night is the point: it is recognisable at a glance on a lock
screen, which is the only place a reminder is read.

Rules this file will not break:

  * It never raises and never waits on anything: no model, no network, no disk.
  * It never invents an errand or drops one: every word of what he asked for
    is in the sentence, with the pronouns turned round ("my" -> "your",
    because JARVIS is the one saying it).
  * It is never empty and never cut short.
"""
from __future__ import annotations

import re

# Verbs that only say "do the usual thing with": "take my supplements" and
# "wear my retainers" read as "Time for your supplements / retainers".
_ROUTINE = re.compile(r"^(?:take|wear|put (?:in|on)|use|have|do)\s+(your\s+.+)$", re.I)


def _as_errand(text: str) -> str:
    """His reminder text as a phrase JARVIS can say: trimmed, the "remind me
    to" scaffolding off, and first person turned to second.

    He sets a reminder as "wear my retainers" - first person, because he is
    talking about himself - and a sentence that keeps "my" sounds like JARVIS
    owns a set of retainers (reported 2026-09-02)."""
    t = re.sub(r"\s+", " ", str(text or "")).strip().rstrip(".!?")
    t = re.sub(r"^(?:to|please|remember to|remind me to|don'?t forget to)\s+", "", t, flags=re.I)
    if not t:
        return ""
    for pat, rep in (
        (r"\bmy\b", "your"), (r"\bmine\b", "yours"),
        (r"\bmyself\b", "yourself"), (r"\bme\b", "you"),
        (r"\bi'?m\b", "you're"), (r"\bi'?ve\b", "you've"),
        (r"\bi\b", "you"), (r"\bour\b", "your"), (r"\bours\b", "yours"),
        (r"\bwe\b", "you"),
    ):
        t = re.sub(pat, rep, t, flags=re.I)
    return t


def plain(text: str) -> str:
    """`text` is the reminder as he set it. Returns the sentence to say or send.

      "take my supplements" -> "Time for your supplements, sir."
      "wear my retainers"   -> "Time for your retainers, sir."
      "call the dentist"    -> "Time to call the dentist, sir."
      "supplements"         -> "Supplements, sir."
    """
    errand = _as_errand(text)
    if not errand:
        return "You asked me to remind you of something, sir."
    if len(errand.split()) == 1:
        return f"{errand[0].upper()}{errand[1:]}, sir."
    m = _ROUTINE.match(errand)
    if m:
        return f"Time for {m.group(1)}, sir."
    return f"Time to {errand}, sir."


def nudge(text: str) -> str:
    """The one quiet follow-up when no Done came: still his words."""
    errand = _as_errand(text)
    if not errand:
        return "Still to do, sir: the reminder from earlier."
    return f"Still to do, sir: {errand}."
