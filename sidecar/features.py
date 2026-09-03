"""Naming the parts of a traced design, so he can change one of them.

His words: *"if I say 'hey, make his eyes smaller' — if we're still referring to
the Spider-Man baseball — it refactors and makes the eye smaller. If I say 'make
the lines on the mask bigger', it does that."*

WHY NOT A FACE MODEL. That was the obvious answer and it is the wrong one here.
Face landmarkers and face-parsing networks work on photographs of faces; a
Spider-Man mask is a stylised drawing with two flat shapes where eyes go, and
the detectors that would name them are the detectors that will not fire on it at
all. The research bears this out — every published system that names parts of a
vector design assigns the names AT GENERATION TIME, and the one benchmark that
asks a model to find a feature in raw path data scores about 2%.

WHAT DOES WORK IS THE SHAPE OF THE THING. A traced design already arrives as
outlines and holes with real coordinates, and the features he names are
identifiable from their geometry alone:

    the eyes       two shapes of similar size, both above the middle, mirrored
                   about the design's own centre line — which is what makes them
                   a PAIR rather than two unrelated blobs
    the lines      long and thin: the webbing on a mask, the veins on a leaf
    the outline    the largest shape, the one everything else sits inside

No model, no download, nothing to be offline for, and it works on a drawing —
which is the case that matters, because a photograph of a face is not what he
asks to have printed.

LEFT AND RIGHT ARE THE VIEWER'S. "His left eye" and "the left eye" mean opposite
sides, and there is no way to be right for both — so the names are the ones he
can see, and `describe` says so out loud when it lists them.
"""
from __future__ import annotations

import logging

log = logging.getLogger("jarvis.features")

# Two shapes are a pair when their areas are within this of each other.
PAIR_AREA_RATIO = 0.55
# ...and their centres sit at about the same height.
PAIR_Y_TOLERANCE = 0.18
# ...and they are mirrored about the middle to within this much of the width.
PAIR_MIRROR_TOLERANCE = 0.18

# Longer than this against its width and a shape is a line, not a blob.
LINE_ASPECT = 3.2

# Above this fraction of the height counts as the upper half of the design.
UPPER = 0.45


def _bbox(points):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def _area(points) -> float:
    """The shoelace area. Absolute, because winding varies with the tracer."""
    n = len(points)
    if n < 3:
        return 0.0
    s = 0.0
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def _centre(points):
    x0, y0, x1, y1 = _bbox(points)
    return ((x0 + x1) / 2.0, (y0 + y1) / 2.0)


def _pieces(shapes: list[dict]) -> list[dict]:
    """Every outline and every hole, flattened, with what it is and where."""
    out = []
    for si, sh in enumerate(shapes or []):
        for kind, pts, hi in ([("outline", sh.get("outline") or [], None)]
                              + [("hole", h, hi)
                                 for hi, h in enumerate(sh.get("holes") or [])]):
            if len(pts) < 3:
                continue
            x0, y0, x1, y1 = _bbox(pts)
            w, h = max(x1 - x0, 1e-6), max(y1 - y0, 1e-6)
            out.append({"shape": si, "hole": hi, "kind": kind, "points": pts,
                        "area": _area(pts), "centre": _centre(pts),
                        "w": w, "h": h, "aspect": max(w / h, h / w)})
    return out


