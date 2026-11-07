"""What one transmission says: which town and agency, what kind of incident,
where, which units, and whether it names a road he drives.

Pure functions over text; nothing here touches the network, the database or
the clock, so the gate can feed it a hundred dispatch lines in a second.

Written for plain-English Massachusetts dispatch as a machine transcript
renders it: "Engine 3, Ladder 1 respond to 45 Union Avenue for a reported
structure fire, smoke showing." The transcripts are imperfect - the audio
reaching the PC peaks around -35 dBFS and some channels are digital - so the
patterns are phrases a dispatcher actually says, never single loose words, and
a negation ("no entrapment", "nothing showing") is read before anything is
matched. Rules decide; the local model only rewrites a line for the ones the
rules already think matter.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache

import geo

# ---------------------------------------------------------------- labels
_FD = re.compile(r"\b(?:fd|fire|fireground|fgnd|fg|eng|engine|ladder|dist|district)\b", re.I)
_PD = re.compile(r"\b(?:pd|police|pol|msp|sp|state police|sheriff|cruiser)\b", re.I)
_EMS = re.compile(r"\b(?:ems|amb|ambulance|medic|medics|als|bls|rescue)\b", re.I)


@dataclass(frozen=True)
class Label:
    raw: str
    town: "geo.Town | None"
    agency: str            # "FD" | "PD" | "EMS" | ""

    @property
    def agency_word(self) -> str:
        return {"FD": "Fire", "PD": "Police", "EMS": "EMS"}.get(self.agency, "")

    @property
    def spoken(self) -> str:
        """'Framingham Fire', or the label as he typed it when no town matched."""
        if self.town:
            return f"{self.town.name} {self.agency_word}".strip()
        return (self.raw or "an unnamed channel").strip()


@lru_cache(maxsize=1024)
def parse_label(name: str) -> Label:
    """The town and agency behind a 16-character channel name he typed:
    "Framnghm FD" -> Framingham / FD, "Action PD" -> Acton / PD."""
    raw = (name or "").strip()
    agency = "FD" if _FD.search(raw) else "PD" if _PD.search(raw) else "EMS" if _EMS.search(raw) else ""
    try:
        town = geo.match_town(raw) if raw else None
    except Exception:
        town = None
    return Label(raw, town, agency)


# ---------------------------------------------------------------- incident types
@dataclass(frozen=True)
class Kind:
    key: str
    label: str             # how it is said: "a working fire"
    family: str            # fire | police | traffic | ems
    severity: int          # 1..10, higher is worse
    danger: bool           # active danger: URGENT near him, ALERT elsewhere in range


KINDS: dict[str, Kind] = {k.key: k for k in (
    Kind("multiple_alarm", "a multiple-alarm fire", "fire", 10, True),
    Kind("persons_trapped", "a fire with people trapped", "fire", 10, True),
    Kind("working_fire", "a working fire", "fire", 9, True),
    Kind("hazmat", "a hazardous-materials incident", "fire", 9, True),
    Kind("smoke_showing", "a structure fire with smoke showing", "fire", 8, True),
    Kind("water_rescue", "a water rescue", "fire", 7, False),
    Kind("structure_fire", "a reported structure fire", "fire", 6, False),
    Kind("gas_leak", "a gas leak", "fire", 4, False),
    Kind("brush_fire", "a brush fire", "fire", 4, False),
    Kind("vehicle_fire", "a vehicle fire", "fire", 4, False),
    Kind("small_fire", "a small fire", "fire", 3, False),
    Kind("smoke_investigation", "a smoke investigation", "fire", 3, False),
    Kind("co_alarm", "a carbon monoxide call", "fire", 3, False),
    Kind("wires_down", "wires down", "fire", 3, False),
    Kind("alarm", "a fire alarm", "fire", 2, False),
    Kind("shots_fired", "shots fired", "police", 10, True),
    Kind("armed_person", "an armed person", "police", 9, True),
    Kind("pursuit", "a police pursuit", "police", 8, True),
    Kind("missing_child", "a missing child", "police", 8, True),
    Kind("bomb_threat", "a suspicious package", "police", 7, False),
    Kind("stabbing", "a stabbing", "police", 7, False),
    Kind("robbery", "a robbery", "police", 6, False),
    Kind("missing_person", "a missing person", "police", 5, False),
    Kind("break_in", "a break-in", "police", 4, False),
    Kind("disturbance", "a disturbance", "police", 2, False),
    Kind("entrapment", "a crash with entrapment", "traffic", 9, True),
    Kind("pedestrian_struck", "a pedestrian struck", "traffic", 7, False),
    Kind("rollover", "a rollover crash", "traffic", 6, False),
    Kind("road_closure", "a road closure", "traffic", 5, False),
    Kind("mva", "a crash", "traffic", 3, False),
    Kind("cardiac_arrest", "a cardiac arrest", "ems", 5, False),
    Kind("overdose", "an overdose", "ems", 4, False),
)}
TRAFFIC = {"entrapment", "pedestrian_struck", "rollover", "mva"}
# A burst on a fire channel is a kind with no words behind it.
BURST = Kind("activity_burst", "unusual activity", "fire", 5, False)

_P = lambda s: re.compile(s, re.I)  # noqa: E731
PATTERNS: dict[str, re.Pattern] = {
    "multiple_alarm": _P(r"\b(?:second|2nd|third|3rd|fourth|4th|fifth|5th|multiple)[- ]alarm\b"
                         r"|\b(?:strike|striking|struck|requesting|request|going to|go to|upgrade to|upgraded to)"
                         r"\s+(?:a\s+|the\s+)?(?:second|2nd|third|3rd)(?:\s+alarm)?\b"
                         r"|\b[2-5][- ]alarm(?:er)?\b"),
    "working_fire": _P(r"\bworking (?:fire|structure fire|house fire|building fire)\b|\bfully involved\b"
                       r"|\bheavy fire\b|\bflames? (?:showing|visible|through|from|coming)\b"
                       r"|\bfire (?:showing|through the roof|in the walls|extending|extension"
                       r"|on (?:the )?(?:first|second|third|1st|2nd|3rd) floor)\b"
                       r"|\bdefensive (?:operations?|attack|mode)\b|\ball hands\b"),
    "hazmat": _P(r"\b(?:haz ?mat|hazardous (?:materials?|spill|leak)|chemical (?:spill|leak|release|odor|smell)"
                 r"|fuel (?:spill|leak)|(?:oil|diesel|gasoline) spill|tanker (?:leak|rollover)"
                 r"|unknown (?:substance|powder)|white powder)\b"),
    "smoke_showing": _P(r"\bsmoke showing\b|\bheavy smoke\b|\bsmoke (?:coming |pushing )?from (?:the )?"
                        r"(?:roof|eaves|windows?|building|house|structure|second floor|attic|basement)\b"
                        r"|\bsmoke (?:in|filling) the (?:building|house|hallway|apartment)\b"),
    "water_rescue": _P(r"\b(?:water rescue|ice rescue|dive team|(?:person|someone|party|victim) in the water"
                       r"|(?:fell|fallen|went) (?:in|into|through) the (?:water|ice|lake|pond|river)"
                       r"|through the ice|drowning|capsized)\b"),
    # "residential fire alarm" / "fire activation" is an ALARM call: on
    # 2026-11-07 "...for the residential fire activation" became a structure
    # fire. The lookahead keeps those for the alarm pattern below.
    "structure_fire": _P(r"\b(?:structure|house|building|apartment|dwelling|residential|commercial|garage|barn) fire\b"
                         r"(?!\s+(?:alarm|activation|detector|system|panel|box|drill|inspection))"
                         r"|\bfire in (?:the |a )?(?:building|house|home|apartment|basement|attic|unit|garage)\b"
                         r"|\breported (?:structure )?fire\b"),
    "gas_leak": _P(r"\b(?:gas (?:leak|odor|smell|main|line)|(?:odor|smell) of (?:natural )?gas|natural gas"
                   r"|struck (?:a )?gas (?:line|main))\b"),
    "brush_fire": _P(r"\b(?:brush|grass|woods|forest|wildland|leaf|field) fire\b"),
    "vehicle_fire": _P(r"\b(?:car|vehicle|auto|truck|bus|motorcycle) fire\b"
                       r"|\b(?:car|vehicle|truck) (?:is )?(?:on fire|fully involved|engulfed)\b"),
    "small_fire": _P(r"\b(?:chimney|kitchen|cooking|stove|oven|dumpster|trash|mulch|electrical|outlet) fire\b"
                     r"|\bfood on the stove\b"),
    "co_alarm": _P(r"\b(?:carbon monoxide|co (?:alarm|detector|reading|levels?|call)|c o (?:alarm|detector))\b"),
    "wires_down": _P(r"\b(?:wires? (?:down|arcing|burning)|(?:power|electrical|utility) lines? down"
                     r"|tree (?:down )?on (?:the )?wires|tree (?:on|into) (?:a )?(?:house|car|wires)"
                     r"|pole (?:down|snapped|sheared))\b"),
    "alarm": _P(r"\b(?:fire alarm|fire activation|alarm (?:activation|sounding|going off|system)|box alarm|master box"
                r"|municipal box|pull station|smoke detector|sprinkler (?:alarm|activation|head)"
                r"|water ?flow(?: alarm)?)\b"),
    "shots_fired": _P(r"\b(?:shots? fired|shots? (?:heard|being fired)|gun ?shots?|gun ?fire|shooting"
                      r"|person shot|(?:has been|was|been|got) shot|gunshot wounds?|gsw|shot ?spotter)\b"),
    "armed_person": _P(r"\b(?:armed (?:person|subject|suspect|male|female|individual|man|woman|robbery|party)"
                       r"|(?:man|male|female|woman|subject|person|party|someone|suspect) (?:with|armed with|holding"
                       r"|waving|brandishing) (?:a )?(?:gun|firearm|handgun|pistol|rifle|shotgun|knife|machete|weapon)"
                       r"|brandish(?:ed|ing)? (?:a )?(?:gun|firearm|knife|weapon)|weapon (?:involved|showing|displayed)"
                       r"|(?:gun|knife) involved|threatening (?:\w+ )?with (?:a )?(?:gun|knife))\b"),
    "pursuit": _P(r"\b(?:in pursuit|pursuit|pursuing|chasing (?:a|the) (?:vehicle|suspect|car)"
                  r"|fail(?:ed|ing)? to stop|refus(?:ed|ing) to stop|fled on foot"
                  r"|fleeing (?:police|officers|on foot|northbound|southbound|eastbound|westbound))\b"),
    "missing_child": _P(r"\b(?:missing|lost|runaway|endangered) (?:child|kid|juvenile|boy|girl|toddler|infant|baby"
                        r"|teen(?:ager)?|(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve"
                        r"|thirteen)[- ]year[- ]old)\b|\bamber alert\b"
                        r"|\b(?:child|kid|toddler|juvenile) (?:is |has gone |went )?(?:missing|unaccounted for|wandered off)\b"
                        r"|\bcan(?:not|'t) find (?:her|his|their) (?:child|son|daughter|kid)\b"),
    "bomb_threat": _P(r"\b(?:bomb threat|suspicious package|suspicious device|explosive device)\b"),
    "stabbing": _P(r"\b(?:stabbing|stabbed|stab wounds?|knife wound|slashed)\b"),
    "robbery": _P(r"\b(?:robbery|robbed|hold ?up|carjack(?:ed|ing)?|bank alarm)\b"),
    "missing_person": _P(r"\bmissing (?:person|party|elderly|adult|man|woman|male|female|resident|patient|hiker)\b"
                         r"|\b(?:wandered off|walked away from)\b|\bsilver alert\b"
                         r"|\bendangered (?:adult|elderly|person)\b"),
    "break_in": _P(r"\b(?:b ?(?:and|&|n) ?e|breaking and entering|break[- ]?in|burglary|burglar alarm"
                   r"|house break|forced entry|broke into|breaking into)\b"),
    "disturbance": _P(r"\b(?:disturbance|fight|fighting|domestic|assault(?:ed)?|altercation|disorderly"
                      r"|shoplift(?:ing|er)?|larceny|suspicious (?:person|vehicle|activity|male|female|party)"
                      r"|noise complaint|trespass(?:ing)?)\b"),
    "entrapment": _P(r"\b(?:entrapment|entrapped|trapped|pinned (?:in|under)|extrication|extricate|jaws of life"
                     r"|cut (?:him|her|them) out|heavy rescue)\b"),
    "pedestrian_struck": _P(r"\b(?:pedestrian (?:struck|hit|down|versus|vs\.?|accident)|ped (?:struck|hit|versus)"
                            r"|(?:struck|hit) by a (?:car|vehicle|truck|bus|train)"
                            r"|car (?:versus|vs\.?|into|hit) (?:a )?(?:pedestrian|ped|person|bicyclist|cyclist|bike)"
                            r"|(?:bicyclist|cyclist|bicycle|bike) (?:struck|hit|versus|vs\.?))\b"),
    "rollover": _P(r"\b(?:roll ?over|rolled over|on (?:its|it's|the) (?:roof|side)|overturned|flipped(?: over)?)\b"),
    "road_closure": _P(r"\b(?:road (?:is |will be )?(?:closed|closure|shut down|blocked)"
                       r"|clos(?:e|ing|ed)(?: down)? (?:the )?(?:road|street|route|ramp|lanes?|highway"
                       r"|intersection|exit|on ?ramp|off ?ramp)"
                       r"|shut(?:ting)? down (?:the )?(?:road|street|route|highway|traffic|lanes?|ramp)"
                       r"|all lanes (?:are )?(?:blocked|closed|shut)|(?:two|both|three|several|multiple) lanes"
                       r" (?:are )?(?:blocked|closed)|detour(?:ing)? traffic"
                       r"|traffic (?:is |being )?(?:stopped|blocked|diverted|detoured))\b"),
    "mva": _P(r"\b(?:m ?v ?a|motor vehicle (?:accident|crash|collision)|car (?:accident|crash)|crash(?:ed)?"
              r"|collision|accident(?! report)|two[- ]car|(?:two|three|multi)[- ]vehicle"
              r"|(?:car|vehicle|truck|motorcycle|suv) (?:versus|vs\.?|into)"
              r"|(?:into|hit|struck) (?:a |the )?(?:pole|tree|guardrail|guard rail"
              r"|jersey barrier|wall)|hit and run)\b"),
    "cardiac_arrest": _P(r"\b(?:cardiac arrest|cpr (?:in progress|being done|started)|(?:performing|doing|started) cpr"
                         r"|not breathing|no pulse|pulseless|full arrest|code (?:blue|99)|apneic)\b"),
    "overdose": _P(r"\b(?:overdose|over dose|narcan|naloxone|possible o ?d)\b"),
}

# Read BEFORE anything is matched: "no entrapment", "nobody is trapped",
# "negative for fire extension" must not read as the thing they deny.
_NEGATED = (r"(?:entrapment|entrapped|trapped|injur\w*|injuries|fire|smoke|flames?|extension|hazards?|shots?|"
            r"weapons?|gun|knife|pursuit)")
_NEG = re.compile(
    r"\b(?:no|negative(?: for)?|not|nobody|no one|without|nothing)\s+"
    r"(?:(?:a|an|the|any|one|more|further|visible|working|reported|is|are|was|were|signs? of|"
    r"fire|for)\s+){0,3}"
    rf"{_NEGATED}\b"
    # "no smoke or fire showing" denies both, and the "showing" with them
    rf"(?:\s+(?:or|and|nor)\s+(?:any\s+)?{_NEGATED}\b)*(?:\s+(?:showing|visible|evident))?", re.I)
# The traffic says it is over. Read per transmission; a transmission that says
# both "working fire" and "under control" is a report about a fire, not a stand-down.
RESOLVED = re.compile(
    r"\b(?:false alarm|nothing (?:showing|found|evident|visible|on arrival)|unfounded|under control"
    r"|knock ?down|knocked down|fire (?:is )?(?:out|knocked down|under control)|all clear"
    r"|(?:units?|engines?|companies|everyone|all units) (?:can |may )?(?:clear|return|go back)"
    r"|clearing (?:the )?scene|(?:back|returning) (?:in|to) (?:service|quarters)"
    r"|cancel (?:all units|the call|that call)|disregard|code 4|unable to locate|gone on arrival"
    r"|no longer needed|stand ?down|accidental activation|burnt food)\b", re.I)
_STRUCTURE = re.compile(r"\b(?:structure|house|home|building|apartment|dwelling|residence|residential|"
                        r"commercial|condo|duplex|roof|attic|basement|eaves|floor|garage)\b", re.I)
_VEHICLE = re.compile(r"\b(?:car|vehicle|auto|truck|bus|motorcycle)\b", re.I)
_ELEVATOR = re.compile(r"\belevator\b", re.I)
_CONFIRMED = re.compile(r"\b(?:confirm(?:ed|ing)?|we have|we've got|got)\b", re.I)

# ---------------------------------------------------------------- spoken numbers
_ONES = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
         "eight": 8, "nine": 9}
_TEENS = {"ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
          "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
         "eighty": 80, "ninety": 90}


def _spoken_number(words: list[str]) -> tuple[int | None, int]:
    """'four ninety five' -> (495, 3), 'one twenty six' -> (126, 3),
    'one hundred twenty six' -> (126, 4), 'twenty seven' -> (27, 2), 'nine' -> (9, 1)."""
    w = [x.strip("-,.") for x in words[:5]]

    def tens_at(i: int) -> tuple[int | None, int]:
        if i < len(w) and w[i] in _TENS:
            v = _TENS[w[i]]
            if i + 1 < len(w) and w[i + 1] in _ONES and _ONES[w[i + 1]] > 0:
                return v + _ONES[w[i + 1]], 2
            return v, 1
        if i < len(w) and w[i] in _TEENS:
            return _TEENS[w[i]], 1
        return None, 0

    if not w:
        return None, 0
    if w[0] in _ONES:
        if len(w) > 2 and w[1] == "hundred":
            t, n = tens_at(2)
            return _ONES[w[0]] * 100 + (t or 0), 2 + n
        t, n = tens_at(1)
        if t is not None:
            return _ONES[w[0]] * 100 + t, 1 + n          # "four ninety five"
        return _ONES[w[0]], 1
    return tens_at(0)


_NUM_WORD = "|".join(list(_ONES) + list(_TEENS) + list(_TENS) + ["hundred"])
_SPOKEN_AFTER = re.compile(rf"\b(route|rt|rte|interstate|highway|exit|i)\s+((?:(?:{_NUM_WORD})[\s-]+)*(?:{_NUM_WORD}))\b",
                           re.I)
# Highways people name by number alone, said the way people say them.
_BARE_HIGHWAYS = {"four ninety five": "495", "one twenty eight": "128", "two ninety": "290"}


def normalise(text: str) -> str:
    """Lower case, one space, highway numbers as digits ('route nine' -> 'route 9')."""
    t = re.sub(r"\s+", " ", (text or "").lower()).strip()
    for said, digits in _BARE_HIGHWAYS.items():
        t = re.sub(rf"\b{said}\b", digits, t)

    def one(m: "re.Match") -> str:
        v, _n = _spoken_number(re.split(r"[\s-]+", m.group(2)))
        return f"{m.group(1)} {v}" if v is not None else m.group(0)
    t = _SPOKEN_AFTER.sub(one, t)
    return re.sub(r"\b(?:rt|rte)\.?\s+(\d)", r"route \1", t)


# ---------------------------------------------------------------- his roads
# Route 9, 30, 126, 135, 20, 27, 85, I-90 (the Pike), I-95/128, I-495, Route 290.
_ROUTE_NAMES = {"9": "Route 9", "20": "Route 20", "27": "Route 27", "30": "Route 30", "85": "Route 85",
                "126": "Route 126", "135": "Route 135", "290": "Route 290", "90": "the Mass Pike",
                "95": "Route 128", "128": "Route 128", "495": "Route 495"}
_ROUTE_RE = re.compile(r"\b(?:route|highway|state route)\s+(9|20|27|30|85|126|135|290|128|495|95|90)\b"
                       r"|\b(?:i|interstate)[\s-]*(90|95|495|290)\b"
                       r"|\b(128|495|290)\s+(?:north|south|east|west)(?:bound)?\b"
                       r"|\b(?:on|onto|off) (128|495|290)\b", re.I)
_PIKE = re.compile(r"\b(?:mass(?:achusetts)? ?pike|masspike|the pike|turnpike)\b", re.I)
# Local names for his routes, only in the towns where the name IS the route.
_ROAD_ALIASES = (
    (re.compile(r"\bworcester (?:road|rd|street|st)\b", re.I), "Route 9",
     {"Framingham", "Natick", "Wellesley", "Southborough", "Westborough", "Shrewsbury", "Newton"}),
    (re.compile(r"\bboston post (?:road|rd)\b", re.I), "Route 20",
     {"Sudbury", "Wayland", "Marlborough", "Weston", "Waltham", "Northborough"}),
    (re.compile(r"\bcochituate (?:road|rd)\b", re.I), "Route 30", {"Framingham", "Wayland"}),
    (re.compile(r"\bcommonwealth (?:avenue|ave)\b", re.I), "Route 30", {"Newton"}),
    (re.compile(r"\b(?:concord|hollis) (?:street|st)\b", re.I), "Route 126", {"Framingham", "Ashland"}),
    (re.compile(r"\bwaverl(?:e)?y (?:street|st)\b", re.I), "Route 135", {"Framingham"}),
)


def my_roads(text: str, town: str = "") -> list[str]:
    """The roads he drives that this text names, as he would say them."""
    t = normalise(text)
    out: list[str] = []
    for m in _ROUTE_RE.finditer(t):
        num = next(g for g in m.groups() if g)
        name = _ROUTE_NAMES.get(num)
        if name and name not in out:
            out.append(name)
    if _PIKE.search(t) and "the Mass Pike" not in out:
        out.append("the Mass Pike")
    for rx, name, towns in _ROAD_ALIASES:
        if town in towns and rx.search(t) and name not in out:
            out.append(name)
    return out


# ---------------------------------------------------------------- addresses and units
_SUFFIX = {"street": "St", "st": "St", "avenue": "Ave", "ave": "Ave", "road": "Rd", "rd": "Rd",
           "drive": "Dr", "dr": "Dr", "lane": "Ln", "ln": "Ln", "way": "Way", "court": "Ct", "ct": "Ct",
           "place": "Pl", "pl": "Pl", "boulevard": "Blvd", "blvd": "Blvd", "circle": "Cir", "cir": "Cir",
           "terrace": "Ter", "ter": "Ter", "parkway": "Pkwy", "pkwy": "Pkwy", "highway": "Hwy",
           "square": "Sq", "sq": "Sq", "path": "Path", "turnpike": "Tpke", "plaza": "Plaza", "row": "Row"}
_SPOKEN_SUFFIX = {"St": "Street", "Ave": "Avenue", "Rd": "Road", "Dr": "Drive", "Ln": "Lane", "Ct": "Court",
                  "Pl": "Place", "Blvd": "Boulevard", "Cir": "Circle", "Ter": "Terrace", "Pkwy": "Parkway",
                  "Hwy": "Highway", "Sq": "Square", "Tpke": "Turnpike"}
_UNIT_WORDS = {"engine", "ladder", "rescue", "medic", "ambulance", "truck", "tower", "squad", "tanker",
               "brush", "forestry", "marine", "boat", "battalion", "deputy", "district", "car", "unit",
               "cruiser", "sector", "sergeant", "lieutenant", "captain", "k9", "box", "exit", "station"}
_STOP = _UNIT_WORDS | {
    "respond", "responding", "response", "to", "for", "at", "on", "in", "the", "a", "an", "with", "and",
    "of", "units", "report", "reported", "reporting", "caller", "from", "by", "near", "is", "are", "was",
    "be", "structure", "fire", "smoke", "showing", "alarm", "crash", "accident", "mva", "route", "rt",
    "eastbound", "westbound", "northbound", "southbound", "area", "vicinity", "police", "please", "go",
    "going", "that", "this", "there", "here", "out", "up", "down", "just", "we", "have", "got", "its",
    "it's", "who", "what", "off", "into", "onto", "under", "over", "corner", "front", "rear", "side",
    "behind", "across", "possible", "working", "heavy", "house", "building", "apartment", "car's",
    "vehicle", "person", "party", "male", "female", "en", "enroute", "copy", "received", "okay", "ok",
}
_DIRS = {"eastbound", "westbound", "northbound", "southbound"}
_UNIT_RE = re.compile(r"\b(engine|ladder|rescue|medic|ambulance|truck|tower|squad|tanker|brush|forestry|marine"
                      r"|battalion|deputy|car|cruiser)\s*(\d{1,3})\b", re.I)


def _title(words: list[str]) -> str:
    return " ".join(w[:1].upper() + w[1:] for w in words)


def _street_spans(tokens: list[str]) -> list[dict]:
    """Street names (and 'Route 9') found in a token list, in order."""
    spans = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "route" and i + 1 < len(tokens) and tokens[i + 1].isdigit():
            end = i + 1
            d = tokens[end + 1] if end + 1 < len(tokens) and tokens[end + 1] in _DIRS else ""
            spans.append({"start": i, "end": end + (1 if d else 0), "name": f"Route {tokens[i + 1]}",
                          "key": f"route {tokens[i + 1]}", "dir": d})
            i = end + 1
            continue
        if tok.rstrip(".") in _SUFFIX and i > 0:
            name: list[str] = []
            j = i - 1
            while j >= 0 and len(name) < 3:
                w = tokens[j].strip(".'")
                if not w.isalpha() or w in _STOP:
                    break
                name.insert(0, w)
                j -= 1
            if name:
                suf = _SUFFIX[tok.rstrip(".")]
                spans.append({"start": j + 1, "end": i, "name": f"{_title(name)} {suf}",
                              "key": f"{' '.join(name)} {suf.lower()}", "dir": ""})
        i += 1
    return spans


# Police and fire spelling alphabets (APCO and NATO). Two of these beside a
# "street" mean a plate or a name is being spelled out: "Master 3, Fox drive,
# Charlie, Romeo 213" was a licence plate, not 3 Fox Drive (2026-11-07).
_PHONETIC = {"adam", "boy", "charlie", "david", "edward", "frank", "george", "henry", "ida", "john",
             "king", "lincoln", "mary", "nora", "ocean", "paul", "queen", "robert", "sam", "tom",
             "victor", "william", "xray", "young", "zebra", "alpha", "bravo", "delta", "echo",
             "foxtrot", "golf", "hotel", "india", "juliet", "kilo", "lima", "mike", "november", "oscar",
             "papa", "quebec", "romeo", "sierra", "tango", "uniform", "whiskey", "yankee", "zulu"}


def _capitals(text: str) -> tuple[set[str], set[str]]:
    """(words written with a capital anywhere, words with a capital that is
    not just the start of a sentence), lowercased. Parakeet writes names with
    capitals, so a street it heard as a name is in here and "why do you cut
    it about one place" is not."""
    anywhere: set[str] = set()
    inner: set[str] = set()
    for m in re.finditer(r"[A-Za-z][A-Za-z']*", text or ""):
        w = m.group(0)
        if not w[0].isupper():
            continue
        anywhere.add(w.lower())
        before = text[:m.start()].rstrip()
        if before and before[-1] not in ".!?":
            inner.add(w.lower())
    return anywhere, inner


