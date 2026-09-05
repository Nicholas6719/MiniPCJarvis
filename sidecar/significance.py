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

# Says Massachusetts and cannot mean anywhere else.
ANCHOR_RE = re.compile(r"\bmassachusetts\b|\bmass\.|\bmetrowest\b|\bmiddlesex\b"
                       r"|\bbay state\b|\b(?:ma|mass)\b(?=[,.]|\s+state)", re.I)

# Place names that are ALSO somewhere else, or something else entirely. The
# always-scanned national wire is BBC and Sky, so "Cambridge United striker
# killed in car crash" read as a death close to home, "Worcester man charged over
# stabbing" as violence in his state, and "Marlboro maker Altria to cut 500 jobs"
# as news from one of his five towns. A name like this needs corroboration.
AMBIGUOUS_TOWN_RE = re.compile(r"\bmarlboro\b(?!ugh)", re.I)

# A local desk is not only local. WCVB, MassLive and Boston.com all carry the
# national wire, so provenance ALONE made "Woman randomly stabs 2 people in New
# York City's Times Square, killing 1" read as violence in his state on
# 2026-08-31. When a story names a place that plainly is not Massachusetts, and
# names nothing of his anywhere in it, the dateline beats the feed it rode in on.
FAR_PLACE = re.compile(
    r"\b(?:new york|manhattan|brooklyn|the bronx|times square|"
    r"los angeles|san francisco|san diego|seattle|portland|denver|phoenix|"
    r"chicago|houston|dallas|austin|atlanta|miami|orlando|tampa|new orleans|"
    r"las vegas|nevada|detroit|cleveland|philadelphia|baltimore|st\. louis|"
    r"new jersey|connecticut|rhode island|new hampshire|vermont|maine|"
    r"california|florida|texas|arizona|georgia|ohio|michigan|illinois|"
    r"oregon|colorado|utah|alaska|hawaii|oklahoma|kentucky|tennessee)\b", re.I)

# Rejecting every bare "Boston" was too blunt - it cost him a hazmat call at Mass
# General, a fatal MBTA incident and a Boston police story, all genuinely his.
# The UK stories give themselves away by their SUBJECT, not their city: football
# clubs and British idiom. So an ambiguous city stays local unless the text
# reads as somewhere else.
ELSEWHERE_RE = re.compile(
    r"\b(?:united|fc|premier league|championship|relegation|striker|midfielder|"
    r"gaffer|pub|nhs|mp|borough|parliament|whitehall|pence|"
    r"britain|british|england|english|scotland|scottish|wales|welsh|ireland|"
    r"irish|london|manchester|liverpool|leeds|ontario|quebec|sydney|melbourne)\b"
    r"|£", re.I)

# --- life and limb ------------------------------------------------------------
# The things he named: active shooters, earthquakes, tragedies. Anything here is
# a candidate for waking him; how close it is decides between URGENT and ALERT.
# ONGOING danger to a lot of people: it is still happening and it does not care
# who you are. This is the gas leak he asked about by name — distance does not
# excuse it, because the thing is still unfolding.
# Every entry here must name an EVENT, not a subject. Three of them did not, and
# on 2026-09-01 the bare word "nuclear" turned "Trump lashes out at reporter who
# grilled him on nuclear strikes on alleged drug boats" into "something dangerous
# close to home" - URGENT, off a local desk, chasing him until he acknowledged
# it. A press conference is not a hazard. `toxic` would have done the same for a
# toxic workplace, and `collapse` for a market collapse or the collapse of talks.
HAZARD = re.compile(
    r"\b(?:earthquake|tsunami|tornado|hurricane|wildfire|flash flood|"
    r"explosion|blast|gas leak|chemical (?:leak|spill)|hazmat|"
    r"toxic (?:gas|fumes?|smoke|spill|leak|cloud|chemical|water|air|waste)|"
    r"evacuat\w+|shelter in place|lockdown|"
    r"terror(?:ist)? attack|terror plot|bombing|bomb threat|"
    r"plane crash|derail\w+|"
    r"(?:building|bridge|roof|structure|balcony|crane|deck|wall) collapse|"
    r"collapsed? (?:building|bridge|roof|structure)|"
    r"nuclear (?:plant|reactor|meltdown|accident|leak|spill|emergency|"
    r"contamination|material|waste)|"
    r"radiation (?:leak|exposure|release|alert)|"
    r"outbreak|pandemic|amber alert)\b", re.I)

