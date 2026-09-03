"""The way he actually says angles and axes.

Its own module so it can be tested without a running stage, and so the skill
slots and the tool handler cannot disagree about what "a quarter turn" means.

THREE DECISIONS WORTH STATING.

WHICH AXIS IS WHICH, in his words rather than the renderer's. "Spin it" and
"turn it" mean about the vertical axis, which is Z — the one the part stands on
the bed on, and the same Z that printcheck measures overhangs from. "Tip it" and
"tilt it" lay it over. "Roll it" turns it about the axis pointing at him. If he
names an axis outright, he gets that axis.

"UPSIDE DOWN" AND "FLIP" NAME AN OUTCOME, NOT AN AXIS, and the outcome is only
reachable about a horizontal one. Left to the verb list, "turn it upside down"
resolved to 180 degrees about the vertical — which leaves it standing exactly as
it was, merely facing backwards.

DEGREES AND FRACTIONS OF A TURN. "Ninety degrees" is ninety. "A quarter turn" is
also ninety, "half a turn" is a hundred and eighty, and "all the way round" is
three hundred and sixty — a real request even though it ends where it started,
because what he is asking for is to WATCH it go round. A bare "turn it" with no
angle is deliberately NOT an error and NOT zero: it is a quarter turn, which is
what a person means when they wave at a model and say that.
"""
from __future__ import annotations

import re

# Vertical is Z. The renderer is Y-up, but every number in this project — STL,
# the slicer, the overhang maths — is Z-up, and that conversion belongs in the
# one place that draws, not in the language.
_AXIS_WORDS = [
    # the vertical axis: how a thing sits on the bed
    (r"\b(?:spin|turn|rotate|yaw|swing)\b", "z"),
    (r"\b(?:vertical(?:ly)?|upright)\b", "z"),
    # laying it over, forwards or backwards
    (r"\b(?:tip|tilt|lean|pitch|nod)\b", "x"),
    # rolling it towards or away from him
    (r"\b(?:roll|bank)\b", "y"),
]

# A named axis beats a verb: "tip it about the z" is about Z, and the verb list
# would otherwise have claimed it for X.
_NAMED_AXIS = r"\b(?:about|around|on|along)?\s*(?:the\s+)?([xyz])[- ]?axis\b"

# An outcome rather than an axis; see the module docstring.
_OVER = r"\bupside[- ]down\b|\bflip\b|\bflipped\b|\bturn it over\b|\bon its head\b"

_FRACTIONS = [
    (_OVER, 180.0),
    (r"\ba quarter (?:turn|of a turn|way|way round|way around)\b", 90.0),
    (r"\bquarter turn\b", 90.0),
    (r"\b(?:a )?half(?: a)? turn\b", 180.0),
    (r"\bhalfway (?:round|around)\b", 180.0),
    (r"\b(?:three quarters|3 quarters)(?: of)?(?: a)? turn\b", 270.0),
    (r"\b(?:all the way|the whole way|full circle)(?: turn| round| around)?\b", 360.0),
    (r"\bright (?:side|way) up\b", 0.0),
]

_WORD_NUMBERS = {
    "one hundred and eighty": 180, "a hundred and eighty": 180,
    "one thirty five": 135, "forty-five": 45, "forty five": 45,
    "one eighty": 180, "two seventy": 270, "three sixty": 360, "one twenty": 120,
    "ninety": 90, "thirty": 30, "sixty": 60, "fifteen": 15, "twenty": 20,
    "forty": 40, "fifty": 50, "seventy": 70, "eighty": 80, "ten": 10, "five": 5,
}

DEFAULT_DEGREES = 90.0


def parse_axis(text: str) -> str:
    """Which way he means to turn it. Defaults to the vertical axis."""
    t = (text or "").lower()
    if re.search(_OVER, t):
        return "x"
    m = re.search(_NAMED_AXIS, t)
    if m:
        return m.group(1)
    for pat, ax in _AXIS_WORDS:
        if re.search(pat, t):
            return ax
    return "z"


def parse_degrees(text: str) -> float:
    """How far, in degrees. Signed: 'back'/'left'/'anticlockwise' turn the other way."""
    t = (text or "").lower()

    deg: float | None = None
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*(?:degrees?|deg\b|°)", t)
    if m:
        deg = float(m.group(1))
    if deg is None:
        for pat, v in _FRACTIONS:
            if re.search(pat, t):
                deg = v
                break
    if deg is None:
        # Longest phrase first, so "one eighty" is not read as "one" then "eighty".
        for word, v in _WORD_NUMBERS.items():
            if re.search(rf"\b{re.escape(word)}\b", t):
                deg = float(v)
                break
    if deg is None:
        m = re.search(r"(-?\d+(?:\.\d+)?)", t)
        deg = float(m.group(1)) if m else DEFAULT_DEGREES

    if re.search(r"\b(?:back|backwards|anticlockwise|counter[- ]?clockwise|the other way|left)\b", t):
        deg = -abs(deg)
    return deg


