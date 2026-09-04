"""Protocols: the film's idiom over the routines he teaches by voice.

"When I say lockdown protocol, lock the PC and mute" has always taught a
routine. What was missing is that JARVIS did not answer to the way the films
say it — "initiate the lockdown protocol", "engage protocol lockdown",
"lockdown protocol, now" — and that a protocol he never taught got the shrug
every unknown sentence gets, instead of an offer to set one up.

Pure text; the routing itself lives in brain.router.match_command.
"""
from __future__ import annotations

import re

_VERBS = r"(?:initiate|engage|run|execute|activate|start|begin|launch|trigger|commence)"
_NAME = r"[a-z0-9][a-z0-9' -]{0,40}?"
_PROTOCOL = re.compile(
    r"^(?:hey |hi |ok |okay )?(?:jarvis[,!.]?\s*)?"
    r"(?:(?:please\s+)?" + _VERBS + r"\s+)?(?:the\s+)?"
    r"(?:protocol\s+(?P<a>" + _NAME + r")|(?P<b>" + _NAME + r")\s+protocol)"
    r"(?:[,!.]?\s*(?:now|please|sir))*[.!?]?$",
    re.I)
_LIST = re.compile(
    r"^(?:hey |ok |okay )?(?:jarvis[,!.]?\s*)?(?:what|which|list|show me|tell me)\b.*\bprotocols\b",
    re.I)


def protocol_name(text: str) -> str | None:
    """The protocol a sentence invokes, or None when it is not that sentence.

    "initiate the lockdown protocol" -> "lockdown"; "engage protocol lockdown"
    -> "lockdown"; "what's the internet protocol for" -> None (words after the
    noun); "open the protocol document" -> None (a verb that is not an order
    to run one); "what protocols do I have" -> None (that is the listing).
    """
    m = _PROTOCOL.match((text or "").strip())
    if not m:
        return None
    name = (m.group("a") or m.group("b") or "").strip(" '-").lower()
    name = re.sub(r"\s+", " ", name)
    if not name or name in ("the", "a", "an", "my", "this", "that"):
        return None
    return name


def wants_listing(text: str) -> bool:
    return bool(_LIST.match((text or "").strip()))


def phrase_for(name: str) -> str:
    """The taught phrase a protocol lives under."""
    return f"{name.strip().lower()} protocol"


def taught(brain) -> list[str]:
    """Protocol names he has taught, from the routines table."""
    out = []
    for c in brain.commands():
        p = str(c.get("phrase") or "")
        if p.endswith(" protocol") and len(p) > len(" protocol"):
            out.append(p[:-len(" protocol")].strip())
    return sorted(set(out))


def missing_line(name: str) -> str:
    return (f"I don't have a {name} protocol yet, sir. Tell me what it should do — "
            f"say \"when I say {name} protocol, do…\" — and I'll set it up.")


def list_line(names: list[str]) -> str:
    if not names:
        return ("No protocols yet, sir. Say \"when I say lockdown protocol, lock the PC "
                "and mute\" and I'll make one.")
    if len(names) == 1:
        return f"One protocol, sir: {names[0]}."
    return f"{len(names)} protocols, sir: {', '.join(names[:-1])} and {names[-1]}."
