"""Where things are, relative to him: every Massachusetts town, and how far.

One table, shared by everything that has to decide whether something is near
him - the news judge, the scanner desk, the briefs. Before this each of them
kept its own little list of town names, and a list of names cannot tell
Gloucester, Massachusetts from Gloucester, England: on 2026-11-06 a sword
incident outside a school in Gloucestershire went to his phone four times as
"a hazard loose in his state".

The towns are the Census Bureau's 2024 county-subdivision gazetteer for
Massachusetts (all 351 cities and towns, interior points), committed as
`data/ma_towns.csv`. Distances are great-circle miles from his home, which is
Framingham 01701 unless `home.lat`/`home.lon` say otherwise.

The careful part is ambiguity. Many Massachusetts town names are also places
somewhere else (Sudbury is in Suffolk and Ontario, Concord in New Hampshire,
Lincoln in Nebraska, Boston in Lincolnshire). `find_towns` therefore reports
how sure it is: a name that only Massachusetts uses is certain; a shared name
counts only with corroboration - "Mass." or "Massachusetts" in the text, a
Massachusetts outlet as the source, a village or road that only exists here -
and never when the text carries a foreign marker.
"""
from __future__ import annotations

import csv
import math
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from config import config

DATA = Path(__file__).resolve().parent / "data" / "ma_towns.csv"

# Framingham 01701, the Census interior point of the city.
HOME_LAT, HOME_LON = 42.30791, -71.43620
DEFAULT_RADIUS_MI = 20.0

# Spellings people and dispatchers actually use.
ALIASES = {
    "marlboro": "Marlborough", "southboro": "Southborough", "westboro": "Westborough",
    "northboro": "Northborough", "boxboro": "Boxborough", "foxboro": "Foxborough",
    "attleboro": "Attleboro", "middleboro": "Middleborough", "jamaica plain": "Boston",
    "framingham center": "Framingham", "saxonville": "Framingham", "nobscot": "Framingham",
    "cochituate": "Wayland", "fayville": "Southborough", "cordaville": "Southborough",
    "newton centre": "Newton", "newton center": "Newton", "west newton": "Newton",
    "newtonville": "Newton", "auburndale": "Newton", "waban": "Newton",
    "chestnut hill": "Newton", "newton highlands": "Newton", "newton upper falls": "Newton",
    "newton lower falls": "Newton", "nonantum": "Newton", "oak hill": "Newton",
    "dorchester": "Boston", "roxbury": "Boston", "back bay": "Boston", "south boston": "Boston",
    "east boston": "Boston", "allston": "Boston", "brighton": "Boston", "charlestown": "Boston",
    "hyde park": "Boston", "west roxbury": "Boston", "roslindale": "Boston", "mattapan": "Boston",
    "south end": "Boston", "north end": "Boston", "beacon hill": "Boston", "fenway": "Boston",
    "seaport": "Boston", "mission hill": "Boston", "west concord": "Concord",
    "west acton": "Acton", "south acton": "Acton", "north sudbury": "Sudbury",
    "south sudbury": "Sudbury", "wellesley hills": "Wellesley", "babson park": "Wellesley",
    "south natick": "Natick", "east natick": "Natick", "west medway": "Medway",
    "hopkinton center": "Hopkinton", "woodville": "Hopkinton", "gleasondale": "Stow",
    "rochdale": "Leicester", "whitinsville": "Northbridge", "cambridgeport": "Cambridge",
}

# Names Massachusetts shares with places that turn up in news he reads. These
# need corroboration before they count as here.
SHARED_NAMES = {
    "Gloucester", "Worcester", "Cambridge", "Manchester", "Concord", "Salem", "Springfield",
    "Lincoln", "Newton", "Hudson", "Acton", "Boston", "Andover", "Bedford", "Dover", "Plymouth",
    "Chelsea", "Arlington", "Lexington", "Milton", "Medford", "Marlborough", "Hopkinton",
    "Bolton", "Essex", "Sudbury", "Stow", "Waltham", "Wellesley", "Burlington", "Franklin",
    "Clinton", "Lancaster", "Washington", "Canton", "Warren", "Monroe", "Lenox", "Holland",
    "Hamilton", "Hampden", "Winchester", "Weston", "Wayland", "Woburn", "Reading", "Richmond",
    "Ashland", "Auburn", "Berlin", "Bristol", "Carlisle", "Chester", "Dalton", "Dudley",
    "Easton", "Florida", "Granby", "Halifax", "Harvard", "Kingston", "Lee", "Leicester",
    "Lowell", "Ludlow", "Lynn", "Mansfield", "Marion", "Middleton", "Norfolk", "Northampton",
    "Norwood", "Oxford", "Palmer", "Peru", "Princeton", "Quincy", "Rochester", "Rockport",
    "Sharon", "Sheffield", "Shirley", "Somerset", "Southampton", "Sterling", "Stockbridge",
    "Stoughton", "Sutton", "Taunton", "Truro", "Upton", "Wales", "Ware", "Warwick", "Wells",
    "Westfield", "Westminster", "Weymouth", "Williamsburg", "Wilmington", "Windsor", "York",
    "Athol", "Avon", "Barre", "Becket", "Belmont", "Brookline", "Buckland", "Charlton",
    "Chatham", "Cheshire", "Chesterfield", "Colrain", "Dennis", "Douglas", "Egremont",
    "Erving", "Groton", "Hadley", "Hancock", "Harwich", "Hatfield", "Holden", "Hull",
    "Huntington", "Leverett", "Leyden", "Malden", "Mendon", "Milford", "Millis", "Monson",
    "Montague", "Nahant", "Needham", "Newbury", "Orange", "Otis", "Paxton", "Pembroke",
    "Phillipston", "Plainfield", "Rowley", "Royalston", "Rutland", "Savoy", "Shelburne",
    "Sherborn", "Shrewsbury", "Southbridge", "Spencer", "Templeton", "Tyringham", "Wakefield",
    "Walpole", "Wendell", "Westport", "Whately", "Wilbraham", "Winthrop", "Worthington",
}