def label(shapes: list[dict]) -> list[dict]:
    """Name what can be named. Everything else keeps a positional name.

    Returns the pieces with a `name` on each, in the order they were traced, so
    a caller can map a name back to a contour and change it.
    """
    pieces = _pieces(shapes)
    if not pieces:
        return []

    xs = [p["centre"][0] for p in pieces]
    ys = [p["centre"][1] for p in pieces]
    lo_x, hi_x = min(xs), max(xs)
    lo_y, hi_y = min(ys), max(ys)
    span_x = max(hi_x - lo_x, 1e-6)
    span_y = max(hi_y - lo_y, 1e-6)
    mid_x = (lo_x + hi_x) / 2.0

    biggest = max(pieces, key=lambda p: p["area"])
    biggest["name"] = "outline"
    named = {id(biggest)}

    # THE PAIR. Two shapes of similar size, at the same height, mirrored about
    # the middle — that is what makes them eyes rather than two blobs, and it
    # holds for a stylised mask where no face detector would fire at all.
    rest = [p for p in pieces if id(p) not in named]
    best = None
    for i, a in enumerate(rest):
        for b in rest[i + 1:]:
            if min(a["area"], b["area"]) <= 0:
                continue
            ratio = min(a["area"], b["area"]) / max(a["area"], b["area"])
            if ratio < PAIR_AREA_RATIO:
                continue
            if abs(a["centre"][1] - b["centre"][1]) / span_y > PAIR_Y_TOLERANCE:
                continue
            off = abs((a["centre"][0] - mid_x) + (b["centre"][0] - mid_x))
            if off / span_x > PAIR_MIRROR_TOLERANCE:
                continue
            if (a["centre"][1] - lo_y) / span_y < UPPER:
                continue          # a pair low down is feet, not eyes
            score = ratio - off / span_x
            if best is None or score > best[0]:
                best = (score, a, b)
    if best:
        _, a, b = best
        left, right = (a, b) if a["centre"][0] <= b["centre"][0] else (b, a)
        left["name"], right["name"] = "left eye", "right eye"
        named.update({id(left), id(right)})

    for p in pieces:
        if id(p) in named:
            continue
        if p["aspect"] >= LINE_ASPECT:
            p["name"] = "lines"
            continue
        high = (p["centre"][1] - lo_y) / span_y
        side = ("left" if p["centre"][0] < mid_x - span_x * 0.12
                else "right" if p["centre"][0] > mid_x + span_x * 0.12
                else "middle")
        p["name"] = f"{'upper' if high >= UPPER else 'lower'} {side}"
    return pieces


# What he might call each thing. Deliberately small: a name nobody says is a
# name that only ever matches by accident.
_WORDS = {
    "eyes": ("left eye", "right eye"),
    "eye": ("left eye", "right eye"),
    "left eye": ("left eye",),
    "right eye": ("right eye",),
    "lines": ("lines",),
    "line": ("lines",),
    "web": ("lines",),
    "webbing": ("lines",),
    "webs": ("lines",),
    "outline": ("outline",),
    "border": ("outline",),
    "edge": ("outline",),
    "body": ("outline",),
    "shape": ("outline",),
}


def find(pieces: list[dict], said: str) -> list[dict]:
    """The pieces he means, from what he called them."""
    want = (said or "").strip().lower()
    if not want:
        return []
    names: tuple = ()
    for word, mapped in sorted(_WORDS.items(), key=lambda kv: -len(kv[0])):
        if word in want:
            names = mapped
            break
    if not names:
        # He may have used a positional name back to us.
        names = tuple({p["name"] for p in pieces if p["name"] in want})
    return [p for p in pieces if p.get("name") in names]


def scaled(shapes: list[dict], targets: list[dict], factor: float) -> list[dict]:
    """The design again, with those pieces resized about their own centres.

    About their OWN centres, not the design's: making the eyes smaller should
    shrink each eye where it sits, not slide them both toward the middle.
    """
    if not targets or factor <= 0:
        return shapes
    want = {(t["shape"], t["hole"]) for t in targets}
    out = []
    for si, sh in enumerate(shapes or []):
        outline = sh.get("outline") or []
        if (si, None) in want:
            outline = _resize(outline, factor)
        holes = []
        for hi, hole in enumerate(sh.get("holes") or []):
            holes.append(_resize(hole, factor) if (si, hi) in want else hole)
        out.append({"outline": outline, "holes": holes})
    return out


def _resize(points, factor: float):
    cx, cy = _centre(points)
    return [[cx + (x - cx) * factor, cy + (y - cy) * factor] for x, y in points]


def describe(pieces: list[dict]) -> str:
    """What this design is made of, said once, for "what can I change?"."""
    if not pieces:
        return ""
    seen: list[str] = []
    for p in pieces:
        n = p.get("name", "")
        if n and n not in seen:
            seen.append(n)
    if not seen:
        return ""
    # Left and right are the viewer's, and saying so costs four words and saves
    # an argument about which eye got smaller.
    note = (" — left and right as you're looking at it"
            if any("eye" in s for s in seen) else "")
    if len(seen) == 1:
        return seen[0] + note
    return ", ".join(seen[:-1]) + " and " + seen[-1] + note
