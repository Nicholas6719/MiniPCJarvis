"""Small things that make him feel like JARVIS rather than a speech prompt.

The films' JARVIS answers his name with more than one word, and greets Stark
by the time of day when he has been away. Pure functions — the orchestrator
supplies the clock and the gap; the gates supply the rest.
"""
from __future__ import annotations

# What he says when he hears his name and nothing else. Short, dry, and never
# the same word twice in a row. "Yes?" stays first: it is the one he is used to.
WAKE_ACKS = ("Yes?", "Sir?", "Yes, sir?", "Go ahead.", "At your service.")

# Been away long enough that a greeting is the natural first word.
AWAY_S = 6 * 3600


def greeting(hour: int) -> str:
    if 5 <= hour < 12:
        return "Good morning, sir."
    if 12 <= hour < 18:
        return "Good afternoon, sir."
    return "Good evening, sir."


def _subject(e: dict) -> str:
    s = str(e.get("subject") or "").strip()
    if s.startswith("task:"):
        s = ""
    if not s:
        t = str(e.get("text") or "").strip().rstrip(".!?")
        s = t if len(t) <= 48 else t[:45].rsplit(" ", 1)[0] + "…"
    return s


def briefing(entries: list[dict]) -> str:
    """One spoken sentence about what happened while he was away, from the
    delivery ledger; "" when nothing did. Counts first, then the subjects,
    never the whole text of anything — the films' JARVIS reports, he does
    not read the mail aloud."""
    if not entries:
        return ""
    sent = [e for e in entries if e.get("outcome") in ("telegram", "spoken")]
    held = [e for e in entries if e.get("outcome") not in ("telegram", "spoken", "nothing")]
    parts = []
    if sent:
        subs = [_subject(e) for e in sent if _subject(e)]
        head = ("One thing reached you" if len(sent) == 1 else f"{len(sent)} things reached you")
        if subs:
            head += " — " + (subs[0] if len(subs) == 1 else ", ".join(subs[:2])
                             + (f" and {len(subs) - 2} more" if len(subs) > 2 else ""))
        parts.append(head)
    if held:
        subs = [_subject(e) for e in held if _subject(e)]
        h = ("one thing I held back" if len(held) == 1 else f"{len(held)} things I held back")
        if subs:
            h += f" ({subs[0]}{'…' if len(subs) > 1 else ''})"
        parts.append(h)
    if not parts:
        return ""
    line = "While you were away: " + "; ".join(parts) + "."
    return line[0].upper() + line[1:]


def wake_line(gap_s: float, hour: int, last: str | None, entries: list[dict]) -> str:
    """The wake acknowledgement, with the briefing attached after time away."""
    ack = wake_ack(gap_s, hour, last)
    if gap_s >= AWAY_S:
        b = briefing(entries)
        if b:
            return f"{ack} {b}"
    return ack


def wake_ack(gap_s: float, hour: int, last: str | None = None) -> str:
    """The line for a bare wake word.

    `gap_s` is the time since he last spoke to JARVIS (a very large number on
    the first wake of a session), `hour` the local hour, `last` the previous
    acknowledgement so this one differs from it.
    """
    if gap_s >= AWAY_S:
        return greeting(hour)
    for i, line in enumerate(WAKE_ACKS):
        if line != last:
            # Rotate from the one after the last, so the sequence moves
            # rather than snapping back to "Yes?" every time.
            if last in WAKE_ACKS:
                j = (WAKE_ACKS.index(last) + 1) % len(WAKE_ACKS)
                return WAKE_ACKS[j]
            return line
    return WAKE_ACKS[0]
