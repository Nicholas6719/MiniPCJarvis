"""Look first, say what was found, then ask. For anything he names by name.

His words: *"if I say a PS5 or a PlayStation controller, he should look, search
the internet, research his LLM at the same time, and say — all right, I found
this image of a PS5, I couldn't find any dimensions, but do you want me to use
my best judgment and create the PS5 based off of my judgment? I'd say yes, and
we would figure it out. If it finds a 3D model of the PS5, it says I found this,
these are the dimensions, you want me to render it?"*

Two different answers to the same request, and which one he gets depends on what
is actually out there — so the looking has to happen BEFORE the question, not
after the decision.

    a model exists          "Somebody's already made one — 340 by 260 mm,
                             from <repo>. Shall I fetch it?"
    only a picture          "I found a picture but no dimensions anywhere.
                             Shall I use my best judgment on the proportions?"
    neither                 "I can't find anything on it, sir. Want me to
                             build one from what I know?"

The third is still an offer. "I found nothing" as a final answer is the thing he
told me to stop doing.

ALL THREE LOOKUPS AT ONCE. A model search, a dimensions search and an image
search are three independent round trips of a few seconds each; run one after
another they are the difference between a question that arrives while he is
still thinking about it and one that arrives after he has moved on. Nothing here
shares state, so there is no reason for them to wait for each other.

AND NONE OF THEM CAN FAIL THE REQUEST. Every one is wrapped: no dimensions is a
useful answer, no picture is a useful answer, and the web being down means "I
couldn't look" rather than a request that dies.
"""
from __future__ import annotations

import asyncio
import logging
import re

log = logging.getLogger("jarvis.scout")

# How long to spend looking before asking anyway. He is waiting on this.
TIMEOUT_S = 30

# A dimension in a sentence: "390 x 104 x 260 mm", "15.4 inches wide".
_DIMS = re.compile(
    r"\b\d{1,4}(?:\.\d+)?\s*(?:x|×|by)\s*\d{1,4}(?:\.\d+)?"
    r"(?:\s*(?:x|×|by)\s*\d{1,4}(?:\.\d+)?)?\s*"
    r"(?:mm|millimet(?:er|re)s?|cm|centimet(?:er|re)s?|inches|inch|in|\")",
    re.I)

# ...and ONE measurement, when it is named. "73.5 mm in diameter" is the whole
# specification of a baseball, and the three-number pattern above misses it
# entirely. Only counted when a word says what is being measured: a bare number
# with a unit is as likely to be a price or a weight.
_ONE_DIM = re.compile(
    r"\b(?:diameter|radius|width|height|depth|length|across|tall|wide|long|high|deep|thick)\b[^.]{0,24}?(\d{1,4}(?:\.\d+)?\s*(?:mm|millimet(?:er|re)s?|cm|centimet(?:er|re)s?|inches|inch|in))|(\d{1,4}(?:\.\d+)?\s*(?:mm|millimet(?:er|re)s?|cm|centimet(?:er|re)s?|inches|inch|in))\s*(?:in\s+)?(?:diameter|radius|width|height|depth|length|across|tall|wide|long|high|deep|thick)\b",
    re.I)


async def _find_model(description: str) -> dict:
    """Has somebody already published one?"""
    try:
        import create3d
        import model_find as MF
        found = await MF.find(description)
        for c in found.get("candidates") or []:
            if "github.com" not in c.get("host", ""):
                continue
            meshes = await MF.github_meshes(c["url"], want=description)
            pick, _others = create3d._pick_mesh(meshes, description)
            if pick:
                return {"repo": pick["repo"], "file": pick["path"].split("/")[-1],
                        "bytes": pick.get("bytes", 0), "url": pick["url"],
                        "page": c.get("url", "")}
        # Nothing fetchable, but the pages are worth reporting.
        pages = [c for c in (found.get("candidates") or [])
                 if "github.com" not in c.get("host", "")]
        return {"pages": [p["host"] for p in pages[:3]]} if pages else {}
    except Exception:
        log.debug("model search failed for %r", description, exc_info=True)
        return {}


async def _find_dimensions(description: str) -> dict:
    """Does the web say how big it is?"""
    try:
        from tools.builtin import web_search
        got = await web_search(f"{description} dimensions size mm", count=6)
        for r in (got.get("results") or []):
            text = f"{r.get('title','')} {r.get('snippet','')}"
            m = _DIMS.search(text)
            if m:
                return {"said": m.group(0), "where": r.get("url", ""),
                        "full": True}
        # Nothing with three numbers in it. One NAMED measurement is still a
        # specification for something round.
        for r in (got.get("results") or []):
            text = f"{r.get('title','')} {r.get('snippet','')}"
            m = _ONE_DIM.search(text)
            if m:
                return {"said": (m.group(1) or m.group(2) or m.group(0)).strip(),
                        "where": r.get("url", ""), "full": False}
        return {}
    except Exception:
        log.debug("dimension search failed for %r", description, exc_info=True)
        return {}


async def _find_picture(description: str) -> dict:
    """Is there a clean picture to work from?"""
    try:
        import create3d
        p = await create3d.reference_image(description)
        return {"path": p} if p else {}
    except Exception:
        log.debug("reference search failed for %r", description, exc_info=True)
        return {}


async def look(description: str) -> dict:
    """Everything findable about a named thing, gathered at once.

    Returns {model, dimensions, picture} — each either populated or empty. An
    empty one is a real answer and is what the question is built from.
    """
    desc = (description or "").strip()
    if not desc:
        return {}
    try:
        model, dims, pic = await asyncio.wait_for(
            asyncio.gather(_find_model(desc), _find_dimensions(desc),
                           _find_picture(desc)),
            timeout=TIMEOUT_S)
    except asyncio.TimeoutError:
        log.info("scouting %r took too long; asking anyway", desc[:40])
        return {"timed_out": True}
    except Exception:
        log.warning("could not scout %r", desc[:40], exc_info=True)
        return {}
    return {"model": model, "dimensions": dims, "picture": pic}


def question(description: str, found: dict) -> dict:
    """The sentence to put to him, from what was actually found.

    Three shapes, and the third is still an offer: "I found nothing" as a final
    answer is the thing he told me to stop saying.
    """
    model = (found or {}).get("model") or {}
    dims = (found or {}).get("dimensions") or {}
    pic = (found or {}).get("picture") or {}

    if model.get("repo"):
        size = f", {model['bytes'] // 1024} KB" if model.get("bytes") else ""
        q = (f"Somebody's already made one, sir — {model['file']}{size}, from "
             f"{model['repo']}. Shall I fetch it?")
        return {"question": q, "route": "fetch", "found": "model"}

    if dims.get("said"):
        q = (f"I've got the dimensions, sir — {dims['said']}"
             f"{' and a picture to work from' if pic.get('path') else ''}. "
             f"Shall I build it?")
        return {"question": q, "route": "build", "found": "dimensions"}

    if pic.get("path"):
        q = ("I found a picture but no dimensions anywhere, sir. Shall I use my "
             "best judgment on the proportions?")
        return {"question": q, "route": "build", "found": "picture"}

    q = (f"I can't find anything on {description}, sir. Shall I build one from "
         f"what I know of it?")
    return {"question": q, "route": "build", "found": "nothing"}
