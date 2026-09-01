"""A reminder, said the way JARVIS would say it.

Nicholas set a standing 9 p.m. reminder to wear his retainers. What he got back,
every night, was `A reminder: wear my retainers` - his own words read back at
him. His note on 2026-09-01: *"he should say something different or similar
almost every night at nine but like with JARVIS's personality... sir, it's about
time to put in your retainers, or I believe you have to wear your retainers now,
sir."*

So the reminder goes through the LLM and comes back in his voice, and it should
not be the same sentence twice in a row.

Three rules this file will not break, all of them learned the hard way:

  * It NEVER raises. A reminder that fails to be phrased is still a reminder;
    the caller gets plain, decent English back instead of an exception.
  * It NEVER blocks for long. The model is on the same machine as everything
    else, and a reminder that arrives four minutes late is a broken reminder.
  * It NEVER invents an errand. The model rephrases what he asked for and adds
    nothing - no new tasks, no advice, no "and don't forget to floss".
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re

from config import APP_DIR

log = logging.getLogger("jarvis.reminder_voice")

BUDGET_S = 8.0          # a late reminder is a broken reminder
MAX_TOKENS = 60
KEEP_RECENT = 8         # how many past phrasings to steer away from
_STATE = APP_DIR / "reminder_phrasings.json"

PROMPT = """You are JARVIS, the user's AI assistant: calm, precise, courteous, quietly witty. You are speaking aloud.

He asked you, earlier, to remind him to do this: "{text}"

It is now time. Say the reminder in your own words, in your own voice.

Rules:
- ONE short sentence. Spoken aloud, so no markdown, no lists, no emoji.
- Address him as "sir", either at the start or at the end, never both.
- Do NOT quote his phrasing back at him verbatim. Put it in your own words.
- Remind him of exactly that one thing. Do not add advice, do not invent extra
  tasks, do not ask a question.
- Calm and courteous. A little dry is welcome. Never nagging, never cute.
{avoid}
Reply with the sentence and nothing else."""

# Used when the model is unavailable, too slow, or says something unusable.
# Still his voice, just not a fresh thought - which is much better than
# reciting his own words back at him.
FALLBACKS = (
    "Sir, it's time to {t}.",
    "A reminder, sir: time to {t}.",
    "I believe you're meant to {t} now, sir.",
    "Time to {t}, sir.",
    "That's your reminder to {t}, sir.",
)


def _load() -> dict:
    try:
        return json.loads(_STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(state: dict) -> None:
    try:
        _STATE.write_text(json.dumps(state)[:200_000], encoding="utf-8")
    except Exception:
        log.debug("could not persist reminder phrasings", exc_info=True)


def _as_errand(text: str) -> str:
    """His reminder text as a verb phrase that reads correctly after "time to".

    He says "wear my retainers", so "time to wear my retainers" is already
    right. He also says "retainers" on its own, and "time to retainers" is not.
    """
    t = re.sub(r"\s+", " ", str(text or "")).strip().rstrip(".!?")
    t = re.sub(r"^(?:to|please|remember to|remind me to|don'?t forget to)\s+", "", t, flags=re.I)
    if not t:
        return ""
    # A bare noun ("retainers") needs a verb in front of it, or the fallback
    # sentences read as "time to retainers".
    if len(t.split()) == 1:
        return f"see to your {t}"
    return t


def _tidy(said: str, errand: str) -> str:
    """Trim the model's scaffolding. Empty string means 'unusable'."""
    s = re.sub(r"\s+", " ", str(said or "")).strip()
    s = s.strip('"').strip("'").strip()
    s = re.sub(r"^(?:reminder|jarvis|answer|response)\s*:\s*", "", s, flags=re.I)
    s = re.sub(r"[*_`#]+", "", s)                     # no markdown, it is spoken
    if not s:
        return ""
    # One sentence. A model that runs on gets cut rather than rambling at him.
    parts = re.split(r"(?<=[.!?])\s+", s)
    s = parts[0].strip()
    if len(s) > 160 or len(s) < 8:
        return ""
    # It must still be about the thing he asked for. If the model wandered off
    # and produced a pleasantry, that is worse than the plain sentence.
    words = [w for w in re.findall(r"[a-z]{4,}", errand.lower())]
    if words and not any(w in s.lower() for w in words):
        log.info("reminder phrasing lost the subject (%r); using the fallback", s[:60])
        return ""
    if not s.endswith((".", "!", "?")):
        s += "."
    return s


async def _think(errand: str, avoid: list[str]) -> str:
    from llm.provider import local_llm
    avoid_block = ""
    if avoid:
        lines = "\n".join(f'- "{a}"' for a in avoid[-KEEP_RECENT:])
        avoid_block = ("- You have recently said these. Say something different "
                       f"this time:\n{lines}\n")
    out = ""
    async for ch in local_llm.stream(
            [{"role": "user", "content": PROMPT.format(text=errand, avoid=avoid_block)}],
            max_tokens=MAX_TOKENS,
            # Warm enough to be a different sentence most nights. The news
            # summariser runs at 0.1 because it must not embellish; this one is
            # allowed to have a bit of life in it.
            sampling={"temperature": 0.9, "top_p": 0.95}):
        out += ch.text
        if ch.done:
            break
    return out.strip()


async def phrase(text: str) -> str:
    """`text` is the reminder as he set it. Returns a sentence to say aloud."""
    errand = _as_errand(text)
    if not errand:
        return "You asked me to remind you of something, sir."

    state = _load()
    recent = [str(x) for x in (state.get(errand.lower()) or []) if x]
    said = ""
    try:
        said = _tidy(await asyncio.wait_for(_think(errand, recent), timeout=BUDGET_S),
                     errand)
    except asyncio.TimeoutError:
        log.info("reminder phrasing timed out; using the plain form")
    except Exception:
        log.debug("reminder phrasing failed", exc_info=True)

    if not said:
        # Not the same fallback every time either.
        pool = [f.format(t=errand) for f in FALLBACKS]
        fresh = [p for p in pool if p not in recent] or pool
        said = random.choice(fresh)

    recent.append(said)
    state[errand.lower()] = recent[-KEEP_RECENT:]
    _save(state)
    return said