# VIOLENCE against people. Near home this is the "active shooter" case he named
# and it wakes him. A single stabbing in another country is a tragedy and not
# his emergency — that headline was waking him before this split existed.
VIOLENCE = re.compile(
    r"\b(?:active shooter|mass shooting|shooting|shot|gunman|hostage|manhunt|"
    r"stabbing|stabbed|assault)\b", re.I)

# ...but not every "shot" is a gunshot. On 2026-08-31 Boston.com ran "This
# Somerville bar aimed to help set a world record. It involved taking shots." -
# thirty-five people drinking a shot of Malort - and it reached him as violence
# in his state. Same shape as NOT_A_DEATH below: strip the innocent senses of
# the word before asking, rather than dropping "shot" and losing real shootings.
NOT_VIOLENCE = re.compile(
    r"\bshots?\s+of\s+\w+"
    r"|\b(?:a|the|another|free|flu|booster|vaccine|covid|tequila|whiskey)\s+shots?\b"
    r"|\b(?:tak\w+|took|drink\w*|drank|down\w+|pour\w+|order\w*)\s+(?:a\s+)?shots?\b"
    r"|\bshot\s+(?:clock|glass|list|put)\b|\bscreenshots?\b"
    r"|\b(?:jump|three|3|slap|penalty|corner|free)\s*-?\s*shots?\b"
    r"|\bshots?\s+on\s+goal\b|\b(?:big|long|hot|cheap|parting)\s+shot\b"
    r"|\bshot\s+(?:a|the)\s+(?:film|movie|video|scene|documentary)\b", re.I)


def _violence_text(text: str) -> str:
    """The text with non-violent uses of 'shot' removed, for VIOLENCE only."""
    return NOT_VIOLENCE.sub(" ", text)


# The courts are not an emergency. A verdict, an indictment or a sentencing is
# the system working on something that already happened, often years ago. On
# 2026-08-31 "Jury convicts a man of first-degree murder for the 1996 killing of
# rap icon Tupac Shakur" arrived on the MassLive feed and reached him as
# "somebody died close to home" - a killing thirty years ago and two thousand
# miles away, delivered as an alert.
ADJUDICATED = re.compile(
    r"\b(?:jury|jurors?|verdict|convict\w+|acquit\w+|sentenc\w+|"
    r"pleads? guilty|pleaded guilty|plea deal|found guilty|on trial|retrial|"
    r"indict\w+|arraign\w+|grand jury|lawsuit|settlement|appeals? court|"
    r"parole|extradit\w+|testifie[sd]|takes the stand|courtroom)\b", re.I)

# ...unless the thing is still out there. An active manhunt is an emergency even
# when the same sentence is full of courtroom words, so this outranks the guard
# above rather than sitting inside it.
STILL_ACTIVE = re.compile(
    r"\b(?:manhunt|at large|on the loose|active shooter|lockdown|"
    r"shelter in place|evacuat\w+|ongoing|unfolding|still burning|"
    r"no suspect in custody|police are searching|search continues|"
    r"remains? at large)\b", re.I)

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
    r"road clos\w+|closure|clos(?:e|es|ed|ing)|shut down|shutdown|detour|"
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

