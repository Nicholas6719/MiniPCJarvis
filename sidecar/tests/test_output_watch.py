"""A television is not talking to him.

The wake word was never the hole. The hole is the window left open after a turn,
in which plain speech is enough — film dialogue walked through it and he ran a
web search on what he heard. While another app is making noise, that window
closes and his name is required again.

Offline: no audio is played and no session is required.
Run: python tests/test_output_watch.py
"""
import asyncio
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "gate.db"))
from audio import output_watch as ow  # noqa: E402

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


def main() -> int:
    # --- it must never be able to cost him a turn ----------------------------
    ow.reset()
    ow._broken = False
    real = ow._scan
    ow._scan = lambda: (_ for _ in ()).throw(OSError("no audio service"))
    check("a broken audio API answers 'not playing', it does not raise",
          ow.other_app_is_playing() == (False, ""))
    check("...and having failed once, it stops asking", ow._broken)
    ow._broken = False
    ow._scan = real

    # --- speech has gaps, so a single silent look means nothing ---------------
    ow.reset()
    ow._scan = lambda: (True, "vlc.exe")
    playing, who = ow.other_app_is_playing()
    check("something playing is reported, with its name", playing and who == "vlc.exe",
          (playing, who))
    ow._scan = lambda: (False, "")
    time.sleep(ow.CACHE_S + 0.05)
    check("a gap between two lines of dialogue still counts as playing",
          ow.other_app_is_playing()[0] is True)
    # ...but it does not last forever
    ow._heard_at -= ow.HOLD_S + 1
    check("and a room that has gone quiet is quiet again",
          ow.other_app_is_playing()[0] is False)

    # --- the answer is cached: this runs on every block of microphone audio ---
    ow.reset()
    calls = []
    ow._scan = lambda: (calls.append(1), (False, ""))[1]
    for _ in range(20):
        ow.other_app_is_playing()
    check("twenty questions cost one look, not twenty", len(calls) == 1, len(calls))
    ow._scan = real

    # --- COM is per-THREAD, and this one must keep to its own ------------------
    # Two bugs live here. First: without CoInitialize every call failed and the
    # answer was always "nothing is playing" — indistinguishable from working.
    # Second, and worse: this needs a MULTITHREADED apartment while the UI
    # Automation behind "click the Send button" needs the default one, and an
    # apartment cannot be changed once set. Sharing asyncio's pool meant clicking
    # by name started failing with "couldn't read that window's controls",
    # intermittently, depending on which thread each landed on. So it runs on its
    # own thread and never touches the shared pool.
    ow.reset()
    ow._broken = False
    errors = []

    async def repeated():
        for _ in range(6):
            try:
                await ow.playing()
            except Exception as e:                      # noqa: BLE001
                errors.append(f"{type(e).__name__}: {e}")
        return ow.thread_name()
    ran_on = asyncio.run(repeated())
    check("six calls in a row all survive", not errors, errors[:2])
    check("...and it never marked itself broken", not ow._broken)
    check("it runs on its OWN thread, not a shared one",
          ran_on.startswith(ow.THREAD_PREFIX), ran_on)

    # ...and the thing it used to break still works afterwards: a plain
    # single-threaded CoInitialize on a pool thread, which is what UI Automation
    # does on every click by name.
    async def uia_style_after():
        await ow.playing()
        bad = []
        for _ in range(4):
            def sta():
                import comtypes
                comtypes.CoInitialize()
                return True
            try:
                await asyncio.to_thread(sta)
            except Exception as e:                      # noqa: BLE001
                bad.append(f"{type(e).__name__}: {e}")
        return bad
    broke = asyncio.run(uia_style_after())
    check("clicking by name still works after an audio scan", not broke, broke[:2])

    # --- it must survive being asked over and over ----------------------------
    # This one is not paranoia. A redundant cast() around the QueryInterface
    # result made a pointer holding no reference of its own, the interface was
    # freed underneath it, and the next call read freed memory — taking the whole
    # sidecar down with an access violation in _ctypes.pyd. Nine times in one
    # afternoon. It does not raise, so nothing but a crash tells you: the test
    # for it is simply to do it a lot and still be here afterwards.
    ow.reset()
    ow._scan = real
    ow._broken = False
    for _ in range(600):
        real()
    check("600 scans in a row and the process is still alive", True)

    # --- his OWN voice is not interference ------------------------------------
    # The meter reads the OUTPUT DEVICE, which hears JARVIS as clearly as it
    # hears a film. Without this he takes his own voice for a television and
    # starts demanding his name back straight after answering.
    ow.reset()
    ow._scan = real
    from audio.io import speaker
    was = speaker.last_write_at
    speaker.last_write_at = time.time()
    check("his own voice, a moment ago, is not 'something is playing'",
          real() == (False, ""), real())
    speaker.last_write_at = was
    check("the tail is long enough to cover his own speech",
          0.3 <= ow.OWN_TAIL_S <= 2.0, ow.OWN_TAIL_S)

    # --- one interface, made once ---------------------------------------------
    ow._meter = None
    real()
    first = ow._meter
    real()
    check("the meter is built once and kept, not rebuilt every look",
          first is not None and ow._meter is first)

    check("the peak floor ignores an open but silent player", 0 < ow.PEAK < 0.1, ow.PEAK)
    check("the hold is long enough for a pause in speech", 2 <= ow.HOLD_S <= 10, ow.HOLD_S)

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
