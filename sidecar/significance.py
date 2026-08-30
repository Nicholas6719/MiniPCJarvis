"""What is worth interrupting a man for.

The whole proactive design rests on this file. An assistant that reports
everything is one you stop reading inside a day, and then it is worse than
useless, because the one message that mattered is buried in the noise.

The rule Nicholas gave, in his words: *"I don't need to hear about a road closure
in Ohio, but if there is major news like a gas leak in an Ohio facility, I wanna
know about it."* So distance is not the test — SEVERITY is. What proximity
changes is the bar: a road closure in Natick is worth a line in the morning
brief precisely because it is his road; the same closure in Ohio is worth
nothing to him at all.

Four tiers, and the same four everywhere:

  URGENT   life-safety, or an extraordinary market event. Reaches him wherever
           he is, at any hour, and keeps asking until he answers.
  ALERT    breaking and serious, or a large move in something he owns. Reaches
           him immediately, quiet hours included.
  NOTABLE  worth knowing, not worth stopping for. Waits for the next brief.
  (none)   below the bar. Never mentioned.

Rules rather than a model, deliberately: this must be fast, predictable and
testable against real headlines. A model may sharpen the wording of a brief; it
does not get to decide whether to wake him at 3 a.m.
"""
from __future__ import annotations

import re

URGENT = "urgent"
ALERT = "alert"
NOTABLE = "notable"
NONE = ""

# --- where he lives -----------------------------------------------------------
# Middlesex County, Massachusetts. The five towns he named come first; the state
# is still local, and Boston is close enough to matter to him.
HOME_TOWNS = ("framingham", "sudbury", "marlborough", "marlboro", "maynard", "natick")
HOME_REGION = ("massachusetts", "middlesex", " mass.", "boston", "metrowest",
               "worcester", "cambridge", "somerville", "newton", "waltham")

# --- life and limb ------------------------------------------------------------
# The things he named: active shooters, earthquakes, tragedies. Anything here is
# a candidate for waking him; how close it is decides between URGENT and ALERT.
# ONGOING danger to a lot of people: it is still happening and it does not care
# who you are. This is the gas leak he asked about by name — distance does not
# excuse it, because the thing is still unfolding.
HAZARD = re.compile(
    r"\b(?:earthquake|tsunami|tornado|hurricane|wildfire|flash flood|"
    r"explosion|blast|gas leak|chemical (?:leak|spill)|hazmat|toxic|"
    r"evacuat\w+|shelter in place|lockdown|"
    r"terror\w*|bomb(?:ing)?|"
    r"plane crash|derail\w+|collapse|"
    r"nuclear|radiation|outbreak|pandemic|amber alert)\b", re.I)

# VIOLENCE against people. Near home this is the "active shooter" case he named
# and it wakes him. A single stabbing in another country is a tragedy and not
# his emergency — that headline was waking him before this split existed.
VIOLENCE = re.compile(
    r"\b(?:active shooter|mass shooting|shooting|shot|gunman|hostage|manhunt|"
    r"stabbing|stabbed|assault)\b", re.I)

# Someone died. Not every death is his business — a fatal crash in Oregon is
# not — but a death near home is never a footnote, and "fatal" on its own was
# reading as ordinary news because no word in it names a hazard.
FATALITY = re.compile(
    r"\b(?:killed|kills|dead|deaths?|die[sd]|dying|fatal\w*|casualt\w+|"
    r"body found|bodies|homicide|murder|"
    r"house fire|structure fire|building fire)\b", re.I)

# Many. What makes a distant tragedy national news.
MANY = re.compile(r"\b(?:thousands|hundreds|dozens|mass|multiple|several)\b", re.I)

# ...and what makes a distant event enormous regardless of the count. Kept apart
# from MAJOR_SCALE below ON PURPOSE: that list contains the fatality words, so
# using it to promote a distant incident made every death anywhere urgent —
# "British woman killed in stabbing at German railway station" was waking him.
CATASTROPHE = re.compile(
    r"\b(?:catastroph\w+|devastat\w+|unprecedented|nationwide|statewide|"
    r"state of emergency|martial law|mass casualt\w+|magnitude)\b", re.I)

# Scale words: what turns a distant incident into national news.
MAJOR_SCALE = re.compile(
    r"\b(?:killed|dead|deaths?|fatal\w*|casualt\w+|injur\w+|victims?|"
    r"thousands|hundreds|millions|nationwide|statewide|"
    r"declares?|emergency|catastroph\w+|devastat\w+|historic|unprecedented|"
    r"damage\w*|destroy\w+|missing|trapped)\b", re.I)

# --- national weight ----------------------------------------------------------
NATIONAL_WEIGHT = re.compile(
    r"\b(?:president|white house|congress|supreme court|federal reserve|the fed|"
    r"pentagon|war|invasion|missile|strike[sd]?|sanctions|treaty|state of emergency|martial law|"
    r"recession|market crash|shutdown|impeach\w*|election|indict\w+)\b", re.I)