# A note for the next person who thinks this door is too narrow. On 2026-08-31 I
# widened it to let a magnitude 7.1 California earthquake with thousands
# evacuated through, on the reasoning that a disaster should not have to finish
# killing people before it counts. test_significance.py failed immediately, on
# "Hurricane makes landfall in Florida, state of emergency declared" - which is
# in the list of alerts HE named when he said "WAY too many news reports", right
# beside "Tornado kills 14 in Oklahoma". The test was right and the reasoning
# was wrong: he set this bar himself, at things every American hears about the
# same hour - an attack, a nuclear accident, a pandemic, the grid down, a toll
# in the hundreds. A disaster in another state is not on his list. Do not widen
# this without him asking; `briefing.news_scope = "national"` already exists for
# the day he wants the whole wire back.


# Somewhere else's country. "Everyone in THE COUNTRY needs to know" means his
# country: a Nepal landslide rescue is a tragedy and front-page news, and it is
# not something every American needs to hear about at 1:42 in the afternoon.
# He was sent one on 2026-08-31 - the BBC summary said hundreds dead, which is
# exactly what CATASTROPHIC_TOLL is for, so the rule fired as designed and the
# design was wrong.
FOREIGN = re.compile(
    r"\b(?:nepal|india|pakistan|bangladesh|china|chinese|japan|korea|vietnam|"
    r"thailand|indonesia|philippines|myanmar|afghanistan|iran|iraq|syria|israel|"
    r"gaza|lebanon|turkey|turkiye|egypt|libya|sudan|nigeria|kenya|ethiopia|"
    r"somalia|congo|ghana|morocco|algeria|russia|russian|ukraine|poland|germany|"
    r"german|france|french|spain|spanish|italy|italian|greece|britain|british|"
    r"england|scotland|wales|ireland|netherlands|dutch|belgium|sweden|norway|"
    r"denmark|finland|brazil|argentina|chile|peru|colombia|venezuela|mexico|"
    r"mexican|haiti|cuba|canada|canadian|australia|australian|new zealand|"
    r"south africa|saudi|yemen|qatar|dubai|emirates|kabul|tehran|moscow|beijing|"
    r"tokyo|seoul|mumbai|delhi|karachi|cairo|lagos|nairobi|paris|berlin|madrid|"
    r"rome|athens|london|dublin|kyiv|kiev|gaza strip|west bank)\b", re.I)

# ...unless it is his country too. "US strikes", "American hostages" - those are
# foreign datelines that are still national news here.
# NOT case-insensitive on the abbreviation. `re.I` made `u\.?s\.?` match the
# ordinary pronoun "us", and RSS summaries are full of it - "a survivor told us
# the ground shook" was enough to make a Nepal quake a national emergency again,
# defeating the FOREIGN rule about an hour after it was written. The country is
# "US" or "U.S."; the pronoun is not.
OURS = re.compile(
    r"\b(?:U\.?S\.?|USA)\b"
    r"|\b(?:america|american|americans|washington|pentagon|"
    r"white house|congress|federal|nationwide|homeland)\b")


def national_emergency(text: str, headline: str = "") -> bool:
    """Would everyone in the country need to hear about this?

    Deliberately narrow, and measured against a live wire rather than imagined:
    a first attempt at this used a "man-made hazard" keyword list and matched a
    Trump/NBC story, a Visa/Mastercard announcement and a West Bank report. The
    word "attack" alone is worthless. Every rule here needs national reach, an
    attack on the country, or a toll in the hundreds.
    """
    # Somebody else's country, with nothing tying it to his: not his emergency,
    # however large. This is the Nepal case - real, enormous, and not something
    # every American needs on their phone in the middle of the afternoon.
    if FOREIGN.search(text) and not OURS.search(text):
        return False
    # A FOREIGN STORY IS JUDGED BY ITS HEADLINE. On 2026-09-05 at 07:19 "US
    # envoys in Moscow in new push for peace between Russia and Ukraine" went
    # to his phone as URGENT and chased him for an acknowledgement. Nothing in
    # the headline is an emergency; the summary's background - "Russia's
    # full-scale invasion of Ukraine", the overnight missile strikes - matched
    # ATTACK. The body of a story about a war is full of the war. If the thing
    # itself is the emergency, the headline says so.
    if headline and FOREIGN.search(text):
        text = headline
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


