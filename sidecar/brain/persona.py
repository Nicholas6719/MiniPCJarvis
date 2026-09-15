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
    """What an entry was ABOUT, in words he can hear. The ledger key is
    for deduplication ("news:cause of fatal needham christmas eve fire...",
    "brief:2026-09-14 16:15", "task:278") and was being read aloud as-is
    (2026-09-15)."""
    s = str(e.get("subject") or "").strip()
    kind, _, rest = s.partition(":")
    if kind == "task" or not s:
        s = ""
    elif kind == "brief":
        hhmm = rest.strip()[-5:]
        try:
            h, m = int(hhmm[:2]), int(hhmm[3:])
            s = f"the {h % 12 or 12}:{m:02d} {'PM' if h >= 12 else 'AM'} brief"
        except ValueError:
            s = "a brief"
    elif rest and kind in ("news", "market", "weather", "watchlist", "nws", "alert"):
        s = rest.strip()
        s = s[0].upper() + s[1:] if s else s
    if not s:
        t = str(e.get("text") or "").strip().rstrip(".!?")
        s = t if len(t) <= 48 else t[:45].rsplit(" ", 1)[0] + "…"
    elif len(s) > 60:
        s = s[:57].rsplit(" ", 1)[0] + "…"
    return s


def _missed(entries: list[dict]) -> list[dict]:
    """What he has NOT seen. Telegram reached his phone and he reads his
    phone; a brief is read there too. Spoken to a room he was not in, or
    held back, is the whole point of telling him."""
    out = []
    for e in entries:
        if str(e.get("subject") or "").startswith("brief:"):
            continue
        if e.get("outcome") == "telegram":
            continue
        if e.get("outcome") == "nothing" and "test" in str(e.get("why") or ""):
            continue                                    # muted for a test
        out.append(e)
    return out


def briefing(entries: list[dict]) -> str:
    """One spoken sentence about what happened while he was away, from the
    delivery ledger; "" when nothing did. Counts first, then the subjects,
    never the whole text of anything — the films' JARVIS reports, he does
    not read the mail aloud."""
    missed = _missed(entries or [])
    if not missed:
        return ""                 # everything reached his phone: nothing to add
    spoken = [e for e in missed if e.get("outcome") == "spoken"]
    held = [e for e in missed if e.get("outcome") != "spoken"]
    parts = []
    if spoken:
        subs = [_subject(e) for e in spoken if _subject(e)]
        p = ("one thing I said while you were out" if len(spoken) == 1
             else f"{len(spoken)} things I said while you were out")
        if subs:
            p += f" — {subs[0]}" + (f", and {len(subs) - 1} more" if len(subs) > 1 else "")
        parts.append(p)
    if held:
        subs = [_subject(e) for e in held if _subject(e)]
        p = ("one thing I held back" if len(held) == 1 else f"{len(held)} things I held back")
        if subs:
            p += f" — {subs[0]}" + (f", and {len(subs) - 1} more" if len(subs) > 1 else "")
        parts.append(p)
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

def briefing_sections(entries: list[dict]) -> list[dict]:
    """The same ledger as sections for the HUD's brief stage: what reached
    him, what was held back. The voice says one sentence; the screen lists."""
    if not entries:
        return []

    def line(e: dict) -> str:
        subj = _subject(e)
        text = str(e.get("text") or "").strip().rstrip(".")
        if subj and text and subj.lower() not in text.lower():
            return f"{subj}: {text}"
        return text or subj

    # What reached his phone is not listed: he reads his phone. What the
    # screen is for is the rest (2026-09-15: "the news was news that I got
    # yesterday from Telegram... the briefs aren't needed because I read the
    # briefs"). Nothing missed means no stage at all.
    missed = _missed(entries)
    spoken = [line(e) for e in missed if e.get("outcome") == "spoken"]
    held = [line(e) for e in missed if e.get("outcome") != "spoken"]
    out = []
    if spoken:
        out.append({"title": "Said while you were out", "lines": [s for s in spoken if s][-6:]})
    if held:
        out.append({"title": "Held back", "lines": [h for h in held if h][-6:]})
    return out
