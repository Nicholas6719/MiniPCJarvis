"""Is every fact in a model's sentence something the radio actually said?

The scanner's words are a machine's guess at weak audio, and the local model
turned them into facts nobody said when it was tried on real calls
(2026-11-07: "no injuries reported", "responders are on scene"). He asked for
JARVIS to say what is going on rather than paste the transcript (2026-11-08),
and chose a summary that is fact-checked before he sees it.

So a summary is checked one sentence at a time against what was heard (the
transcripts) and what the rules already found word for word (town, channel,
address, units, call type, details). A sentence survives only if every
content word in it is supported: present in the sources, the same word in
another form (injured / injury), a known equivalent (PD -> police, MVA ->
crash), or a near-spelling of a long word (Framingam / Framingham). Numbers
must be in the sources. A sentence that says "no" or "not" needs a negative
in what was heard - "no injuries" is the invention this exists to stop. A
sentence with anything unsupported is dropped whole, never patched: cutting
one word out of "no injuries reported" would change what it says.
"""
from __future__ import annotations

import re

# Words that carry no fact of their own: grammar, and the plain verbs and
# nouns any account of a dispatch uses. Everything else must be supported.
SAFE = frozenset("""
a an the and or but so to at on in into onto near by for of off from with about after before over
under up down out is are was were be been being has have had do does did it its they them their
there here this that these those who what where when which while as than then also just now
someone somebody something person people one some any other another
sent send sending dispatched dispatching responding respond responded heading headed going go goes
called call calls calling asked asking requested requesting request report reports reported reporting
says said say saying heard hear
sounds sound seems seem appears appear apparently possibly probably maybe likely unclear
crew crews unit units responder responders department dispatch dispatcher
incident situation location address area involving involved involves regarding including possible
both all each either more again help check checking asking
may might could would can will possibly perhaps hours
detail details information info unknown known
""".split())

NEGATIVES = frozenset("no not none nobody nothing never without negative cannot".split())

NUMBER_WORDS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
    "seven": "7", "eight": "8", "nine": "9", "ten": "10", "eleven": "11", "twelve": "12",
    "thirteen": "13", "fourteen": "14", "fifteen": "15", "sixteen": "16", "seventeen": "17",
    "eighteen": "18", "nineteen": "19", "twenty": "20", "thirty": "30", "forty": "40", "fifty": "50",
    "hundred": "00", "first": "1", "second": "2", "third": "3",
}

# A word heard (or found by the rules) stands for these too. Kept to what a
# dispatch actually means: "PD" is the police, an engine is the fire
# department, an MVA is a crash.
IMPLIES = {
    "pd": "police officer officers cops", "police": "officer officers cops pd",
    "officer": "police", "officers": "police", "cruiser": "police officer",
    "fd": "fire firefighters", "fire": "firefighters fd", "engine": "fire firefighters truck",
    "ladder": "fire firefighters truck", "tower": "fire firefighters truck", "squad": "fire firefighters",
    "rescue": "fire firefighters ambulance medical", "deputy": "fire chief", "chief": "fire",
    "ambulance": "medical ems paramedics medics", "amb": "ambulance medical", "ems": "ambulance medical",
    "medic": "ambulance medical paramedics medics", "paramedic": "ambulance medical medics",
    "als": "ambulance medical paramedics", "bls": "ambulance medical",
    "mva": "crash accident collision vehicle vehicles car cars", "mvc": "crash accident collision vehicle",
    "crash": "accident collision vehicle vehicles", "accident": "crash collision vehicle vehicles",
    "collision": "crash accident vehicle vehicles", "car": "vehicle", "cars": "vehicles",
    "vehicle": "car", "vehicles": "cars", "truck": "vehicle", "suv": "vehicle", "van": "vehicle",
    "sleeping": "asleep sleep", "asleep": "sleeping", "unresponsive": "medical", "cardiac": "medical heart",
    "breathing": "medical", "seizure": "medical", "overdose": "medical", "fall": "fell medical",
    "fell": "fall", "lift": "medical", "assist": "help", "injury": "injured hurt", "injuries": "injured hurt",
    "injured": "hurt", "transport": "hospital transported transporting taken", "transporting": "hospital",
    "alarm": "alarms", "smoke": "smoke", "investigate": "investigation check checking",
    "investigation": "investigate check", "check": "checking", "welfare": "wellbeing well-being check",
    "st": "street", "street": "st", "rd": "road", "road": "rd", "ave": "avenue", "avenue": "ave",
    "rte": "route", "route": "rte", "hwy": "highway", "highway": "hwy", "dr": "drive", "drive": "dr",
    "ln": "lane", "lane": "ln", "pl": "place", "ct": "court", "sq": "square", "pkwy": "parkway",
    "blvd": "boulevard", "mass": "massachusetts", "pike": "turnpike", "turnpike": "pike",
    "one": "single", "weapon": "armed", "knife": "armed weapon", "gun": "armed weapon firearm",
}

# Set phrases whose words mean nothing on their own ("en route" is not Route 9)
PHRASES = (r"\ben route\b", r"\bon the way\b")


def _words(text: str) -> list[str]:
    t = (text or "").lower().replace("’", "'").replace("n't", " not")
    for ph in PHRASES:
        t = re.sub(ph, " ", t)
    t = re.sub(r"'s\b", "", t)
    # "P D" / "M V A" spoken letter by letter: one word
    t = re.sub(r"\b((?:[a-z] ){1,3}[a-z])\b", lambda m: m.group(1).replace(" ", ""), t)
    return re.findall(r"[a-z]+|\d+", t)