# The word "death" doing legal or administrative work rather than reporting one.
# A real alert on his phone: "Lindsay Clancy trial: Does Massachusetts have the
# death penalty? What sentence could she face if found guilty?" - a court
# explainer from an Indian outlet, which read as "somebody died close to home"
# because FATALITY found the word "death" in "death penalty". Nobody had died.
NOT_A_DEATH = re.compile(
    r"\bdeath (?:penalty|row|sentence|benefits?|certificate|notice|threats?)\b"
    r"|\bsentenced to death\b|\blife or death\b|\bdeath with dignity\b"
    r"|\bdeath tax\b|\bdeath star\b"
    # Nobody died. "Man seriously injured in box cutter attack" carried
    # "attempted murder" in its summary and reached him as a death near home.
    r"|\battempted (?:murder|homicide|killing)\b", re.I)


# A death is not automatically an emergency. On 2026-09-01 he was sent
# "Dolly Parton died on August 25 after a brief battle with cancer. Levi Herman,
# an outlaw country musician, died on Monday — MassLive." off a LOCAL desk, and
# it reached him as "somebody died close to home". His reply: *"this doesn't seem
# like a news emergency... so why am I hearing about it?"*
#
# The distinction that was missing: an emergency is an INCIDENT — something
# happened, and it may still be happening. An illness, an age, a hospital bed is
# news, and news waits for the brief. Local desks carry national obituaries, so
# provenance will keep handing these to us.
NATURAL_DEATH = re.compile(
    r"\b(?:battle|struggle)\s+with\s+\w+"
    r"|\b(?:died|dies|death)\s+(?:of|from|after)\s+(?:a\s+|an\s+|his\s+|her\s+)?"
    r"(?:brief\s+|long\s+|short\s+)?(?:illness|cancer|complications|disease)"
    r"|\b(?:cancer|leukemia|natural causes|old age|pneumonia|alzheimer\w*|"
    r"parkinson\w*|dementia|heart failure|long illness|hospice)\b"
    r"|\bpassed away\b|\b(?:dies?|died)\s+at\s+(?:the\s+age\s+of\s+)?\d{2}\b"
    r"|\bobituar\w+|\bin memoriam\b", re.I)

# Someone whose death is an obituary rather than an emergency, wherever it
# happened. "Levi Herman, an outlaw country musician, died on Monday" names no
# cause at all, so the illness words above cannot catch it.
PUBLIC_FIGURE = re.compile(
    r"\b(?:musician|singer|songwriter|guitarist|drummer|rapper|band|actor|"
    r"actress|comedian|author|novelist|poet|artist|painter|director|producer|"
    r"broadcaster|journalist|athlete|quarterback|pitcher|boxer|wrestler|coach|"
    r"senator|congressman|congresswoman|governor|mayor|ambassador|"
    r"laureate|icon|legend|star|hall of fame)\b", re.I)

# ...unless something HAPPENED to them. These are the deaths that stay
# emergencies no matter who died: a crash is a crash.
INCIDENT = re.compile(
    r"\b(?:crash|collision|struck by|hit by|drown\w+|overdose|electrocut\w+|"
    r"suffocat\w+|carbon monoxide|blaze|wreck|derail\w+|capsiz\w+|"
    r"fell from|fall from|shot|stabb\w+|assault\w*|attack\w*|accident)\b", re.I)


# More than one person died. A single fatality an hour away is the thing he
# asked to stop hearing about; a toll is not.
MULTI_DEATH = re.compile(
    r"\bdeath toll\b"
    r"|\b(?:[2-9]|\d{2,})\s+(?:people\s+)?(?:are\s+)?(?:dead|killed|died|feared dead)\b"
    r"|\b(?:two|three|four|five|six|seven|eight|nine|ten)\s+(?:people\s+)?"
    r"(?:are\s+)?(?:dead|killed|died)\b", re.I)