# The text is about somewhere else: the town name is a coincidence.
FOREIGN = re.compile(
    r"\b(?:england|english|britain|british|u\.?k\.?|scotland|wales|welsh|ireland|irish|"
    r"gloucestershire|worcestershire|cambridgeshire|lincolnshire|suffolk county council|"
    r"ontario|canada|canadian|australia|new zealand|new hampshire|n\.h\.|vermont|"
    r"connecticut|rhode island|maine|new york|ohio|nebraska|california|oregon|kentucky|"
    r"virginia|texas|tennessee|pupils|armed police|met police|nhs|council estate|"
    r"county council|mp\b|parliament)\b", re.I)
HERE = re.compile(r"\b(?:mass\.|massachusetts|metrowest|middlesex county|worcester county|"
                  r"norfolk county|route 9|route 128|i-?495|i-?90|mass ?pike|"
                  r"massdot|mbta|commonwealth of massachusetts)\b", re.I)
# Outlets whose stories are Massachusetts stories unless they say otherwise.
MA_SOURCES = re.compile(r"masslive|wcvb|whdh|boston ?globe|boston ?25|nbc ?boston|"
                        r"metrowest|wbz|boston\.com|boston herald|7news|universal hub", re.I)


@dataclass(frozen=True)
class Town:
    name: str
    lat: float
    lon: float

    @property
    def miles(self) -> float:
        return distance_mi(self.lat, self.lon)


@dataclass(frozen=True)
class Found:
    town: Town
    certain: bool          # unique to Massachusetts, or corroborated
    said: str              # the words in the text that matched

    @property
    def miles(self) -> float:
        return self.town.miles


def home() -> tuple[float, float]:
    lat = config.get("home", "lat", default=None)
    lon = config.get("home", "lon", default=None)
    try:
        return (float(lat), float(lon)) if lat is not None and lon is not None else (HOME_LAT, HOME_LON)
    except (TypeError, ValueError):
        return HOME_LAT, HOME_LON


def radius_mi() -> float:
    try:
        return float(config.get("home", "radius_mi", default=DEFAULT_RADIUS_MI))
    except (TypeError, ValueError):
        return DEFAULT_RADIUS_MI


def distance_mi(lat: float, lon: float, frm: tuple[float, float] | None = None) -> float:
    """Great-circle miles from `frm` (his home by default)."""
    la, lo = frm or home()
    r = 3958.8
    p1, p2 = math.radians(la), math.radians(lat)
    dp, dl = math.radians(lat - la), math.radians(lon - lo)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


@lru_cache(maxsize=1)
def towns() -> dict[str, Town]:
    """Lower-case name -> Town, every Massachusetts city and town."""
    out: dict[str, Town] = {}
    with open(DATA, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            t = Town(row["name"], float(row["lat"]), float(row["lon"]))
            out[t.name.lower()] = t
    return out


def town(name: str) -> Town | None:
    """A town by name or by an alias/village ("Marlboro", "Saxonville")."""
    key = re.sub(r"\s+", " ", (name or "").strip().lower())
    if not key:
        return None
    t = towns().get(key)
    if t:
        return t
    alias = ALIASES.get(key)
    return towns().get(alias.lower()) if alias else None


def _osa(a: str, b: str) -> int:
    """Edit distance with transpositions (a typo is one edit)."""
    d = [[i + j if i * j == 0 else 0 for j in range(len(b) + 1)] for i in range(len(a) + 1)]
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (a[i - 1] != b[j - 1]))
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[-1][-1]


