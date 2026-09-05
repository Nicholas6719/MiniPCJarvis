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
