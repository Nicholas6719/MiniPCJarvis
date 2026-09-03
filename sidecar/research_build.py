"""Look the thing up before building it.

HIS INSTRUCTION, and it is a better design than what was here:

    "I want to do the web search/image search as kind of an idea base. If I say
    'Create me Iron Man's arc reactor', 'Create me a baseball', 'a tape
    measure', or 'a Nintendo 2DS XL', I expect him to do the research he needs
    to do to make that."

WHAT WAS THERE BEFORE, and why it was wrong. A named real object with no
dimensions in the sentence went to tier 4: find ONE photograph and run a
single-image reconstruction over it. That produces a likeness of a photograph —
soft, unmeasured, unprintable, and impossible to edit afterwards. For a baseball
it produced a lump; for anything with flat faces and known sizes it was strictly
worse than the thing this project can already do well.

The right answer for a real object is the one JARVIS is best at: the model
writes OpenSCAD, which is exact, printable and editable by voice. What it was
missing was FACTS — a baseball is 73-75 mm across and a 2DS XL is not a shape
anyone can recall. So the web becomes what he called it, an idea base: a short
brief of the object's form and real dimensions, handed to the model with the
request.

The brief is deliberately SMALL and SHAPE-FIRST. A page of prose about the
history of baseball helps nobody write a solid; "a sphere 73 mm in diameter with
two curved seams" is the whole job.
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger("jarvis.research_build")

# How long the brief may be. This is going into a prompt in front of a model
# that has to write code, and a long brief buries the request.
MAX_BRIEF_CHARS = 700

# A dimension somewhere in the text, so a brief that found no measurements can
# say so rather than implying it knows.
_HAS_MM = re.compile(r"\b\d+(?:\.\d+)?\s*(?:mm|millimet|cm|centimet|in\b|inch)", re.I)


async def brief_for(description: str) -> dict:
    """What the thing looks like and how big it is, from the web.

    Returns {"brief": str, "sources": [...], "measured": bool}. An empty brief is
    a real answer — the model then works from what it already knows, which is
    what it did before this existed, rather than being handed an invention.
    """
    desc = (description or "").strip()
    if not desc:
        return {"brief": "", "sources": [], "measured": False}

    try:
        from tools.web_tools import research
        found = await research(f"{desc} dimensions size shape description", 3)
    except Exception:
        log.debug("research for %r failed", desc, exc_info=True)
        return {"brief": "", "sources": [], "measured": False}
    if found.get("error"):
        return {"brief": "", "sources": [], "measured": False}

    sources = found.get("sources") or []
    extracts = "\n\n".join((s.get("extract") or "")[:1200] for s in sources)[:6000]
    if not extracts.strip():
        return {"brief": "", "sources": [], "measured": False}

    prompt = (
        "Below are web extracts about an object somebody wants to 3D print.\n"
        "Write a SHORT build brief for whoever has to model it: the primary "
        "shapes it is made of, how they are arranged, and its real dimensions "
        "in MILLIMETRES. Numbers matter more than adjectives.\n"
        "Rules: at most six lines. No history, no brand story, no opinions. "
        "If a dimension is not in the extracts, say 'unknown' rather than "
        "guessing — a made-up size is worse than none.\n\n"
        f"OBJECT: {desc}\n\nEXTRACTS:\n{extracts}\n\nBRIEF:")
    try:
        from llm.provider import local_llm
        out = ""
        # Room to think: this is the same reasoning model that spent 700 tokens
        # on analysis and returned nothing when it was given a tight budget.
        async for ch in local_llm.stream([{"role": "user", "content": prompt}],
                                         max_tokens=1200,
                                         sampling={"temperature": 0.1}):
            out += ch.text
            if ch.done:
                break
    except Exception:
        log.debug("could not summarise the brief", exc_info=True)
        return {"brief": "", "sources": [], "measured": False}

    brief = (out or "").strip()[:MAX_BRIEF_CHARS]
    return {"brief": brief,
            "sources": [s.get("url", "") for s in sources if s.get("url")],
            "measured": bool(_HAS_MM.search(brief))}