def _believable(span: dict, tokens: list[str], cased: bool, caps: tuple[set[str], set[str]]) -> bool:
    """Is this 'street' a street? Not when it sits inside a spelled-out plate,
    and - when the transcript has capitals at all - not unless it was written
    as a name ("It About One Pl" came from "cut it about one place")."""
    near = tokens[max(0, span["start"] - 5):span["start"]] + tokens[span["end"] + 1:span["end"] + 5]
    if sum(1 for w in near if w.strip(".,'") in _PHONETIC) >= 2:
        return False
    if span["name"].startswith("Route") or not cased:
        return True
    anywhere, inner = caps
    name = span["key"].split()[:-1]
    suffix = tokens[span["end"]].rstrip(".")
    return bool(name) and all(w in anywhere for w in name) and \
        (any(w in inner for w in name) or suffix in inner)


def extract_address(text: str) -> tuple[str, str]:
    """(address as written, street key for merging). A house number and a
    street first, then an intersection, then a route with its direction, then
    a street on its own. ('', '') when the transmission names no place."""
    t = normalise(text)
    tokens = re.findall(r"[a-z0-9][a-z0-9'.]*|&", t)
    cased = any(c.isupper() for c in text or "") and any(c.islower() for c in text or "")
    caps = _capitals(text)
    spans = [s for s in _street_spans(tokens) if _believable(s, tokens, cased, caps)]
    # a house number: digits right before the street, not a unit's number
    for s in spans:
        k = s["start"] - 1
        if k >= 0 and re.fullmatch(r"\d{1,5}[a-z]?", tokens[k]) and not s["name"].startswith("Route"):
            if k >= 1 and tokens[k - 1] in _UNIT_WORDS:
                continue
            return f"{tokens[k].upper() if tokens[k][-1:].isalpha() else tokens[k]} {s['name']}", s["key"]
    for a, b in zip(spans, spans[1:]):
        joint = tokens[a["end"] + 1] if b["start"] - a["end"] == 2 else ""
        if joint in ("and", "&", "at", "near", "by"):
            first = f"{a['name']} {a['dir']}".strip()
            return f"{first} {'at' if joint in ('at', 'near', 'by') else 'and'} {b['name']}", a["key"]
    for s in spans:
        if s["name"].startswith("Route"):
            return (f"{s['name']} {s['dir']}".strip(), s["key"])
    if spans:
        return spans[0]["name"], spans[0]["key"]
    return "", ""


