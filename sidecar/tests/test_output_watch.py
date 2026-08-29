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

    # --- it runs on asyncio's thread pool, and COM is per-THREAD ---------------
    # This is the bug that made the whole guard a no-op: the first call worked and
    # every one after it failed with "Cannot find window class", so the answer was
    # always "nothing is playing" — indistinguishable from working. It must
    # survive being called repeatedly from different pool threads.
    ow.reset()
    ow._broken = False
    errors = []

    async def pool_calls():
        for _ in range(6):
            try:
                await asyncio.to_thread(ow._scan)
            except Exception as e:                      # noqa: BLE001
                errors.append(f"{type(e).__name__}: {e}")
    asyncio.run(pool_calls())
    check("six calls from asyncio's thread pool all survive", not errors, errors[:2])
    check("...and it never marked itself broken", not ow._broken)

    # --- his OWN voice is not interference ------------------------------------
    check("this process counts as its own", os.getpid() in ow._own_pids())
    check("the peak floor ignores an open but silent player", 0 < ow.PEAK < 0.1, ow.PEAK)
    check("the hold is long enough for a pause in speech", 2 <= ow.HOLD_S <= 10, ow.HOLD_S)

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
