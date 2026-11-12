"""A reply may not promise what nothing will do.

Out of his own transcript:
  * "let me know if you hear more about that" -> "I'll keep an eye on it."
    (2026-09-30) Nothing was watching. He was never told another word.
  * "when will you notify me about calls" -> "I'll alert you right away when
    a new call comes in, sir." (2026-11-08) No such rule existed.
  * "audit the codebase" -> "I'll start by scanning the project folder...
    Once that's done, I'll re..." (2026-10-27) It cannot, and did not.

The model says these because they are what an assistant says. When a turn has
run NO tool - no watch set, no reminder made, nothing started - a sentence
that promises to watch, to tell him later, or to go and do work is replaced
by the truth, once, and any further promise in the same reply is dropped.
A turn that did run a tool keeps its words: "I'll remind you at five" after
a reminder was set is a fact.
"""
from __future__ import annotations

import re

_I_WILL = r"\bI(?:'|’)?(?:ll| will| am going to|'m going to|’m going to)\s+"
WATCHING = re.compile(
    _I_WILL + r"(?:(?:also |just |certainly |definitely |happily |gladly )?)"
    r"(?:keep (?:an eye|watch|you posted|you updated|you informed|track|monitoring|listening)|"
    r"let you know|notify you|alert you|tell you (?:when|if|as soon|the moment)|inform you|"
    r"get back to you|monitor|watch (?:for|out|it|that|this)|update you|report back|check back|"
    r"remind you|ping you|flag (?:it|that|anything)|be watching|be listening)\b", re.I)
WORKING = re.compile(
    _I_WILL + r"(?:start|begin) (?:by|with)\b|"
    r"\bonce (?:that(?:'|’)?s|that is|it(?:'|’)?s|i(?:'|’)?m) (?:done|finished|complete),? I(?:'|’)?ll\b|"
    r"\b(?:then|next|after that),? I(?:'|’)?ll (?:check|scan|review|go through|look|examine|analy[sz]e|run)\b|"
    + _I_WILL + r"(?:work on (?:it|that|this)|look into (?:it|that|this)|investigate|dig into)\b", re.I)

CANT_WATCH = ("I can't keep watch on that by myself, sir - ask me again whenever you like, "
              "or tell me to set a reminder.")
CANT_WORK = "That's more than I can do on my own, sir - I can't carry out work in the background."


def unbacked_promise(sentence: str) -> str | None:
    """The honest line to say instead of this sentence, or None if it promises
    nothing. Only for a turn in which no tool has run."""
    s = sentence or ""
    if WATCHING.search(s):
        return CANT_WATCH
    if WORKING.search(s):
        return CANT_WORK
    return None