def spoken_address(address: str) -> str:
    """'45 Union Ave' -> '45 Union Avenue', for the voice."""
    words = (address or "").split()
    return " ".join(_SPOKEN_SUFFIX.get(w, w) for w in words)


_UNIT_WORD_RE = re.compile(r"\b(engine|ladder|rescue|medic|ambulance|truck|tower|squad|tanker|brush|forestry"
                           r"|marine|battalion|deputy|car|cruiser)\s+(one|two|three|four|five|six|seven|eight"
                           r"|nine|ten|eleven|twelve)\b", re.I)


def extract_units(text: str) -> list[str]:
    """'Engine 3, Ladder 1' - and the way Parakeet often writes them, 'car
    three, ambulance one' (2026-11-07, Sudbury FD)."""
    found: list[tuple[int, str]] = []
    for m in _UNIT_RE.finditer(text or ""):
        found.append((m.start(), f"{m.group(1).title()} {m.group(2)}"))
    words = {**_ONES, **_TEENS}
    for m in _UNIT_WORD_RE.finditer(text or ""):
        found.append((m.start(), f"{m.group(1).title()} {words[m.group(2).lower()]}"))
    out: list[str] = []
    for _pos, u in sorted(found):
        if u not in out:
            out.append(u)
    return out[:8]


