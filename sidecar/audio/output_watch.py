"""Is something ELSE making noise right now?

After a turn, JARVIS leaves a short window in which plain speech is enough — you
should not have to say his name twice in one conversation. A television walks
straight through that window. It really happened: film dialogue was heard as a
request, and he went and ran a web search on it.

The wake word is not the hole; the open window is. So while another application
is producing sound, the window closes and his name is required again. In a quiet
room nothing changes at all.

Windows already knows which processes are rendering audio and how loud each one
is, so this asks it rather than guessing from the microphone — a microphone
cannot tell a film from a person, which is the entire problem.

Our OWN output is excluded, or he would deafen himself every time he spoke.
"""
from __future__ import annotations

import logging
import os
import time

log = logging.getLogger("jarvis.output")

PEAK = 0.02          # below this a session is silent-but-open (a paused player)
CACHE_S = 0.75       # enumerating sessions costs a few ms; the answer barely moves
# Speech has gaps. Measured against a real clip, the meter reads silent between
# every line — so a single look says "nothing is playing" in the middle of a
# conversation on screen. Once something has been heard, hold that for a few
# seconds, which is also how long a film takes to draw breath.
HOLD_S = 4.0

_last_at = 0.0
_last: bool = False
_heard_at = 0.0      # when another app was last actually making noise
_broken = False      # if the API is unavailable, never block a turn over it


def _own_pids() -> set[int]:
    """This process and its parent — JARVIS's own speech is not interference."""
    pids = {os.getpid()}
    try:
        import psutil
        me = psutil.Process()
        if me.parent():
            pids.add(me.parent().pid)
        for c in me.children(recursive=True):
            pids.add(c.pid)
    except Exception:
        pass
    return pids


def _scan() -> tuple[bool, str]:
    """(is another app making noise, what it is).

    Runs on a worker thread, which is why the CoInitialize matters: COM is
    per-thread, and without it every call fails with "CoInitialize has not been
    called" — the check quietly answered "nothing is playing" forever, which
    looks exactly like working. Same reason tools/uia.py opens the same way.
    """
    from ctypes import POINTER, cast

    import comtypes
    from comtypes import CLSCTX_ALL  # noqa: F401  (imported for pycaw's sake)
    from pycaw.pycaw import AudioUtilities, IAudioMeterInformation

    # MULTITHREADED, not the default apartment: this runs on whatever worker
    # thread asyncio hands us, and a single-threaded apartment needs a message
    # pump that a pool thread does not have — the second call onwards then fails
    # with "Cannot find window class" and the check silently answers "nothing is
    # playing" forever, which looks exactly like working.
    try:
        comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
    except OSError:
        pass          # already initialised on this thread, in some apartment

    mine = _own_pids()
    for session in AudioUtilities.GetAllSessions():
        try:
            pid = session.ProcessId
            if not pid or pid in mine:
                continue
            meter = cast(session._ctl.QueryInterface(IAudioMeterInformation),
                         POINTER(IAudioMeterInformation))
            if meter.GetPeakValue() > PEAK:
                name = "something"
                try:
                    name = session.Process.name() if session.Process else "something"
                except Exception:
                    pass
                return True, name
        except Exception:
            continue
    return False, ""


def other_app_is_playing() -> tuple[bool, str]:
    """Has another application made noise in the last few seconds, and which one."""
    global _last_at, _last, _heard_at, _broken
    if _broken:
        return False, ""
    now = time.time()
    if now - _last_at >= CACHE_S:
        try:
            playing, who = _scan()
        except Exception:
            # The audio API being unavailable must never cost him a turn: fall
            # back to the old behaviour and stop asking.
            log.warning("cannot read audio sessions - not gating on output", exc_info=True)
            _broken = True
            return False, ""
        _last_at, _last = now, playing
        if playing:
            _heard_at, _last = now, True
            return True, who
    return (now - _heard_at) < HOLD_S, ""


def reset() -> None:
    """Forget what was heard — for tests, and after the output device changes."""
    global _last_at, _last, _heard_at
    _last_at, _last, _heard_at = 0.0, False, 0.0