# Weather that HAZARD does not name but that is still moving while he reads it.
WEATHER_EVENT = re.compile(
    r"\b(?:flood\w*|blizzard|ice storm|nor'?easter|snowstorm|storm surge|"
    r"heat wave|landslide|mudslide|avalanche)\b", re.I)


def _ongoing(text: str) -> bool:
    """Is this still happening, or is it over?

    The distinction that decides whether a serious thing an hour away is his
    emergency or his news. A gas leak is unfolding and can travel; a suspect at
    large is unfolding and can move. A drowning that ended at the hospital and a
    cyclist killed in a collision are finished - terrible, and nothing he can do
    at nine in the morning.
    """
    if HAZARD.search(text) or STILL_ACTIVE.search(text):
        return True                      # a hazard is unfolding by definition
    if WEATHER_EVENT.search(text):
        return True                      # flooding and storms are still moving
    # More than one person. "Death toll rises to 12 in Massachusetts flooding"
    # is an emergency however finished it sounds; "one dead after a crash" is
    # the case this whole rule exists to quieten.
    return bool(MULTI_DEATH.search(text) or MANY.search(text)
                or CATASTROPHE.search(text))


def _is_obituary(text: str) -> bool:
    """A death that is sad news rather than an unfolding emergency."""
    if INCIDENT.search(text) or HAZARD.search(text) or VIOLENCE.search(
            _violence_text(text)):
        return False
    return bool(NATURAL_DEATH.search(text) or PUBLIC_FIGURE.search(text))


def _event_text(text: str) -> str:
    """The text with non-event uses of death words removed, for FATALITY only."""
    return NOT_A_DEATH.sub(" ", text)


def _text_of(story: dict) -> str:
    return " ".join(str(story.get(k) or "") for k in
                    ("headline", "title", "summary", "description"))


def is_local(story: dict) -> tuple[bool, bool]:
    """(near him at all, one of his five towns).

    Place names are not evidence on their own. Boston, Cambridge, Worcester and
    Newton are all English towns too, and the always-scanned national wire is BBC
    and Sky - so "Cambridge United striker killed in car crash" read as somebody
    dying close to home, and "Marlboro maker Altria to cut 500 jobs" read as news
    from one of his five towns.

    Two ways to be local, and an ambiguous name alone is neither:
      * the story came from one of HIS desks (WCVB, MassLive, the Patch towns) -
        provenance beats parsing, and it is free;
      * or the text names Massachusetts unambiguously, or names one of his towns
        in a form nobody else uses.
    """
    t = _text_of(story)
    from_his_desk = bool(story.get("_local_feed"))
    anchored = bool(ANCHOR_RE.search(t))

    # Provenance is good evidence, not proof. If the story names nothing of his
    # and does name somewhere clearly else, the desk it came from stops counting
    # - otherwise every wire story a local outlet reprints is "close to home".
    if from_his_desk and FAR_PLACE.search(t) and not (
            anchored or TOWN_RE.search(t) or REGION_RE.search(t)):
        from_his_desk = False

    town = bool(TOWN_RE.search(t))
    if town and AMBIGUOUS_TOWN_RE.search(t) and not (anchored or from_his_desk):
        town = False            # "Marlboro maker Altria" is a cigarette company

    if town or from_his_desk or anchored:
        return True, town
    # A bare "Boston"/"Cambridge"/"Worcester" is his until the story says
    # otherwise - England has all three, and the national wire is BBC and Sky.
    if REGION_RE.search(t) and not ELSEWHERE_RE.search(t):
        return True, False
    return False, False


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