# ---------------------------------------------------------------- the details
# What a call is like, beyond what kind it is: the facts he would want on his
# phone ("2 vehicles, one patient, transported to MetroWest"). Each is a short
# phrase, said only when the words are there - never inferred.
_HOSPITALS = (("metrowest", "MetroWest"), ("metro west", "MetroWest"), ("framingham union", "MetroWest"),
              ("leonard morse", "Leonard Morse"), ("newton wellesley", "Newton-Wellesley"),
              ("newton-wellesley", "Newton-Wellesley"), ("emerson", "Emerson"), ("marlborough hospital",
              "Marlborough Hospital"), ("marlboro hospital", "Marlborough Hospital"), ("milford", "Milford"),
              ("umass", "UMass Memorial"), ("u mass", "UMass Memorial"), ("mass general", "Mass General"),
              ("mgh", "Mass General"), ("beth israel", "Beth Israel"), ("st. elizabeth", "St. Elizabeth's"),
              ("saint elizabeth", "St. Elizabeth's"), ("brigham", "the Brigham"), ("children's", "Children's"))
_N = r"(one|two|three|four|five|1|2|3|4|5)"
_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
_DETAILS: tuple[tuple[re.Pattern, str], ...] = tuple((re.compile(p, re.I), d) for p, d in (
    (r"\bhit[- ]and[- ]run\b", "hit and run"),
    (r"\b(?:single|one|1)[- ]?(?:car|vehicle)\b(?! (?:one|two|three|four|five|\d))", "1 vehicle"),
    (rf"\b{_N}[- ]?(?:car|vehicle)s?\b(?! (?:one|two|three|four|five|\d))", "{n} vehicles"),
    (r"\bno (?:apparent |reported )?injur(?:y|ies)\b|\bnegative (?:for )?injur|\buninjured\b|\bno one (?:is )?hurt\b",
     "no injuries"),
    (r"\bminor injur", "minor injuries"),
    (r"\b(?:serious|severe|life[- ]threatening|critical) injur|\bseriously (?:injured|hurt)\b", "serious injuries"),
    (rf"\b{_N} patients?\b", "{n} patient(s)"),
    (r"\b(?:unconscious|unresponsive)\b", "someone unconscious"),
    (r"\bconscious and breathing\b", "conscious and breathing"),
    (r"\bcpr\b", "CPR in progress"),
    (r"\brefus(?:ed|ing|al)\b", "a patient refused transport"),
    (r"\bairbags?\b", "airbags deployed"),
    (r"\bfront[- ]end damage\b", "front-end damage"),
    (r"\b(?:heavy|major|significant|extensive) damage\b", "heavy damage"),
    (r"\bminor damage\b", "minor damage"),
    (r"\bdisabled (?:vehicle|car)\b", "a disabled vehicle"),
    (r"\b(?:doesn'?t|does not|don'?t|do not) (?:wish|want) to (?:file|make|pursue) (?:a |the )?report\b",
     "no report wanted"),
    (r"\bfluids?\b", "fluids on the road"),
    (r"\btow (?:truck|requested)\b|\brequest(?:ing)? a tow\b|\bneed a tow\b", "tow requested"),
    (r"\b(?:lanes?|road|roadway|street) (?:is |are )?(?:blocked|closed|shut down)\b|\bblocking (?:the |a )?lane",
     "road blocked"),
    (r"\b(?:fire|flames) (?:showing|visible|through the roof)\b|\bfully involved\b", "fire showing"),
    (r"\bsmoke (?:showing|visible|in the (?:building|house|basement))\b", "smoke showing"),
    (r"\bnothing (?:showing|found|evident)\b", "nothing showing"),
    (r"\b(?:everyone|everybody|all occupants|occupants) (?:is |are )?out\b", "everyone out"),
    (r"\bon (?:scene|location)\b", "units on scene"),
    (r"\bmutual aid\b", "mutual aid"),
    (r"\bpolice (?:requested|needed|to respond)\b|\brequest(?:ing)? (?:pd|police)\b", "police requested"),
))


