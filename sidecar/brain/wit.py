"""Dry wit, on a budget (2026-09-18).

The film JARVIS is funny about once a scene and never on command. The
difference between a butler and a chatbot is restraint: one unprompted
remark an hour at most, only on a handful of triggers, never in a muted
room, never on the phone, never twice the same line running.

Triggers:
  mistake   after he had to apologise (a correction, a lost turn)
  refusal   after an absurd ask he cannot do (order a pizza, dim the lights)
  late      a bare wake between one and five in the morning
  streak    the fifth question in a row without a pause
"""
from __future__ import annotations

import random
import time

from config import config

BUDGET_S = 3600.0
_last_at = 0.0
_last_line = ""

LINES = {
    "mistake": [
        "I'll add that to the list of things I'm quietly embarrassed about.",
        "Not my finest moment, sir.",
        "I'd blame the hardware, but it would only agree with you.",
        "Noted, filed, and slightly ashamed.",
    ],
    "refusal": {
        "order": [
            "Though if you find a way to wire me to a pizza oven, I'm listening.",
            "I remain, regrettably, unable to feed you.",
        ],
        "home": [
            "The house doesn't take my calls yet, sir.",
            "Give me a switch to talk to and I'll talk to it.",
        ],
        "call": [
            "A phone that answers to me is on the list, right after the arc reactor.",
        ],
        "message": [
            "I'm allowed to bother exactly one person, sir, and it's you.",
        ],
        "email": [
            "I draft; you send. It keeps us both out of trouble.",
        ],
    },
    "late": [
        "Burning the midnight oil, sir?",
        "It's rather late, sir. I'm not judging. I'm merely noting.",
        "The small hours again, sir.",
    ],
    "streak": [
        "You're on a roll, sir.",
        "I do enjoy a busy evening.",
    ],
}


def _enabled() -> bool:
    return bool(config.get("persona", "wit", default=True))


def _muted() -> bool:
    try:
        from audio.io import speaker
        return speaker.silent_until > time.time()
    except Exception:
        return False


def remark(trigger: str, kind: str = "", *, hour: int | None = None, remote: bool = False) -> str:
    """One line, or ''. Spends the hourly budget only when it returns a line."""
    global _last_at, _last_line
    if not _enabled() or remote or _muted():
        return ""
    if time.time() - _last_at < BUDGET_S:
        return ""
    pool: list[str] = []
    if trigger == "refusal":
        pool = LINES["refusal"].get(kind, [])
    elif trigger == "late":
        if hour is None or not (1 <= hour <= 5):
            return ""
        pool = LINES["late"]
    else:
        pool = LINES.get(trigger, [])
    pool = [ln for ln in pool if ln != _last_line] or pool
    if not pool:
        return ""
    line = random.choice(pool)
    _last_at, _last_line = time.time(), line
    return line


def reset_for_tests() -> None:
    global _last_at, _last_line
    _last_at, _last_line = 0.0, ""
