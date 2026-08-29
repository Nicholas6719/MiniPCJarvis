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

import asyncio
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

log = logging.getLogger("jarvis.output")

# Its OWN thread, never asyncio's shared pool. COM apartments are per-thread and
# cannot be changed once set: this needs MULTITHREADED, while the UI Automation
# in tools/uia.py needs the default single-threaded apartment. Run them on the
# same pool thread and whichever arrives second fails with "Cannot change thread
# mode after it is set" — which is exactly what happened: clicking by name
# started failing with "couldn't read that window's controls" as soon as this
# module existed, intermittently, depending on which thread each landed on.
_EXEC = ThreadPoolExecutor(max_workers=1, thread_name_prefix="jarvis-audiowatch")
THREAD_PREFIX = "jarvis-audiowatch"

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


_ran_on = ""


def _scan() -> tuple[bool, str]:
    """(is another app making noise, what it is).

    Runs on a worker thread, which is why the CoInitialize matters: COM is
    per-thread, and without it every call fails with "CoInitialize has not been
    called" — the check quietly answered "nothing is playing" forever, which
    looks exactly like working. Same reason tools/uia.py opens the same way.
    """
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
    global _ran_on
    _ran_on = threading.current_thread().name

    mine = _own_pids()
    for session in AudioUtilities.GetAllSessions():
        try:
            pid = session.ProcessId
            if not pid or pid in mine:
                continue
            # NO cast() here, however much the pycaw examples look like there
            # should be one. QueryInterface already hands back a typed, counted
            # interface pointer; casting it makes a second pointer that holds no
            # reference of its own, so the interface is freed underneath it and
            # the next call reads freed memory. It does not raise — it takes the
            # whole process down with an access violation in _ctypes.pyd, which
            # is what nine crashes today were. (The cast belongs on the raw
            # pointer that Activate() returns, which is where the examples use it.)
            meter = session._ctl.QueryInterface(IAudioMeterInformation)
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


async def playing() -> tuple[bool, str]:
    """The async way in, and the ONLY one production should use: it keeps the
    COM apartment on this module's own thread."""
    return await asyncio.get_running_loop().run_in_executor(_EXEC, other_app_is_playing)


async def scan_now() -> tuple[bool, str]:
    """An uncached look, for diagnostics. Same thread, same reason."""
    return await asyncio.get_running_loop().run_in_executor(_EXEC, _scan)


def thread_name() -> str:
    """Which thread the last scan ran on — the gate checks this, because the
    whole point is that it is not a shared one."""
    return _ran_on


def reset() -> None:
    """Forget what was heard — for tests, and after the output device changes."""
    global _last_at, _last, _heard_at
    _last_at, _last, _heard_at = 0.0, False, 0.0