def details(text: str) -> list[str]:
    """Short facts in one transmission, in the order of _DETAILS."""
    t = normalise(text)
    scrubbed = _NEG.sub(" ~neg~ ", t)
    out: list[str] = []
    denied_injury = False
    for rx, d in _DETAILS:
        # "no injuries" is read from the text itself; everything after it from
        # the text with denials scrubbed, so "no fire showing" is not fire
        src = t if d == "no injuries" else scrubbed
        m = rx.search(src)
        if not m:
            continue
        if d == "no injuries":
            denied_injury = True
        if denied_injury and d in ("minor injuries", "serious injuries"):
            continue
        if "{n}" in d:
            n = m.group(1).lower()
            n = _NUM.get(n, n)
            d = d.replace("{n}", str(n))
            if d.endswith("(s)"):
                d = d[:-3] + ("" if str(n) == "1" else "s")
            if d == "1 vehicles":
                d = "1 vehicle"
        if d not in out:
            out.append(d)
    if "a patient refused transport" not in out and re.search(r"\btransport(?:ing|ed)?\b", t):
        place = next((name for key, name in _HOSPITALS if key in t), "")
        out.append(f"transported to {place}" if place else "a patient transported")
    return out[:8]


# ---------------------------------------------------------------- reading one transmission
@dataclass
class Reading:
    types: list[str] = field(default_factory=list)   # highest severity first
    severity: int = 0
    danger: bool = False
    family: str = ""
    address: str = ""
    street: str = ""
    roads: list[str] = field(default_factory=list)
    units: list[str] = field(default_factory=list)
    resolved: bool = False
    confirmed: bool = False      # "entrapment confirmed", "we have a working fire"
    towns: list[str] = field(default_factory=list)

    @property
    def top(self) -> str:
        return self.types[0] if self.types else ""

    @property
    def label(self) -> str:
        return describe(self.types)