def emergencies_only() -> bool:
    """He wants emergencies, not a news service.

    2026-08-31, his third narrowing of this in two days: *"Only have him tell me
    about emergencies from now on. I was getting too many news feeds. I only want
    to hear about the emergencies and the local ones. Only tell me about national
    ones if it's extremely important."*

    So NOTABLE stops existing for news. Anything that merely 'waits for the
    brief' is now nothing at all - no local colour, no roll-up, no town notices.
    What survives is what would interrupt him: a local emergency, something that
    changes his day, or the very narrow national door.
    """
    from config import config
    return str(config.get("briefing", "news_mode",
                          default="emergencies")).lower().startswith("emerg")


def _classify_news_full(story: dict) -> tuple[str, str]:
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
        if national_emergency(text, headline=str(story.get("headline") or "")):
            return URGENT, "the whole country needs to know this"
        return NONE, "not local, and not something the country needs to know"
    hazard = bool(HAZARD.search(text))
    violence = bool(VIOLENCE.search(_violence_text(text)))
    danger = hazard or violence

    # A courtroom story is the system processing something that is already over,
    # so it cannot be an emergency however violent its vocabulary - unless the
    # thing itself is still happening out there, which outranks this.
    if (ADJUDICATED.search(text) and not STILL_ACTIVE.search(text)
            and not hazard):
        return NOTABLE, "a court case, not an emergency"
    scale = bool(MAJOR_SCALE.search(text))
    weighty = bool(NATIONAL_WEIGHT.search(text))
    routine = bool(ROUTINE.search(text))

    # Life-safety on his doorstep is the case the whole tier exists for.
    if danger and own_town:
        return URGENT, "something dangerous in one of his towns"
    # In the state but not his town, the two kinds of danger part company. A
    # HAZARD is a thing loose in the environment - a gas leak, an earthquake, a
    # chemical fire - and it can travel, so it still wakes him. VIOLENCE is
    # person-level and does not: two people were shot in Brockton on 2026-08-31,
    # forty minutes away, and it reached him as URGENT with escalating pings that
    # kept asking until he tapped "Got it". He could do nothing with it at 6 a.m.
    # That is how an alarm teaches you to ignore it.
    if hazard and near:
        return URGENT, "something dangerous close to home"
    # Violence in the state, but not his town, and ALREADY OVER. On 2026-09-01
    # he was sent a drowning in Falmouth and a cyclist killed in Lynn - both
    # real, both genuinely Massachusetts, both an hour away and finished. His
    # reply: *"why am I still getting this kind of news?"* The same lesson as
    # the Brockton shooting: he can do nothing with it, and an alarm he can do
    # nothing about is an alarm he learns to ignore. Still ongoing is different
    # - a suspect at large forty minutes away is his business.
    if danger and near and _ongoing(text):
        return ALERT, "still happening, and in his state"
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
    if FATALITY.search(_event_text(text)):
        # An obituary is not an emergency, however local the desk that carried
        # it. This is the Dolly Parton case: a celebrity death from illness,
        # reprinted by MassLive, reaching him as "somebody died close to home".
        if _is_obituary(text):
            return NOTABLE, "a death, but not an emergency"
        if own_town:
            return URGENT, "somebody died in one of his towns"
        # A death elsewhere in the state that is already over is news, not an
        # emergency - see the Falmouth and Lynn cases above. If it is still
        # unfolding, it still reaches him.
        if near and _ongoing(text):
            return ALERT, "a death nearby, and it is still unfolding"
        # ...or it is over, but it has shut a road or a rail line he uses. That
        # is not an emergency either - it is something that changes his day,
        # which is the other reason he wants to be told.
        if near and DISRUPTION.search(text):
            return ALERT, "a death nearby, and it has closed something"
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


def _news_tier(story: dict) -> tuple[str, str]:
    """The tier, with his emergencies-only rule applied last."""
    tier, why = _classify_news_full(story)
    if tier == NOTABLE and emergencies_only():
        return NONE, "not an emergency, and he asked for emergencies only"
    return tier, why


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


# The public entry point applies his emergencies-only rule.
classify_news = _news_tier