def parse_scale(text: str) -> float | None:
    """A view zoom factor, or None if he did not ask for one.

    Deliberately a VIEW factor. Scaling the MODEL would change the size of a part
    he is about to print, and "make it bigger", said at a screen, means the
    screen. Changing the real thing is `edit_part`, which rewrites the source and
    re-renders, and says so.
    """
    t = (text or "").lower()
    m = re.search(r"(?:by\s+)?(\d+(?:\.\d+)?)\s*(?:x|times)\b", t)
    if m:
        return float(m.group(1))
    # No trailing \b after the percent sign: "%" is not a word character, so
    # `(?:%|percent)\b` never matched a string ending in "200%" — the commonest
    # way to say it.
    m = re.search(r"\b(?:to\s+)?(\d{2,3})\s*(?:%|percent\b)", t)
    if m:
        return float(m.group(1)) / 100.0
    if re.search(r"\b(?:zoom in|closer|bigger|larger|enlarge|magnify|blow it up)\b", t):
        return 1.5
    if re.search(r"\b(?:zoom out|further away|smaller|shrink|back off|wider)\b", t):
        return 1 / 1.5
    return None


def parse_layer(text: str) -> dict | None:
    """Which layer of the sliced toolpath he means, or None if not about layers.

    Every slicer has a layer slider and ours drew the whole print at once, which
    is why a cube looked like a solid block. Scrubbing is how anyone actually
    reads a toolpath: you go up through it and watch the part appear.

    Distinguished from "show me the layers", which turns the preview ON. A NUMBER
    or a position word means he wants a particular one.
    """
    t = (text or "").lower()
    if not re.search(r"\blayer", t):
        return None
    if re.search(r"\b(?:top|last|highest|all of them|the lot|everything)\b", t):
        return {"layer": -1}
    if re.search(r"\b(?:first|bottom|lowest|start)\b", t):
        return {"layer": 0}
    if re.search(r"\b(?:next|up|higher|forward|another)\b", t):
        return {"delta": 1}
    if re.search(r"\b(?:previous|back|down|lower|before)\b", t):
        return {"delta": -1}
    m = re.search(r"\blayer\s*(?:number\s*)?(\d+)", t) or \
        re.search(r"(\d+)(?:st|nd|rd|th)?\s+layer", t)
    if m:
        return {"layer": int(m.group(1))}
    return None


def parse_action(text: str) -> str | None:
    """Which control he means, or None if the sentence names none of them.

    None rather than a guess. The first version fell through to "rotate" for
    anything it did not recognise, so "reset it" and "show me the layers" both
    spun the model — a wrong action is worse than an admitted miss, because he
    watches it do the wrong thing and has to undo it.

    Order matters. "Put it back the way it was" contains "way", and "show me the
    layers of the cut" contains both; the more specific intent is tested first.
    """
    t = (text or "").lower()
    if re.search(r"\b(?:reset|start over|back to normal|as it was|the way it was|"
                 r"undo (?:that|it)|straighten it (?:up|out)|put it back)\b", t):
        return "reset"
    if re.search(r"\b(?:solid|the model again|hide the layers|back to the model|"
                 r"stop the layers)\b", t):
        return "solid"
    # A PARTICULAR layer is a scrub; "the layers" is a switch. Checked first, or
    # "show me layer fifty" would merely turn the preview on again.
    if parse_layer(t):
        return "layer"
    if re.search(r"\b(?:layers?|toolpath|tool path|how it'?s printed|"
                 r"the print path|slicing preview)\b", t):
        return "layers"
    if re.search(r"\b(?:explode|exploded|apart|separate the parts|pull it apart|"
                 r"blow it apart)\b", t):
        return "explode"
    # THE REAL COLOURS, and back to the hologram. Both directions, because the
    # cyan is the look he asked to keep and this only borrows the stage.
    if re.search(r"\b(?:in colou?r|real colou?rs?|true colou?rs?|actual colou?rs?|"
                 r"paint(?: it)?|coloured|colored|what (?:it|that|this)(?: really)? looks? like|what does it (?:really )?look like|"
                 r"as it (?:would )?looks?)\b", t):
        return "colour"
    if re.search(r"\b(?:hologram|holographic|wireframe|back to (?:the )?(?:blue|cyan|"
                 r"hologram)|no colou?r|without colou?r)\b", t):
        return "hologram"
    if re.search(r"\b(?:fit it|fit to (?:the )?(?:screen|view|frame)|frame it|"
                 r"centre it|center it|show all of it)\b", t):
        return "fit"
    if parse_section(t):
        return "section"
    if re.search(_OVER, t):
        return "flip"
    if parse_scale(t) is not None:
        return "scale"
    if re.search(r"\b(?:rotate|spin|turn|tip|tilt|roll|lean|pitch|swing|yaw|bank)\b", t):
        return "rotate"
    return None


def parse_section(text: str) -> dict | None:
    """A cut plane: which way it faces and how far along it sits.

    Returns None when nothing in the sentence asks for a cut, so the caller can
    tell "cut it in half" from "rotate it".
    """
    t = (text or "").lower()
    if not re.search(r"\b(?:section|cross[- ]?section|cut|halve|split|open it up|inside)\b", t):
        return None
    axis = "z"
    if re.search(r"\b(?:side to side|across|left to right)\b", t):
        axis = "x"
    elif re.search(r"\b(?:front to back|lengthways|lengthwise)\b", t):
        axis = "y"
    at = 0.5
    m = re.search(r"(\d{1,3})\s*(?:%|percent\b)", t)
    if m:
        at = max(0.0, min(1.0, float(m.group(1)) / 100.0))
    elif re.search(r"\bthree quarters\b", t):
        at = 0.75
    elif re.search(r"\ba quarter\b", t):
        at = 0.25
    return {"axis": axis, "at": at}