def describe(types: list[str]) -> str:
    """How a set of kinds is said: 'a crash closing the road', 'a working fire'."""
    if not types:
        return ""
    ts = set(types)
    top = max(types, key=lambda k: KINDS[k].severity if k in KINDS else 0)
    if top == "road_closure" and ts & TRAFFIC:
        return "a crash closing the road"
    if top in TRAFFIC and "road_closure" in ts:
        return KINDS[top].label + " closing the road"
    if top == "multiple_alarm":
        return "a multiple-alarm fire"
    return KINDS[top].label if top in KINDS else BURST.label


_MUTUAL_AID = re.compile(r"\b(?:mutual aid|cover(?:ing)?|assist(?:ing)?|requested by|box to|station coverage)\b", re.I)
TRUST = 0.35      # below this a transcript is kept but never starts or changes an incident


def read(text: str, channel_town: str = "") -> Reading:
    """Everything the rules can see in one transmission."""
    t = normalise(text)
    scrubbed = _NEG.sub(" ~neg~ ", t)
    found = [k for k, rx in PATTERNS.items() if rx.search(scrubbed)]
    ts = set(found)
    # a vehicle that is "fully involved" is a vehicle fire, not a house
    if "vehicle_fire" in ts and not _STRUCTURE.search(scrubbed):
        ts -= {"working_fire", "smoke_showing"}
    if ts & {"working_fire", "smoke_showing"} and _VEHICLE.search(scrubbed) and not _STRUCTURE.search(scrubbed):
        ts -= {"working_fire", "smoke_showing"}
        ts.add("vehicle_fire")
    # smoke with no building behind it is an investigation, not a fire
    if "smoke_showing" in ts and not _STRUCTURE.search(scrubbed) and not ts & {"structure_fire", "working_fire"}:
        ts.discard("smoke_showing")
        ts.add("smoke_investigation")
    # an elevator rescue is not a crash
    if "entrapment" in ts and _ELEVATOR.search(scrubbed):
        ts.discard("entrapment")
    # people trapped in a burning building are not a crash either - and are worse
    if "entrapment" in ts and ts & {"working_fire", "structure_fire", "smoke_showing", "multiple_alarm"}:
        ts.discard("entrapment")
        ts -= {"structure_fire", "smoke_showing"}
        ts.add("persons_trapped")
    # a kitchen fire is not a structure fire unless it says it has spread
    if "small_fire" in ts and "structure_fire" in ts and not ts & {"working_fire", "smoke_showing"}:
        ts.discard("structure_fire")
    # the bigger word wins inside a family: a working fire is also a structure fire
    if ts & {"working_fire", "multiple_alarm"}:
        ts -= {"structure_fire", "smoke_showing", "smoke_investigation", "alarm"}
    if "armed_person" in ts:
        ts.discard("robbery")
    if "entrapment" in ts or "rollover" in ts or "pedestrian_struck" in ts:
        ts.discard("mva")
    if "missing_child" in ts:
        ts.discard("missing_person")
    types = sorted(ts, key=lambda k: -KINDS[k].severity)
    r = Reading(types=types)
    if types:
        top = KINDS[types[0]]
        r.severity, r.family = top.severity, top.family
        r.danger = any(KINDS[k].danger for k in types)
        # a crash that has closed the road is the active danger on the list
        if "road_closure" in ts and ts & TRAFFIC:
            r.danger = True
    r.resolved = bool(RESOLVED.search(t)) and not r.danger
    r.confirmed = bool(_CONFIRMED.search(t)) and r.danger
    r.address, r.street = extract_address(text)
    r.roads = my_roads(text, channel_town)
    if not r.address and r.roads:
        way = re.search(r"\b(eastbound|westbound|northbound|southbound)\b", t)
        r.address = f"{r.roads[0]} {way.group(1)}" if way else r.roads[0]
        r.street = r.roads[0].lower()
    r.units = extract_units(text)
    r.towns = _other_towns(text, t, channel_town)
    return r


