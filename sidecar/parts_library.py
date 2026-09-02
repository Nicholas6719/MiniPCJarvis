"""Common parts, written directly, without waking the model.

WHY THIS EXISTS. Tier 1 measured 27.5 seconds on this machine, and almost none of
that is OpenSCAD — it is llama-server composing the source. But most of what
anyone asks a 3D printer for is parametric: a cube, a plate, a spacer, a washer,
a tube, a disc, usually with a hole through it. Those need no language model at
all. They need a template and the numbers he said.

The result is not merely faster. It is CORRECT. Asked for "a hex spacer 12 mm
tall" the local model produced OpenSCAD for something 0.4 mm wide; a template
cannot do that, because the dimensions come from his own sentence and the shape
is written once, by hand, and gated.

THE RULE THAT KEEPS IT HONEST: match only when certain, and fall through to the
model otherwise. A template that fires on a request it does not really understand
produces a confident, exact, wrong part — which is far worse than waiting half a
minute for the model to write something right. So every recognizer demands the
dimensions it needs, and anything with extra descriptive weight it cannot account
for ("a bracket shaped like a swan") is left alone.
"""
from __future__ import annotations

import re

# OpenSCAD's default curve resolution gives a 5 mm hole about a dozen segments,
# and a bolt does not fit a hexagon. Same value the model is told to use.
HEADER = "$fn = 48;\n"

_NUM = r"(\d+(?:\.\d+)?)"
_MM = r"\s*(?:mm|millimet(?:er|re)s?)?"


def _n(m, i=1) -> float:
    return float(m.group(i))


def _dims(text: str) -> list[float]:
    """Every millimetre figure in the sentence, in the order he said them."""
    return [float(x) for x in re.findall(rf"{_NUM}\s*(?:mm|millimet(?:er|re)s?)\b", text)]


def _named(text: str, *words: str) -> float | None:
    """A dimension attached to a word: "12 mm tall", "tall 12", "height 12"."""
    alts = "|".join(words)
    for pat in (rf"{_NUM}{_MM}\s+(?:{alts})\b",
                rf"(?:{alts})\s+(?:of\s+)?{_NUM}{_MM}"):
        m = re.search(pat, text)
        if m:
            return _n(m)
    return None


def _hole(text: str) -> float | None:
    """A hole he asked for, by diameter."""
    m = re.search(rf"{_NUM}{_MM}\s*(?:diameter\s*)?(?:hole|bore|through hole)", text)
    if m:
        return _n(m)
    m = re.search(rf"(?:hole|bore)\s*(?:of|is)?\s*{_NUM}{_MM}", text)
    return _n(m) if m else None


def _sides(text: str) -> int:
    """Hex means six. Everything else is round."""
    if re.search(r"\bhex(?:agon(?:al)?)?\b", text):
        return 6
    if re.search(r"\bsquare\b", text):
        return 4
    return 48


def _centred_hole(d: float, h: float) -> str:
    # 0.2 mm proud at each end: a hole cut exactly flush with the surface leaves
    # coincident faces, which is a classic way to hand a slicer a non-manifold
    # solid that renders fine and slices wrong.
    return (f"    translate([0, 0, -0.1])\n"
            f"      cylinder(d = {d:g}, h = {h + 0.2:g});\n")


# --------------------------------------------------------------- recognizers
def _cube(t: str) -> str | None:
    m = re.search(rf"{_NUM}{_MM}\s*(?:cube|box|block)\b", t) or \
        re.search(rf"\b(?:cube|box|block)\s*(?:of\s*)?{_NUM}{_MM}\s*(?:on (?:a|each|every) side)?", t)
    if m and len(_dims(t)) <= 1:
        s = _n(m)
        return f"{HEADER}cube([{s:g}, {s:g}, {s:g}]);\n"
    return None


def _boxy(t: str) -> str | None:
    """"a plate 40 by 30 by 6 mm", optionally with a hole through it."""
    if not re.search(r"\b(?:plate|box|block|slab|bar|panel|base|pad)\b", t):
        return None
    m = re.search(rf"{_NUM}{_MM}\s*(?:by|x|\*)\s*{_NUM}{_MM}\s*(?:by|x|\*)\s*{_NUM}{_MM}", t)
    if not m:
        return None
    x, y, z = _n(m, 1), _n(m, 2), _n(m, 3)
    hole = _hole(t)
    if hole is None:
        return f"{HEADER}cube([{x:g}, {y:g}, {z:g}]);\n"
    return (f"{HEADER}difference() {{\n"
            f"  cube([{x:g}, {y:g}, {z:g}]);\n"
            f"  translate([{x / 2:g}, {y / 2:g}, 0])\n"
            f"{_centred_hole(hole, z)}"
            f"}}\n")


def _cylinder(t: str) -> str | None:
    if not re.search(r"\b(?:cylinder|rod|disc|disk|puck|peg|dowel|post)\b", t):
        return None
    dia = _named(t, "diameter", "wide", "across", "thick") or None
    hi = _named(t, "tall", "high", "height", "long", "length", "deep")
    nums = _dims(t)
    if dia is None and len(nums) >= 2:
        dia, hi = nums[0], (hi if hi is not None else nums[1])
    if dia is None or hi is None:
        return None
    hole = _hole(t)
    body = f"cylinder(d = {dia:g}, h = {hi:g}, $fn = {_sides(t)});\n"
    if hole is None:
        return HEADER + body
    return (f"{HEADER}difference() {{\n  {body}{_centred_hole(hole, hi)}}}\n")


def _sphere(t: str) -> str | None:
    if not re.search(r"\b(?:sphere|ball|orb)\b", t):
        return None
    dia = _named(t, "diameter", "wide", "across")
    nums = _dims(t)
    if dia is None and len(nums) == 1:
        dia = nums[0]
    return f"{HEADER}sphere(d = {dia:g});\n" if dia else None


