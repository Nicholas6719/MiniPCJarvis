"""Did he get what he asked for?

Nothing checked this before. A part was generated, measured, projected and
announced as ready, and the only thing standing between him and a wrong model
was whether some dimension happened to round to zero in the HUD. Asked for "a hex
spacer 12 mm tall" the local model produced OpenSCAD for something 0.4 mm wide;
it was twelve millimetres tall, so it passed every test there was.

The idea is borrowed from TalkCAD, which verifies generated CAD against the spec
with a tolerance and tracks which numbers the user gave against which the model
chose. Both halves matter and they are different jobs:

  * VERIFY what he stated. If he said forty millimetres, it must be forty.
  * DECLARE what we chose. A spacer with no stated width gets a 6 mm body and a
    3.2 mm M3 bore. That was written in a source comment he will never read, and
    a number nobody told him about is a number he will discover at the printer.

WHAT IT DELIBERATELY DOES NOT DO. It checks the claims it can check from a
bounding box, and stays quiet about the rest — a hole diameter is not visible in
the extents, so it is recorded as stated and never asserted. Reporting a
confident pass on something that was never examined would be worse than no check
at all, which is the same rule the wall estimate follows.
"""
from __future__ import annotations

import re

# How close is close enough. A mesh has float error and marching cubes has grid
# error; 0.15 mm or 1.5% is far tighter than anything an FDM machine resolves and
# far looser than the mistakes worth catching, which are wrong by whole
# millimetres or by an order of magnitude.
TOL_MM = 0.15
TOL_REL = 0.015

_NUM = r"(\d+(?:\.\d+)?)"
_MM = r"\s*(?:mm|millimet(?:er|re)s?)?"

# Things that are SUPPOSED to be much wider than they are thick. Without this
# list the aspect check below would call every plate and every lithophane a
# failure, which is the classic way a good check gets switched off.
_THIN_BY_DESIGN = re.compile(
    r"\b(?:plate|sheet|shim|washer|gasket|coaster|disc|disk|card|label|tag|"
    r"lithophane|litho|relief|panel|film|foil|blade|fin|membrane)\b", re.I)

# Beyond this ratio a part is a sliver rather than a shape. The hex spacer that
# started all this was 0.4 x 2 x 12 — a ratio of thirty — while a legitimately
# thin 0.6 mm plate at 40 x 30 is sixty-six, which is why the exemption above
# exists and the ratio alone is not enough.
MAX_ASPECT = 25.0


def extract(description: str) -> dict:
    """What he actually stated, as claims that can be checked or recorded."""
    t = (description or "").strip().lower()
    spec: dict = {}
    if not t:
        return spec

    m = re.search(rf"{_NUM}{_MM}\s*(?:cube|box|block)\b", t) or \
        re.search(rf"\b(?:cube|box|block)\s*(?:of\s*)?{_NUM}{_MM}\s*(?:on (?:a|each|every) side)\b", t)
    if m:
        spec["cube_mm"] = float(m.group(1))

    m = re.search(rf"{_NUM}{_MM}\s*(?:by|x|\*)\s*{_NUM}{_MM}\s*(?:by|x|\*)\s*{_NUM}{_MM}", t)
    if m:
        spec["dims_mm"] = [float(m.group(1)), float(m.group(2)), float(m.group(3))]

    for words, key in ((("tall", "high", "height", "long", "length", "deep"), "height_mm"),
                       (("diameter", "wide", "across", "dia"), "diameter_mm")):
        alts = "|".join(words)
        m = (re.search(rf"{_NUM}{_MM}\s+(?:{alts})\b", t)
             or re.search(rf"(?:{alts})\s+(?:of\s+)?{_NUM}{_MM}", t))
        if m:
            spec[key] = float(m.group(1))

    # Recorded, never asserted: a hole does not show up in the extents.
    m = re.search(rf"{_NUM}{_MM}\s*(?:diameter\s*)?(?:hole|bore)", t)
    if m:
        spec["hole_mm_unchecked"] = float(m.group(1))
    return spec


def _close(got: float, want: float) -> bool:
    return abs(got - want) <= max(TOL_MM, abs(want) * TOL_REL)


def verify(spec: dict, size_mm, description: str = "") -> dict:
    """Check a finished part's extents against what he asked for.

    Returns `checked` (the claims actually tested) and `problems` (sentences).
    An empty spec is not a pass and not a failure — it is nothing to check, and
    it says so rather than implying approval.
    """
    if not size_mm or len(size_mm) != 3:
        return {"ok": None, "checked": [], "problems": [], "why": "nothing to measure"}
    got = sorted(float(v) for v in size_mm)
    x, y, z = (float(v) for v in size_mm)
    checked: list[str] = []
    problems: list[str] = []

    if "cube_mm" in spec:
        n = spec["cube_mm"]
        checked.append(f"{n:g} mm cube")
        if not all(_close(v, n) for v in got):
            problems.append(f"you asked for a {n:g} millimetre cube and it came out "
                            f"{x:.0f} by {y:.0f} by {z:.0f}")

    if "dims_mm" in spec:
        want = sorted(spec["dims_mm"])
        checked.append(" by ".join(f"{v:g}" for v in spec["dims_mm"]) + " mm")
        # Sorted, so a model that got the dimensions right but assigned them to
        # different axes is not called wrong — that is an orientation, and he can
        # turn it.
        if not all(_close(a, b) for a, b in zip(got, want)):
            problems.append(f"you asked for "
                            f"{' by '.join(f'{v:g}' for v in spec['dims_mm'])} millimetres "
                            f"and it came out {x:.1f} by {y:.1f} by {z:.1f}")

    if "height_mm" in spec and "dims_mm" not in spec and "cube_mm" not in spec:
        n = spec["height_mm"]
        checked.append(f"{n:g} mm tall")
        # The tallest extent, not Z: a part can be modelled lying down, and that
        # is an orientation rather than a mistake.
        if not _close(got[2], n) and not _close(z, n):
            problems.append(f"you asked for {n:g} millimetres tall and the longest "
                            f"side is {got[2]:.1f}")

    if "diameter_mm" in spec and "dims_mm" not in spec and "cube_mm" not in spec:
        n = spec["diameter_mm"]
        checked.append(f"{n:g} mm across")
        if not _close(max(x, y), n) and not _close(got[1], n):
            problems.append(f"you asked for {n:g} millimetres across and it measures "
                            f"{max(x, y):.1f}")

    # SHAPE SANITY, which is what actually catches a generation that collapsed.
    # The hex spacer was 0.4 x 2 x 12: every stated dimension correct, and not a
    # spacer. Exempted for the things that are meant to be thin.
    if got[0] > 0 and not _THIN_BY_DESIGN.search(description or ""):
        ratio = got[2] / got[0]
        if ratio > MAX_ASPECT:
            problems.append(f"it came out {got[0]:.2f} millimetres at its thinnest "
                            f"against {got[2]:.0f} at its longest, which is a sliver "
                            f"rather than a part")
            checked.append("proportions")

    return {"ok": (None if not checked else not problems),
            "checked": checked, "problems": problems}


def spoken(result: dict) -> str:
    """What he hears when it did NOT come out right. Empty when it did."""
    probs = (result or {}).get("problems") or []
    if not probs:
        return ""
    if len(probs) == 1:
        return probs[0]
    return "; ".join(probs[:2])