_AGENCY = re.compile(r"\b(?:pd|fd|police|fire|ems|dpw|dispatch|ambulance|rescue|ch|chan|"
                     r"tac|ops|fireground|car|eng|\d+)\b", re.I)


def match_town(label: str) -> Town | None:
    """The town a scanner channel or agency label belongs to.

    Channel names are 16 characters typed by a person: "Framnghm FD",
    "Bolylston PD", "Leominister FD", "Action PD" (Acton). Exact names and
    aliases first, then a prefix of four or more letters, then one typo per
    six letters. The words that name the agency are ignored.
    """
    words = _AGENCY.sub(" ", (label or "")).strip().lower()
    words = re.sub(r"[^a-z ]+", " ", words).strip()
    if not words:
        return None
    for cand in (words, " ".join(words.split()[:2]), words.split()[0]):
        t = town(cand)
        if t:
            return t
    first = words.split()[0]
    if len(first) < 4:
        return None
    pref = [t for k, t in towns().items() if k.startswith(first)]
    if len(pref) == 1:
        return pref[0]
    best, best_d = None, 99
    for k, t in towns().items():
        if abs(len(k) - len(first)) > 2:
            continue
        dd = _osa(first, k)
        if dd < best_d:
            best, best_d = t, dd
    # dropped letters ("Framnghm" for Framingham) are judged against a
    # consonant skeleton, which is how people abbreviate
    if best is None or best_d > max(1, len(first) // 6):
        skel = lambda s: re.sub(r"[aeiou]", "", s)
        hits = [t for k, t in towns().items() if skel(k).startswith(skel(first)) and len(skel(first)) >= 5]
        return hits[0] if len(hits) == 1 else None
    return best


_NAMES_RE = None

# A TOWN NAME FOLLOWED BY A ROAD WORD IS A ROAD. "211 Boylston Street" is in
# Boston, "Concord Street" runs through Framingham, and Route 9 in Framingham
# is "Worcester Road" - Massachusetts names its streets after the next town
# over, so a name directly in front of one of these is not that town.
# (2026-11-07: "Firefighters evacuate a building at 211 Boylston Street" read
# as Boylston, the town, fifteen miles away.)
_STREET_AFTER = re.compile(
    r"\s+(?:st|street|rd|road|ave|avenue|dr|drive|ln|lane|way|blvd|boulevard|pkwy|parkway|"
    r"tpke|turnpike|pike|hwy|highway|pl|place|ct|court|cir|circle|ter|terrace|sq|square|"
    r"path|trail|extension)\b\.?", re.I)
# (not "Common" or "Green": Framingham Common and Lexington Green are in the
# town they are named for)


def _names_re() -> re.Pattern:
    global _NAMES_RE
    if _NAMES_RE is None:
        names = sorted(set(list(towns().keys()) + list(ALIASES.keys())), key=len, reverse=True)
        _NAMES_RE = re.compile(r"\b(" + "|".join(re.escape(n) for n in names) + r")\b", re.I)
    return _NAMES_RE


def find_towns(text: str, source: str = "") -> list[Found]:
    """Massachusetts towns named in `text`, nearest first, each with whether
    it is CERTAINLY the Massachusetts one.

    Certain when the name is unique to Massachusetts, or when it is shared and
    the text or the outlet places it here - and never when the text names a
    foreign place or another state, which beats a Massachusetts outlet (the
    Herald covers Gloucester, England too).
    """
    text = text or ""
    foreign = bool(FOREIGN.search(text))
    here = bool(HERE.search(text)) or (bool(MA_SOURCES.search(source or "")) and not foreign)
    seen: dict[str, Found] = {}
    for m in _names_re().finditer(text):
        said = m.group(1)
        # a capitalised word, not "the reading" or "a hull"
        if not said[0].isupper():
            continue
        if _STREET_AFTER.match(text, m.end()):
            continue                # "Boylston Street", "Worcester Road"
        t = town(said)
        if t is None:
            continue
        via_alias = said.lower() in ALIASES
        shared = t.name in SHARED_NAMES and not via_alias
        certain = (not foreign) and (not shared or here)
        prev = seen.get(t.name)
        if prev is None or (certain and not prev.certain):
            seen[t.name] = Found(t, certain, said)
    return sorted(seen.values(), key=lambda f: f.miles)


def nearest(text: str, source: str = "") -> Found | None:
    """The nearest town the text CERTAINLY places in Massachusetts."""
    sure = [f for f in find_towns(text, source) if f.certain]
    return sure[0] if sure else None


def within_radius(miles: float | None, radius: float | None = None) -> bool:
    return miles is not None and miles <= (radius if radius is not None else radius_mi())


def towns_within(radius: float | None = None) -> list[Town]:
    """Every town within `radius` miles of home, nearest first."""
    r = radius if radius is not None else radius_mi()
    return sorted((t for t in towns().values() if t.miles <= r), key=lambda t: t.miles)