def _spacer(t: str) -> str | None:
    """A spacer or standoff: a post with a bore through it."""
    if not re.search(r"\b(?:spacer|standoff|stand-off|bushing|bush|sleeve|collar)\b", t):
        return None
    hi = _named(t, "tall", "high", "height", "long", "length")
    outer = _named(t, "outer", "od", "outside", "across", "wide", "diameter")
    bore = _hole(t) or _named(t, "inner", "id", "inside", "bore")
    nums = _dims(t)
    if hi is None and nums:
        # "a 12 mm spacer" means twelve tall: that is the dimension people give.
        m = re.search(rf"{_NUM}{_MM}\s*(?:hex\s*)?(?:spacer|standoff|bushing)", t)
        hi = _n(m) if m else None
    if hi is None:
        return None
    if outer is None:
        # A spacer with no stated width is a standard one; 6 mm across is the
        # common M3 size and is stated in the source so it is never a mystery.
        outer = 6.0
    if bore is None:
        bore = 3.2                      # clearance for an M3 screw
    return (f"{HEADER}// spacer: {outer:g} mm across, {hi:g} mm tall, "
            f"{bore:g} mm bore\n"
            f"difference() {{\n"
            f"  cylinder(d = {outer:g}, h = {hi:g}, $fn = {_sides(t)});\n"
            f"{_centred_hole(bore, hi)}"
            f"}}\n")


def _washer(t: str) -> str | None:
    if not re.search(r"\b(?:washer|ring|annulus|gasket)\b", t):
        return None
    outer = _named(t, "outer", "od", "outside")
    inner = _named(t, "inner", "id", "inside", "bore") or _hole(t)
    thick = _named(t, "thick", "thickness", "tall", "high", "deep")
    nums = _dims(t)
    if outer is None and len(nums) >= 2:
        outer, inner = nums[0], (inner if inner is not None else nums[1])
    if thick is None and len(nums) >= 3:
        thick = nums[2]
    if outer is None or inner is None:
        return None
    if thick is None:
        thick = 2.0
    return (f"{HEADER}difference() {{\n"
            f"  cylinder(d = {outer:g}, h = {thick:g});\n"
            f"{_centred_hole(inner, thick)}"
            f"}}\n")


def _tube(t: str) -> str | None:
    if not re.search(r"\b(?:tube|pipe|hollow cylinder)\b", t):
        return None
    outer = _named(t, "outer", "od", "outside", "diameter", "wide")
    inner = _named(t, "inner", "id", "inside", "bore") or _hole(t)
    hi = _named(t, "tall", "high", "height", "long", "length")
    nums = _dims(t)
    if outer is None and len(nums) >= 3:
        outer, inner, hi = nums[0], nums[1], nums[2]
    if outer is None or inner is None or hi is None:
        return None
    return (f"{HEADER}difference() {{\n"
            f"  cylinder(d = {outer:g}, h = {hi:g});\n"
            f"{_centred_hole(inner, hi)}"
            f"}}\n")


# Order matters: the more specific shape wins. A "spacer" is a cylinder, and a
# "washer" is a tube, so those are tried before the general forms.
_RECOGNIZERS = (_spacer, _washer, _tube, _cube, _boxy, _cylinder, _sphere)

# Words that mean the request carries meaning a template cannot hold. If any of
# these is present the model writes it, however many numbers are in the sentence
# — a confident, exact, WRONG part is worse than waiting for a right one.
_TOO_RICH = re.compile(
    r"\b(?:bracket|mount|holder|clip|hook|gear|thread(?:ed)?|screw|nut|bolt|"
    r"hinge|latch|handle|knob|lid|case|enclosure|tray|rack|fillet|chamfer|"
    r"rounded|curve[sd]?|slot|groove|rib|fin|text|letter|logo|shaped like|"
    r"looks like|similar to|organic|dragon|figure|statue)\b", re.I)

# MORE THAN ONE OF SOMETHING. Every template here makes exactly one body with at
# most one hole through the middle, so a request for several is a request it
# cannot honour — and honouring it wrongly is the failure that matters, because
# what comes back is confident, exact and not what he asked for.
#
# Both of these were real false matches, found by throwing realistic phrasings at
# it rather than by a test written from the code:
#   "a plate with 4 mounting holes 60 by 60 by 5 mm"  -> one centred hole
#   "a cube 20 mm and a plate 30 by 30 by 2 mm"       -> just the cube
# A DIGIT IS A DIMENSION, NOT A COUNT — which the first version of this got
# wrong and thereby declined "a 25 mm sphere" and "a plate ... with a 5 mm hole",
# because `\d+ \w+ <noun>` happily matches "25 mm sphere". The real signals are a
# PLURAL noun, a counting WORD, and a conjunction joining two parts.
_MULTIPLE = re.compile(
    r"\b(?:holes|cubes|plates|spacers|washers|tubes|cylinders|spheres|discs|"
    r"disks|rods|bores)\b"
    r"|\b(?:two|three|four|five|six|seven|eight|nine|ten|several|multiple|"
    r"a couple of|a few)\b"
    r"|\band\s+(?:a|an|the|another)\b", re.I)


def match(description: str) -> str | None:
    """OpenSCAD for a part we can write exactly, or None to ask the model.

    None is the safe answer and the common one. This exists to make the easy
    third of requests instant, not to replace the model.
    """
    t = (description or "").strip().lower()
    if not t or _TOO_RICH.search(t) or _MULTIPLE.search(t):
        return None
    for fn in _RECOGNIZERS:
        try:
            out = fn(t)
        except Exception:
            out = None
        if out:
            return out
    return None
