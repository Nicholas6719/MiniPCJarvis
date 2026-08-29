"""Is something ELSE making noise right now?

OFF BY DEFAULT, and the reason matters: with this enabled the packaged sidecar
corrupts its own heap and dies — three different faulting modules (_ctypes.pyd,
ntdll.dll, ucrtbase.dll), which is the signature of memory being scribbled on
and the crash surfacing wherever that allocation is next touched. Nine crashes
in one afternoon, each a silent forty-second restart.

What has been RULED OUT, so nobody repeats it:
  * the original per-session enumeration, and its genuinely wrong cast() around
    a QueryInterface result (a real use-after-free, fixed) — the minimal
    one-interface version below crashes too
  * running it on asyncio's shared pool (it has its own thread now)
  * PortAudio churn on the same device: 300 stream open/close cycles while
    metering, standalone, clean
  * both COM apartments at once: 578,000 scans against concurrent UI Automation,
    standalone, clean
  * 4,000 back-to-back scans standalone, clean

It has never been reproduced OUTSIDE the PyInstaller bundle, which is the one
difference left and the place to look next. Until then it stays off: answering
the television occasionally is a smaller sin than dying.

Turn it on with wake.ignore_while_audio_plays and watch tests/soak_e2e.py — that
is the only test that catches this, because every feature test stays green while
the process is crash-looping underneath them.


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


# Our own voice, coming back at us. The meter below reads the OUTPUT DEVICE,
# which hears JARVIS as clearly as it hears anything else, so a moment after he
# speaks is not evidence that a television is on.
OWN_TAIL_S = 0.7

_ran_on = ""
_meter = None            # one long-lived interface, created once


def _get_meter():
    """The output device's peak meter. Made ONCE and kept.

    The first version of this enumerated every audio session and asked each one
    for its level, which named the app but did ~200x the COM work per look — and
    corrupted the heap, taking the whole sidecar down. One interface, obtained
    once and called, is the smallest possible surface for the same answer.
    """
    global _meter
    if _meter is not None:
        return _meter
    from ctypes import POINTER, cast

    import comtypes
    from comtypes import CLSCTX_ALL, CoCreateInstance
    from pycaw.constants import CLSID_MMDeviceEnumerator, EDataFlow, ERole
    from pycaw.pycaw import IAudioMeterInformation, IMMDeviceEnumerator

    enumerator = CoCreateInstance(CLSID_MMDeviceEnumerator, IMMDeviceEnumerator,
                                  comtypes.CLSCTX_INPROC_SERVER)
    device = enumerator.GetDefaultAudioEndpoint(EDataFlow.eRender.value,
                                                ERole.eMultimedia.value)
    # The cast IS correct here: Activate hands back a raw, uncounted pointer,
    # which is exactly what cast is for. (It is NOT correct on a QueryInterface
    # result, which already owns its reference — that mistake cost nine crashes.)
    _meter = cast(device.Activate(IAudioMeterInformation._iid_, CLSCTX_ALL, None),
                  POINTER(IAudioMeterInformation))
    return _meter


def _scan() -> tuple[bool, str]:
    """Is sound coming out of this machine that is not ours."""
    import comtypes

    # MULTITHREADED, and on this module's own thread: COM apartments are
    # per-thread and cannot be changed once set, and the UI Automation behind
    # "click the Send button" needs the default one. Sharing a thread with it
    # broke clicking by name.
    try:
        comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
    except OSError:
        pass
    global _ran_on, _meter
    _ran_on = threading.current_thread().name

    from audio.io import speaker
    if time.time() - getattr(speaker, "last_write_at", 0.0) < OWN_TAIL_S:
        return False, ""            # that is him, not the room

    try:
        peak = _get_meter().GetPeakValue()
    except Exception:
        # the default output device changed under us: drop it and rebuild once
        _meter = None
        peak = _get_meter().GetPeakValue()
    return (peak > PEAK), ("something" if peak > PEAK else "")


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
