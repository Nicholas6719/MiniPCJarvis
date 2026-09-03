"""What colour each part actually is.

His question: *"the cyan colour JARVIS renders in is great. However if I want to
fully 3D render things they typically aren't cyan — Spider-Man's suit has white
eyes and is usually red with black spiderweb lines. Iron Man's suits are many
colours. My AirPod case is white."*

TWO MODES, BECAUSE THE FILMS HAVE TWO. The cyan wireframe is the hologram and it
is what makes the stage feel like the films; the finished suit in red and gold is
the other thing they show. So colour rides ALONGSIDE the hologram look rather
than replacing it, and "show it in colour" switches over.

WHERE THE COLOURS COME FROM, in order of how much they can be trusted:

  1. THE PICTURE ITSELF, for anything traced. The tracer builds a black-and-white
     mask to find contours and throws the pixels away — but the source image is
     still there, and the median colour inside a contour is that shape's real
     colour. White eyes, red body, black web lines, measured rather than guessed.
     This is the good one and it costs nothing.

  2. THE PART'S NAME, for anything built. "gold trim" is gold; there is no
     research to do.

  3. WHAT THE WEB SAYS, for a named thing — the same search that finds
     dimensions. Iron Man is red and gold, an AirPods case is white.

An unknown colour stays unknown. A plausible guess is how a Mark 3 ends up
silver in a picture he sends someone.

AND IT IS PRINT INFORMATION TOO. Multi-colour printing IS one part per filament,
so "which bits are black" and "which bits print separately" are the same answer —
which is why the colour lives in the part manifest and not in a texture.
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger("jarvis.colours")

# Colours anybody names, and what they are. Deliberately a short list of words
# people actually say about objects, not a paint catalogue.
NAMED = {
    "black": "#1a1a1a", "white": "#f2f2f2", "grey": "#8a8a8a",
    "gray": "#8a8a8a", "silver": "#c8ccd0", "chrome": "#dfe4e8",
    "red": "#c62828", "crimson": "#a01722", "maroon": "#6d1b1b",
    "orange": "#ef6c00", "amber": "#ffb300", "gold": "#d4af37",
    "yellow": "#fdd835", "green": "#2e7d32", "lime": "#7cb342",
    "teal": "#00897b", "blue": "#1565c0", "navy": "#12306b",
    "cyan": "#28d2ff", "purple": "#6a1b9a", "violet": "#7e57c2",
    "pink": "#ec407a", "brown": "#5d4037", "tan": "#c8a165",
    "beige": "#d8c9a8", "copper": "#b87333", "bronze": "#a97142",
    "gunmetal": "#4a5257",
}

_WORD = re.compile(r"\b(" + "|".join(sorted(NAMED, key=len, reverse=True)) + r")\b",
                   re.I)


def from_words(text: str) -> str:
    """A colour named in a sentence, or "" when none is."""
    m = _WORD.search(text or "")
    return NAMED[m.group(1).lower()] if m else ""


def _hex(bgr) -> str:
    b, g, r = (int(max(0, min(255, v))) for v in bgr[:3])
    return f"#{r:02x}{g:02x}{b:02x}"


def sample(image_path: str, shapes: list, pieces: list) -> dict:
    """The real colour of each traced piece, out of the picture it came from.

    Returns {piece name: "#rrggbb"}. The tracer works on a mask and discards the
    pixels; this goes back to the image and takes the median colour inside each
    contour. Median, not mean: a mean over a red shape with a white highlight in
    it is pink, and pink is not what he saw.

    The shapes are in the tracer's coordinates — y already flipped — so they are
    flipped back before they index the image.
    """
    out: dict = {}
    try:
        import cv2
        import numpy as np
    except Exception:
        return out
    img = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if img is None:
        return out
    h, w = img.shape[:2]

    for p in pieces or []:
        pts = p.get("points") or []
        if len(pts) < 3:
            continue
        poly = np.array([[int(round(x)), int(round(h - y))] for x, y in pts],
                        dtype=np.int32)
        mask = np.zeros((h, w), np.uint8)
        cv2.fillPoly(mask, [poly], 255)
        # A HOLE IS NOT ITS PARENT. Everything inside a hole belongs to the hole,
        # so the parent's colour must not be sampled through it — otherwise a
        # mask's body reads as the colour of its eyes.
        for q in pieces:
            if q is p or q.get("shape") != p.get("shape"):
                continue
            if p.get("hole") is None and q.get("hole") is not None:
                qp = q.get("points") or []
                if len(qp) >= 3:
                    cv2.fillPoly(mask, [np.array(
                        [[int(round(x)), int(round(h - y))] for x, y in qp],
                        dtype=np.int32)], 0)
        if int(mask.sum()) < 255 * 12:
            continue                      # too few pixels to mean anything
        px = img[mask > 0]
        if not len(px):
            continue
        out[p.get("name", "")] = _hex(np.median(px, axis=0))
    return out


async def for_object(description: str) -> str:
    """What colour a named thing is, from the web. "" when nothing says.

    The same shape of search that finds dimensions, and the same honesty: an
    unknown colour stays unknown, because a plausible guess is how a Mark 3 ends
    up silver in a picture he sends somebody.
    """
    said = from_words(description)
    if said:
        return said                       # he already told us
    try:
        from tools.builtin import web_search
        got = await web_search(f"{description} colour color", count=5)
    except Exception:
        log.debug("colour search failed for %r", description, exc_info=True)
        return ""
    for r in (got.get("results") or []):
        found = from_words(f"{r.get('title','')} {r.get('snippet','')}")
        if found:
            return found
    return ""


def label(hex_colour: str) -> str:
    """The nearest name for a colour, for saying out loud."""
    if not hex_colour or not hex_colour.startswith("#") or len(hex_colour) != 7:
        return ""
    try:
        r, g, b = (int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))
    except ValueError:
        return ""
    best, dist = "", 1e9
    for name, hx in NAMED.items():
        rr, gg, bb = (int(hx[i:i + 2], 16) for i in (1, 3, 5))
        d = (r - rr) ** 2 + (g - gg) ** 2 + (b - bb) ** 2
        if d < dist:
            best, dist = name, d
    return best
