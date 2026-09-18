"""Lasting facts he says in passing, so JARVIS can OFFER to remember them.

"My chemistry final is on the 25th, what should I study?" carries a fact that
outlives the question; today it is gone when the turn ends unless he says
"remember that". His ask (2026-09-18): offer, with his yes.

Deliberately a WHITELIST. "My computer is slow" and "my head hurts" are
also 'my X is Y' and are not facts about him worth keeping; only the subjects
below, and only as statements. A miss here costs nothing - he can still say
"remember that"; a false offer interrupts him.
"""
from __future__ import annotations

import re

_SUBJECTS = (
    r"name|birthday|anniversary|address|phone number|email|major|minor|school|college|university|"
    r"professor|teacher|advisor|boss|manager|job|title|company|office|doctor|dentist|therapist|vet|"
    r"car|truck|bike|motorcycle|dog|cat|pet|wife|husband|girlfriend|boyfriend|partner|fianc[eé]e?|"
    r"sister|brother|mom|mum|dad|mother|father|parents|son|daughter|kids?|roommate|best friend|"
    r"favou?rite [a-z]+(?: [a-z]+)?|allergy|allergies|blood type|shoe size|height|gym|barber|"
    r"printer|3d printer|monitor|gpu|graphics card|laptop|phone|"
    r"(?:chemistry|physics|biology|calculus|math|english|history|spanish|french|statistics|economics|"
    r"psychology|computer science|[a-z]+) (?:final|exam|midterm|class|lecture|lab)|"
    r"final|midterm|exam|graduation|flight|appointment|interview|shift|practice|game"
)
_FACT = re.compile(
    r"^(?:(?:by the way|also|oh|fyi|so|and|well|jarvis)[,\s]+)*"
    r"(?P<fact>my (?:" + _SUBJECTS + r")(?:'s| is| are| was| will be| starts?| ends?) [^?!]{2,70}?)"
    r"(?:[.!,;]|\s+(?:what|which|so|can|could|should|and|but|do|does|how|when|any)\b|$)",
    re.I)
_FIRST = re.compile(
    r"^(?:(?:by the way|also|oh|fyi|so|and|well|jarvis)[,\s]+)*"
    r"(?P<fact>i(?:'m| am) (?:allergic to|from|a (?:freshman|sophomore|junior|senior)|"
    r"(?:studying|majoring in|taking|learning) [a-z]|left[- ]handed|vegetarian|vegan|lactose intolerant|"
    r"(?:\d{2}|twenty|thirty|forty)\b)[^?!]{0,60}?|"
    r"i (?:live|work|drive|study|go to school|go to college|play|train|graduate|was born) [^?!]{2,60}?)"
    r"(?:[.!,;]|\s+(?:what|which|so|can|could|should|and|but|do|does|how|when|any)\b|$)",
    re.I)
# transient, not lasting: never offered
_TRANSIENT = re.compile(
    r"\b(?:right now|at the moment|currently|today|tonight|this (?:morning|afternoon|evening)|"
    r"broken|not working|isn't working|won't|slow|stuck|hurts|sore|tired|hungry|frozen|down|"
    r"(?:is|are) (?:on|off)$|too (?:loud|quiet|low|high|hot|cold)|dying|dead|full|empty|missing|lost|"
    r"black|blank|open|closed|running|playing|paused)\b", re.I)
_QUESTION = re.compile(r"^(?:what|which|who|where|when|why|how|can|could|would|should|do|does|did|is|are|will|remind|remember|note|tell|show|set)\b", re.I)


def extract(text: str) -> str | None:
    """The lasting fact in `text`, or None. 'My car is a 2019 Civic, what oil
    does it take' -> 'my car is a 2019 civic'."""
    t = " ".join((text or "").split()).strip()
    if not t or "?" in t[:-1] or _QUESTION.match(t):
        return None
    for rx in (_FACT, _FIRST):
        m = rx.match(t)
        if not m:
            continue
        fact = m.group("fact").strip(" ,.;")
        if len(fact.split()) < 3 or _TRANSIENT.search(fact):
            return None
        return fact[0].upper() + fact[1:]
    return None