def _stem(w: str) -> str:
    for suf in ("ings", "ing", "ies", "ied", "ers", "er", "ed", "es", "s", "ly"):
        if len(w) > len(suf) + 2 and w.endswith(suf):
            base = w[:-len(suf)]
            return base + "y" if suf in ("ies", "ied") else base
    return w


# "caller" is "call" said another way; a grammar word in another form is still grammar
SAFE_STEMS = frozenset(_stem(w) for w in SAFE if len(w) >= 4)


def _near(a: str, b: str) -> bool:
    """One letter added, dropped or changed (for words of six letters or more)."""
    if min(len(a), len(b)) < 6 or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) <= 1
    if len(a) > len(b):
        a, b = b, a
    for i in range(len(b)):
        if b[:i] + b[i + 1:] == a:
            return True
    return False


class Sources:
    """Everything a summary may lean on, ready to check words against."""

    def __init__(self, heard: list[str], known: list[str]):
        words = [w for t in list(heard) + list(known) for w in _words(t)]
        self.words = set(words)
        for w in words:
            self.words.update(IMPLIES.get(w, "").split())
        self.stems = {_stem(w) for w in self.words}
        self.numbers = {w for w in self.words if w.isdigit()}
        for w in list(self.words):
            if w in NUMBER_WORDS:
                self.numbers.add(NUMBER_WORDS[w])
        # "six hundred main street": the address number spelled out
        joined = " ".join(_words(" ".join(heard)))
        for m in re.finditer(r"\b(one|two|three|four|five|six|seven|eight|nine) hundred\b", joined):
            self.numbers.add(NUMBER_WORDS[m.group(1)] + "00")
        self.negative = any(w in NEGATIVES for t in heard for w in _words(t))
        self.long = [w for w in self.words if len(w) >= 6]

    def supports(self, w: str) -> bool:
        if w in SAFE or w in self.words:
            return True
        if w.isdigit():
            return w in self.numbers
        if w in NUMBER_WORDS:
            return NUMBER_WORDS[w] in self.numbers or w in self.words
        s = _stem(w)
        if s in self.stems or s in SAFE_STEMS:
            return True
        if len(s) >= 5 and any(len(t) >= 5 and (t.startswith(s) or s.startswith(t)) for t in self.stems):
            return True
        return any(_near(w, t) for t in self.long)


def sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?;])\s+|\s+[-–—]\s+", (text or "").strip())
    return [p.strip() for p in parts if p and p.strip(" .;-")]


# A PLATE OR A PERSON IS NOT HIS NEWS (2026-11-11): "2DB342 being operated by
# Daniel Perone possibly intoxicated" reached his phone - a registration and a
# private person's name off a police channel, word for word. A registration
# (letters and digits run together, five to eight long) is never repeated, and
# neither is whoever something is "operated by", "registered to" or "owned by".
_PLATE = re.compile(r"\b(?=[A-Za-z0-9]*\d)(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{5,8}\b")
_NAMED = re.compile(r"\b(?:operated|driven|owned) by\s+[A-Z][a-z]+|\bregistered to\s+[A-Z][a-z]+|"
                    r"\b(?:named|identified as|name is|by the name of)\s+[A-Z][a-z]+")
_CLAUSE = re.compile(r",\s+(?:with|and|but|while|as)\s+|\s+-\s+")


def _judge(s: str, src: "Sources") -> list[str]:
    """The words nothing supports in one sentence or clause ([] = it stands)."""
    words = _words(s)
    bad = [w for w in words if w not in NEGATIVES and not src.supports(w)]
    if any(w in NEGATIVES for w in words) and not src.negative:
        bad.append("(a negative nobody said)")
    if _PLATE.search(s) and not re.fullmatch(r"\d+(?:st|nd|rd|th)", _PLATE.search(s).group(0).lower()):
        bad.append("(a plate or ID number - leave it out)")
    if _NAMED.search(s):
        bad.append("(a person's name - leave it out)")
    return bad


def check(text: str, heard: list[str], known: list[str]) -> tuple[str, list[tuple[str, list[str]]]]:
    """(what survives, [(dropped sentence, the words nothing supports)]).
    What survives is "" when no sentence does. A sentence whose OPENING clause
    stands keeps that clause when only a trailing one fails ("Engine 8
    responding to 22 West Street for two males having trouble breathing, with
    police also on scene" lost everything to its last five words on
    2026-11-11): cutting an add-on never changes what the main clause says."""
    src = Sources(heard, known)
    kept, dropped = [], []
    for s in sentences(text):
        bad = _judge(s, src)
        if bad:
            clauses = [c.strip() for c in _CLAUSE.split(s) if c and c.strip()]
            good = []
            if len(clauses) > 1 and not _judge(clauses[0], src) and len(_words(clauses[0])) >= 4:
                good = [clauses[0]] + [c for c in clauses[1:] if not _judge(c, src)]
            dropped.append((s, bad))
            if not good:
                continue
            s = ", and ".join(c.rstrip(" .;,:") for c in good)
        s = s.rstrip(" ;,:")
        kept.append(s[0].upper() + s[1:] + ("" if s[-1] in ".!?" else "."))
    return " ".join(kept), dropped