_SUFFIX_ALT = "|".join(sorted(_SUFFIX, key=len, reverse=True))


def _other_towns(text: str, t: str, channel_town: str) -> list[str]:
    """Another town named as WHERE this is: beside mutual-aid words, or as
    "in Natick". A town's name inside a street's ("Worcester Road", "Boston
    Post Road", "Natick Avenue") is a street, not a place."""
    out: list[str] = []
    try:
        found = geo.find_towns(text or "")
    except Exception:
        return out
    aid = bool(_MUTUAL_AID.search(t))
    for f in found:
        name = f.town.name
        if name == channel_town or f.miles > 40 or name in out:
            continue
        said = re.escape(f.said.lower())
        place = re.compile(rf"\b{said}\b(?!\s+(?:[a-z]+\s+)?(?:{_SUFFIX_ALT})\b)")
        spots = list(place.finditer(t))
        if not spots:
            continue
        if aid or any(re.search(r"\b(?:in|into|to|of)\s+$", t[:m.start()]) for m in spots):
            out.append(name)
    return out


def escalation(old_types: list[str], new: Reading) -> str:
    """The words for what just got worse ('a second alarm', 'entrapment
    confirmed'), or '' when nothing did."""
    old = max((KINDS[k].severity for k in old_types if k in KINDS), default=0)
    if new.severity <= old and not (new.confirmed and "entrapment" in new.types and "entrapment" in old_types):
        return ""
    top = new.top
    if top == "multiple_alarm":
        return "it has gone to a second alarm"
    if top == "persons_trapped":
        return "people are reported trapped"
    if top == "entrapment":
        return "entrapment confirmed" if new.confirmed else "there is entrapment"
    if top == "working_fire":
        return "it is a working fire"
    return describe(new.types)