# --- the everyday, which is only interesting when it is HIS everyday ----------
ROUTINE = re.compile(
    r"\b(?:road closure|lane closure|traffic|detour|construction|road work|"
    r"weather advisory|forecast|clouds|sunny|rain expected|"
    r"high school|little league|festival|parade|fundrais\w+|"
    r"ribbon cutting|groundbreaking|town meeting|select ?board|"
    r"school committee|library|farmers market)\b", re.I)


def _text_of(story: dict) -> str:
    return " ".join(str(story.get(k) or "") for k in
                    ("headline", "title", "summary", "description"))


def is_local(story: dict) -> tuple[bool, bool]:
    """(near him at all, one of his five towns)."""
    t = _text_of(story).lower()
    town = any(name in t for name in HOME_TOWNS)
    return (town or any(name in t for name in HOME_REGION)), town


def classify_news(story: dict) -> tuple[str, str]:
    """(tier, why). `story` is any dict with a headline and ideally a summary."""
    text = _text_of(story).strip()
    if not text:
        return NONE, "nothing to read"

    near, own_town = is_local(story)
    hazard = bool(HAZARD.search(text))
    violence = bool(VIOLENCE.search(text))
    danger = hazard or violence
    scale = bool(MAJOR_SCALE.search(text))
    weighty = bool(NATIONAL_WEIGHT.search(text))
    routine = bool(ROUTINE.search(text))

    # Life-safety on his doorstep is the case the whole tier exists for.
    if danger and own_town:
        return URGENT, "something dangerous in one of his towns"
    if danger and near:
        return URGENT, "something dangerous close to home"
    # Distant, but big enough that distance stops mattering. MANY or CATASTROPHE
    # rather than `scale`, which contains the fatality words and so promoted
    # every distant death to something worth waking him for.
    if danger and (MANY.search(text) or CATASTROPHE.search(text)):
        return URGENT, "a serious incident, wherever it is"
    # A hazard is still unfolding and does not care who you are, so distance
    # does not excuse it: this is his gas leak at an Ohio facility.
    if hazard:
        return ALERT, "something still unfolding, wherever it is"
    # Violence far away, one person, already over. A tragedy — and not his
    # emergency. Waking him for it is how he learns to ignore the alerts.
    if violence:
        return NOTABLE, "violence, but far away and already over"

    # A death, with no hazard word to announce it. "Fatal MBTA rail incident"
    # names no danger at all and was reading as ordinary local news.
    if FATALITY.search(text):
        if own_town:
            return URGENT, "somebody died in one of his towns"
        if near:
            return ALERT, "somebody died close to home"
        # MANY only, deliberately not `scale` — the scale words INCLUDE the
        # fatality words, so testing them here made every distant death read as
        # a mass casualty event and woke him for a crash in Nebraska.
        if MANY.search(text):
            return URGENT, "many people have died"
        return NOTABLE, "a death, but not near him"

    if weighty and scale:
        return ALERT, "national news of consequence"
    if weighty:
        return NOTABLE, "national, but not breaking"

    # His own towns get a lower bar — but not no bar. A ribbon cutting is still
    # a ribbon cutting.
    if own_town and not routine:
        return ALERT, "his own town"
    if own_town:
        return NOTABLE, "local colour"
    if near and not routine:
        return NOTABLE, "nearby"
    if near:
        return NOTABLE, "local routine"

    # Somewhere else, nothing serious, nothing weighty: he does not need it.
    return NONE, "not his, and not serious"


def classify_market(*, symbol: str, percent: float, held: bool = False,
                    is_index: bool = False) -> tuple[str, str]:
    """A price move, judged by size and by whether it is his money."""
    move = abs(float(percent or 0))
    if held:
        if move >= 10:
            return URGENT, f"{symbol} has moved {move:.1f}% and he owns it"
        if move >= 5:
            return ALERT, f"{symbol} has moved {move:.1f}% and he owns it"
        if move >= 2:
            return NOTABLE, f"{symbol} is moving"
        return NONE, "an ordinary day for it"
    if is_index:
        if move >= 3:
            return ALERT, f"the market as a whole has moved {move:.1f}%"
        if move >= 1.5:
            return NOTABLE, "the market is moving"
        return NONE, "an ordinary day"
    # Something he does not own: it has to be dramatic to be worth a message,
    # and even then it is a line in the brief rather than an interruption.
    if move >= 15:
        return ALERT, f"{symbol} has moved {move:.1f}%"
    if move >= 7:
        return NOTABLE, f"{symbol} has moved {move:.1f}%"
    return NONE, "not his, and not dramatic"


def worth_saying(tier: str) -> bool:
    return tier in (URGENT, ALERT, NOTABLE)
