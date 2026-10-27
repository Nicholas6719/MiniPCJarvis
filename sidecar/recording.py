"""Whether the turn in flight is HIS, or a test's.

Every live suite drives POST /text, which runs a full turn and files it
exactly as if he had typed it: a transcript row for each side, a turn_stats
row, and from those the day memory and "how did I do" numbers. Twenty-two
suites, run a few times a week, put hundreds of exchanges he never had into
his history ("what time is it" eighty times a night, a jet engine explained
on demand, "remember that my assistant is called Jarvis"). The day summary
read them as his day.

A turn is recorded unless somebody said otherwise. /text takes a
`record: false` field and sets the flag here before the turn is spawned; the
task copies the context, so every write the turn makes - from the
orchestrator, a skill, a child task - sees it. And while /debug/silence is on,
nothing is recorded at all: that endpoint exists only for tests, and a test
that forgot the field is still a test.
"""
from __future__ import annotations

import contextvars
import sys
import time

_recording: contextvars.ContextVar[bool] = contextvars.ContextVar("jarvis_recording", default=True)


def set_recording(flag: bool) -> contextvars.Token:
    """Mark the current context (and every task spawned from it) as recorded or not."""
    return _recording.set(bool(flag))


def reset(token: contextvars.Token) -> None:
    _recording.reset(token)


def test_silence_active() -> bool:
    """Is the test mute (/debug/silence) on right now?

    Read from the speaker's own clock, which is the one truth every other
    part of the test mute keys off, and only if the audio module is already
    loaded: a gate that never touched the audio stack must not load it here.
    """
    io = sys.modules.get("audio.io")
    if io is None:
        return False
    try:
        return float(getattr(io.speaker, "silent_until", 0.0)) > time.time()
    except Exception:
        return False


def recording() -> bool:
    """Should what this turn says and costs be written to his history?"""
    if not _recording.get():
        return False
    return not test_silence_active()