# ---------------------------------------------------------------- how much to trust a transcript
_DISPATCH_WORDS = re.compile(
    r"\b(?:engine|ladder|rescue|medic|ambulance|respond|responding|units?|car|cruiser|dispatch|copy|received"
    r"|en ?route|on scene|street|avenue|road|route|caller|report(?:ed|ing)?|alarm|fire|police|officer|address"
    r"|scene|clear|station|priority|command|sector|patient|male|female|vehicle|plate|registration)\b", re.I)
_JUNK = re.compile(r"^(?:thank you|thanks|you|bye|okay|ok|uh+|um+|hmm+|yeah|no|yes|the|a|so|oh)[.!?]*$", re.I)


def asr_confidence(text: str, duration_s: float, quality: dict | None = None) -> float:
    """A HEURISTIC 0..1 for a machine transcript of radio audio - Parakeet
    reports no confidence of its own. Speaking rate inside what people do on a
    radio, dispatch vocabulary, the console's own quality call; a short
    stock phrase or a word repeated over and over (what a recogniser makes of
    squelch hiss) scores low."""
    s = (text or "").strip()
    words = re.findall(r"[A-Za-z']+|\d+", s)
    n = len(words)
    if n == 0 or _JUNK.match(s):
        return 0.0 if n == 0 else 0.1
    rate = n / max(float(duration_s or 0.0), 0.5)
    score = 0.45
    score += 0.15 if 0.8 <= rate <= 4.8 else -0.2
    score += min(0.25, 0.06 * len(_DISPATCH_WORDS.findall(s)))
    if n >= 5:
        top = Counter(w.lower() for w in words).most_common(1)[0][1]
        if top / n > 0.4:
            score -= 0.35
    if n <= 2:
        score -= 0.15
    cls = str((quality or {}).get("class") or "")
    if cls == "analog_clear":
        score += 0.1
    elif cls == "analog_noisy":
        score -= 0.05
    return round(max(0.0, min(1.0, score)), 2)


def transcribable(quality: dict | None) -> bool:
    """Worth running the recogniser on: everything except what the console
    already knows has no words in it (digital voice, squelch noise, silence)."""
    cls = str((quality or {}).get("class") or "")
    return cls not in ("digital", "noise_squelch", "no_audio")
