"""The second opinion an URGENT needs before it may make his phone sound.

`significance.urgent_gate` is deterministic, which is what lets the gates hold
it to every decision his phone ever got wrong. Rules still miss things a reader
would not - a feature that never uses a feature's words, a town named only as
someone's home. So at runtime, after the article has been read, the local model
is asked a fixed set of plain questions about it at temperature 0, as JSON:

    {happening_now, town, state, country, hazard_to_public, category, one_line}

and the answers can only VETO. A "no" to happening_now or hazard_to_public, a
town geo puts outside his radius, another state or country, or no category, and
the story waits for the next brief instead. Nothing the model says can make a
story urgent that the gate did not already pass.

When the model cannot answer (busy, down, unparseable), the story is neither
confirmed nor vetoed: the caller sends it SILENTLY rather than lose a local
emergency the rules were sure of, and never lets it sound.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re

log = logging.getLogger("jarvis.news_check")

CONFIRMED, VETOED, UNCONFIRMED = "confirmed", "vetoed", "unconfirmed"
BUDGET_S = 45.0              # the whole check, waiting included
QUIET_MAX_S = 20.0           # how long it may wait for him to stop talking
MAX_TOKENS = 700             # gpt-oss reasons first; room for that and the JSON
CATEGORIES = {"fire", "police", "hazmat", "road", "weather", "missing", "lockdown",
              "rescue", "health", "other"}

PROMPT = """You check one local news item before it is allowed to wake a man who lives \
in Framingham, Massachusetts. Answer ONLY with one JSON object, no prose:

{{"happening_now": true or false, "town": "the town where it is happening, or empty", \
"state": "the US state, or empty", "country": "the country", "hazard_to_public": true or false, \
"category": "fire|police|hazmat|road|weather|missing|lockdown|rescue|health|other|none", \
"one_line": "what is happening, 15 words or fewer"}}

Rules:
- happening_now is true only if the danger is still going on as written: not over, not a \
court case, not a feature, column, anniversary, recovery, settlement, opinion, prediction, \
advice or business story.
- hazard_to_public is true only if people nearby are at risk or must act now: avoid an area, \
stay inside, evacuate, boil water, watch for a missing person or a suspect.
- town is where the danger is, not where someone involved lives.
- Use only the text below.

HEADLINE: {headline}
TEXT: {text}

JSON:"""


def _truthy(v) -> bool | None:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("true", "yes", "y", "1"):
            return True
        if s in ("false", "no", "n", "0"):
            return False
    return None


def parse(raw: str) -> dict | None:
    """The last JSON object in the model's reply, or None."""
    s = str(raw or "")
    for m in reversed(list(re.finditer(r"\{[^{}]*\}", s, re.S))):
        try:
            obj = json.loads(m.group(0))
        except (ValueError, TypeError):
            continue
        if isinstance(obj, dict) and ("happening_now" in obj or "hazard_to_public" in obj):
            return obj
    return None


def judge(answer: dict | None) -> tuple[str, str]:
    """(verdict, why) for one parsed answer. The model can veto; it cannot
    promote, and an answer that cannot be read confirms nothing."""
    if not isinstance(answer, dict):
        return UNCONFIRMED, "the model gave no answer I could read"
    now = _truthy(answer.get("happening_now"))
    public = _truthy(answer.get("hazard_to_public"))
    if now is False:
        return VETOED, "the model says it is not happening now"
    if public is False:
        return VETOED, "the model says it is no danger to the public"
    country = re.sub(r"[^a-z ]", "", str(answer.get("country") or "").lower()).strip()
    if country and not (country in ("us", "usa", "america") or "united states" in country
                        or country.startswith(("us ", "usa "))):
        return VETOED, f"the model places it in {answer.get('country')}"
    state = re.sub(r"[^a-z ]", "", str(answer.get("state") or "").lower()).strip()
    if state and not ("massachusetts" in state or state in ("ma", "mass")
                      or state.startswith(("ma ", "mass "))):
        return VETOED, f"the model places it in {answer.get('state')}"
    cat = str(answer.get("category") or "").strip().lower()
    if cat in ("", "none", "null"):
        return VETOED, "the model sees no hazard"
    town = str(answer.get("town") or "").strip()
    if town:
        try:
            import geo
            t = geo.town(re.split(r",|\(", town)[0].strip())
            if t is not None and not geo.within_radius(t.miles):
                return VETOED, f"the model places it in {t.name}, {t.miles:.0f} miles away"
        except Exception:
            log.debug("could not place the model's town %r", town, exc_info=True)
    if now is None or public is None:
        return UNCONFIRMED, "the model did not say yes or no"
    return CONFIRMED, f"confirmed: {str(answer.get('one_line') or cat)[:100]}"


async def _ask(headline: str, text: str) -> str:
    from llm.provider import local_llm, wait_for_quiet
    await wait_for_quiet(QUIET_MAX_S)          # never beside one of his turns
    out = ""
    async for ch in local_llm.stream(
            [{"role": "user", "content": PROMPT.format(headline=headline[:300],
                                                       text=str(text or "")[:1500])}],
            max_tokens=MAX_TOKENS, sampling={"temperature": 0.0}):
        out += ch.text or ""
        if ch.done:
            break
    return out


async def confirm(story: dict) -> dict:
    """{verdict, why, answer} for one story the gate has already passed.
    Never raises."""
    headline = str(story.get("headline") or story.get("title") or "")
    text = str(story.get("summary") or story.get("description") or "")
    try:
        raw = await asyncio.wait_for(_ask(headline, text), timeout=BUDGET_S)
    except asyncio.TimeoutError:
        log.info("news check timed out for %r", headline[:60])
        return {"verdict": UNCONFIRMED, "why": "the model took too long", "answer": None}
    except Exception as e:
        log.info("news check failed for %r: %s", headline[:60], e)
        return {"verdict": UNCONFIRMED, "why": "the model could not be asked", "answer": None}
    answer = parse(raw)
    verdict, why = judge(answer)
    log.info("news check %s for %r: %s", verdict, headline[:60], why)
    return {"verdict": verdict, "why": why, "answer": answer}
