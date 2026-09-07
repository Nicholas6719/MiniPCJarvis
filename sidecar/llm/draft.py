"""The draft model: a small, quick answer for a plain knowledge question.

Measured 2026-09-07 beside the running gpt-oss-20b: gemma-3-4b on the CPU
answers "who wrote Dune" with its first word in ~0.6 s at ~20 tokens a
second; the big model takes two to four seconds to its first word. Most of
what he asks that is not a reflex is a question of this kind.

THE SIDECAR DECIDES WHAT IT MAY ANSWER, NEVER THE MODEL. Asked "what's the
weather tomorrow" in the bench, with an instruction to say DEFER, it invented
a forecast. So `eligible()` is a whitelist of shape - a short question with
no sign of needing the machine, the web, the time, his files, the stage or
his own memory of the day - and `deferred()` is a second net for the answer
itself. Anything not clearly a knowledge question goes to the big model as
before; a draft that hedges is thrown away and the big model answers.
"""
from __future__ import annotations

import re

from config import config

# A sentence that needs something the draft has not got: the clock, the
# weather, the news, the market, the web, his machine, his files, the
# stage, the camera, a reminder, a message, his own doings. Any of these and
# the big model with its tools takes the turn.
NEEDS_TOOLS = re.compile(
    r"\b(?:time|date|today|tonight|tomorrow|yesterday|this (?:week|morning|evening)|"
    r"weather|forecast|temperature|rain|snow|"
    r"news|headline|happening|latest|current|currently|right now|these days|recent\w*|"
    r"stock|share|shares|market|price|trading|earnings|crypto|bitcoin|"
    r"search|look up|google|browse|website|site|page|link|url|online|"
    r"open|close|launch|start|run|play|pause|volume|mute|screen|window|tab|"
    r"file|folder|download|document|desktop|clipboard|"
    r"hologram|render|model|print|project|part|stage|camera|picture|photo|image|"
    r"remind|reminder|timer|alarm|schedule|calendar|meeting|"
    r"email|mail|message|text|telegram|call|send|"
    r"remember|forget|note|my |mine\b|i have|i had|did i|i said|we said|"
    r"you (?:said|told|mentioned|say|just said)|say (?:that )?again|repeat|earlier|last time|"
    r"jarvis|yourself|your (?:name|voice|system|memory|settings)|"
    r"cpu|ram|memory|disk|battery|systems?|computer|pc\b|machine|"
    r"translate|convert|calculate|percent(?:age)?|how much is \d|what is \d|"
    r"\d+\s*(?:plus|minus|times|divided|x|%)\s*\d|"
    r"turn (?:on|off|up|down)|switch|set |make |create|build|design|write|draft|compose)\b",
    re.I)

# Shapes of a knowledge question.
QUESTION = re.compile(
    r"^\s*(?:who|what|when|where|why|which|how|is|are|was|were|do|does|did|can|could|"
    r"tell me|explain|describe|define|name)\b", re.I)

# Words that mean the draft is out of its depth - the second net.
DEFER = re.compile(
    r"\bDEFER\b|"
    r"\bI (?:don'?t|do not|can'?t|cannot|am not able to|'m not able to|am unable to|'m unable to)\b|"
    r"\bI (?:don'?t|do not) have (?:access|the ability|real[- ]time|current)\b|"
    r"\bas an ai\b|\bas a language model\b|\bI'?m not sure\b|\bI have no (?:way|access|information)\b|"
    r"\bno access to\b|\breal[- ]time (?:data|information|access)\b|\bcheck (?:a|the|your) (?:website|weather|news)\b|"
    r"\bI (?:would|will) need (?:to|more)\b|\bplease (?:provide|specify|clarify)\b",
    re.I)

MAX_WORDS = 28


def configured() -> str:
    return str(config.get("llm", "draft_model", default="") or "")


def eligible(text: str, *, must_use_tool: bool = False, stage: bool = False,
             pictures: bool = False, pending: bool = False) -> bool:
    """May the draft take this turn? A short, plain knowledge question with
    nothing in it that points at the machine, the web, the day or him."""
    if not configured() or must_use_tool or stage or pictures or pending:
        return False
    t = (text or "").strip()
    if not t or len(t.split()) > MAX_WORDS:
        return False
    if not QUESTION.match(t):
        return False
    if NEEDS_TOOLS.search(t):
        return False
    return True


def deferred(answer: str) -> bool:
    """Did the draft hedge, refuse or ask for the machine? Then the big model
    answers instead, and nothing of this is spoken."""
    a = (answer or "").strip()
    if not a:
        return True
    return bool(DEFER.search(a))


def system_prompt() -> str:
    """A paragraph, not the tool-laden prompt: the draft's whole value is a
    short prefix."""
    return ("You are JARVIS, Nicholas's assistant - calm, capable, quietly witty, "
            "in the manner of the Iron Man films. Answer his question directly in one "
            "or two spoken sentences, no lists, no markdown. Address him as \"sir\" at "
            "most once, attached to the final sentence. If the question needs live "
            "information, the internet, his computer, his files, his memories, the "
            "time, or anything you cannot know for certain, reply with exactly the "
            "single word DEFER and nothing else.")


def messages_for(history: list[dict], user_text: str, context: str = "") -> list[dict]:
    """The draft's prompt: persona, the turn context, the last two exchanges,
    the question."""
    out: list[dict] = [{"role": "system", "content": system_prompt()}]
    recent = [m for m in (history or []) if m.get("role") in ("user", "assistant")
              and isinstance(m.get("content"), str) and m.get("content")]
    for m in recent[-4:]:
        out.append({"role": m["role"], "content": str(m["content"])[:600]})
    out.append({"role": "user", "content": (context + "\n" if context else "") + user_text})
    return out
