"""What is worth interrupting a man for.

The whole proactive design rests on this file. An assistant that reports
everything is one you stop reading inside a day, and then it is worse than
useless, because the one message that mattered is buried in the noise.

**Distance IS the test now** — see `local_only()`. Nicholas ran the other way for
a day and came back with: *"I've been getting WAY too many news reports... I want
the same type of emergency notifications/alerts but let's keep all news local."*
A California wildfire and a Grand Canyon flood had both reached his phone.

The tiers below are unchanged and still decide how loud a story is; `local_only()`
decides, before any of them, whether it is his at all. Read them with that gate in
mind: everything past this point assumes the story already passed it.

The earlier rule, kept because one config line restores it and because it explains
why the tiers are shaped the way they are: *"I don't need to hear about a road
closure in Ohio, but if there is major news like a gas leak in an Ohio facility, I
wanna know about it."* Under that rule severity beat distance. Under this one,
proximity comes first and severity sorts what remains.

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
HOME_REGION = ("massachusetts", "middlesex", "mass.", "boston", "metrowest",
               "worcester", "cambridge", "somerville", "newton", "waltham")

# "mass." was written " mass." with a leading space, to keep "mass shooting" and
# "mass casualty" from reading as Massachusetts. It also meant a headline that
# STARTS with "Mass." - which is exactly how the local press writes it - was not
# local at all: "Mass. awards $17.9 million to improve health in 31 communities"
# was foreign news to him. A word boundary does the same job without the hole.
REGION_RE = re.compile(
    "|".join(r"\b" + re.escape(term) for term in HOME_REGION), re.I)
TOWN_RE = re.compile(
    "|".join(r"\b" + re.escape(town) + r"\b" for town in HOME_TOWNS), re.I)

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
# NOT `dead\w*` — that matches "deadline", and a tax deadline is not a death.
# But "killing" and "deadly" must be here: three real headlines about one
# Framingham killing used those words and none of them registered as a death.
FATALITY = re.compile(
    r"\b(?:kill\w*|dead|deadly|deaths?|die[sd]|dying|fatal\w*|casualt\w+|"
    r"body found|bodies|homicide|murder\w*|manslaughter|"
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

# Not an emergency, but it changes what he can do today: the power is out, the
# road is shut, the water is not drinkable. This is the ONLY non-emergency reason
# to interrupt him, and it exists because of what local-only did to the tiers.
#
# Going local-only nearly backfired: with his towns on a deliberately low bar and
# nothing else coming in, "Sudbury police to host e-bike safety presentation" and
# "Natick Mall to add three new stores" were both ALERTs. He would have traded
# national noise for town-notice noise. His words were "the same type of EMERGENCY
# notifications", so his town gets a lower bar for what reaches the brief, not a
# lower bar for what interrupts him.
DISRUPTION = re.compile(
    r"\b(?:power outage|outage|blackout|water main|boil water|no water|"
    r"road closed|closure|closed|shut down|shutdown|detour|"
    r"schools? closed|cancell?ed|suspended|delays?|"
    r"evacuat\w+|shelter|curfew|state of emergency|"
    r"service (?:disruption|change)|no service)\b", re.I)

# --- the ONLY reason a distant story reaches him -------------------------------
# His words, 2026-08-30: *"I only want the absolute emergencies from other places,
# something everyone in the country needs to know or hear about."*
#
# That bar is much higher than "dangerous". A wildfire across two California
# counties and a flash flood at the Grand Canyon with twenty missing are both real
# emergencies, and both pinged him, and neither is something the country needs to
# know. So this is not "is it serious" - it is "would this be the thing everyone is
# talking about tonight".
#
# It also answers his gas-leak question honestly: a gas leak in Arizona that
# evacuates 300 homes does NOT clear this bar, even though he named it, because it
# is not something everyone in the country needs to hear about.

# Things that ARE the emergency, needing no second word to prove it. Keeping
# these apart matters: when they were lumped in with the reach words below and
# made to prove themselves with a hazard term too, "Nationwide grid failure leaves
# millions without power" came out silent - no hazard word in it, and no death yet.
SYSTEMIC = re.compile(
    r"\b(?:nuclear (?:plant|reactor|meltdown|accident)|radiation leak|"
    r"pandemic|grid (?:collapse|failure)|nationwide (?:blackout|outage)|"
    r"national emergency|martial law)\b", re.I)

# Reach, rather than severity. These say a thing is everywhere; something else
# still has to say it is bad.
NATIONWIDE = re.compile(
    r"\b(?:nationwide|across the (?:country|nation)|the entire country|"
    r"national guard deployed|every state)\b", re.I)

# An attack on the country, rather than a dangerous thing that happened in it.
ATTACK = re.compile(
    r"\b(?:terror(?:ist)? attack|act of terror|assassinat\w+|"
    r"declares? war|at war with|invasion of|invaded|"
    r"missile (?:strike|attack)s?|nuclear (?:strike|attack))\b", re.I)

# A toll so large it is the news everywhere. "Dozens" is not this: dozens die in
# this country most weeks, and he said ABSOLUTE emergencies.
CATASTROPHIC_TOLL = re.compile(
    r"\b(?:hundreds|thousands|scores)\s+(?:of\s+)?(?:people\s+)?"
    r"(?:are\s+)?(?:dead|killed|died|feared dead)\b"
    r"|\bmass casualt\w+"
    r"|\bdeath toll\s+(?:rises|climbs|passes|tops|reaches)\s+"
    r"(?:past\s+|above\s+|over\s+)?(?:hundreds|thousands|[1-9]\d{2,})", re.I)


def national_emergency(text: str) -> bool:
    """Would everyone in the country need to hear about this?

    Deliberately narrow, and measured against a live wire rather than imagined:
    a first attempt at this used a "man-made hazard" keyword list and matched a
    Trump/NBC story, a Visa/Mastercard announcement and a West Bank report. The
    word "attack" alone is worthless. Every rule here needs national reach, an
    attack on the country, or a toll in the hundreds.
    """
    return bool(SYSTEMIC.search(text) or ATTACK.search(text)
                or CATASTROPHIC_TOLL.search(text)
                or (NATIONWIDE.search(text) and (HAZARD.search(text)
                                                 or FATALITY.search(text))))


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
    t = _text_of(story)
    town = bool(TOWN_RE.search(t))
    return (town or bool(REGION_RE.search(t))), town


def local_only() -> bool:
    """Whether news has to be near him to count at all.

    He turned this on himself, on 2026-08-30, after a day of living with the
    alternative: *"I've been getting WAY too many news reports... I want the same
    type of emergency notifications/alerts but let's keep all news local."*

    That reverses what he asked for earlier - a gas leak at an Ohio facility, and
    "I need to be the first to know what's going on" - and the reversal is the
    correct call, because he is the one who had to read the results. A Ross Fire
    in California and a Grand Canyon flash flood were both real alerts on his
    phone, and neither was any of his business.

    The EMERGENCY machinery is untouched: tiers, escalation, quiet-hours
    exemption. What changed is the catchment. Set `briefing.news_scope` to
    "national" to have the old behaviour back in one line.
    """
    from config import config
    return str(config.get("briefing", "news_scope", default="local")).lower() != "national"


def classify_news(story: dict) -> tuple[str, str]:
    """(tier, why). `story` is any dict with a headline and ideally a summary."""
    text = _text_of(story).strip()
    if not text:
        return NONE, "nothing to read"

    near, own_town = is_local(story)

    # Everything below decides how loud a story is. This decides whether it is
    # his at all, and it comes first so nothing downstream can promote its way
    # past it - a wildfire two thousand miles away is still a wildfire, and the
    # hazard rules would happily wake him for it.
    if not near and local_only():
        # One door stays open, and only one: the thing everyone in the country
        # is going to hear about. Everything else that happens elsewhere - and
        # that is nearly all of it - stops here.
        if national_emergency(text):
            return URGENT, "the whole country needs to know this"
        return NONE, "not local, and not something the country needs to know"
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

    # His own towns get a lower bar for being MENTIONED — not for interrupting
    # him. The only non-emergency worth a ping is something that changes his day:
    # the road is shut, the power is out, the water is not drinkable. A new dean
    # and a mall opening three stores wait for the brief like everything else.
    if own_town and DISRUPTION.search(text) and not routine:
        return ALERT, "his own town, and it changes his day"
    if own_town and not routine:
        return NOTABLE, "his own town, but not an emergency"
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
