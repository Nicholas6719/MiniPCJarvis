"""A test mute ends the moment he speaks, and /health says what a script
needs before it may touch him.

2026-09-06 19:45: he sat down and asked JARVIS four things by voice. JARVIS
answered every one into a speaker a variety script had muted for an hour -
and the release's own -Silent mute is an hour too. "He works as normal but
I can't hear him." A test never has a reason to mute a man who is asking.

Run: python tests/test_quiet.py
"""
import asyncio
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "quiet.db"))

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


async def main() -> int:
    import orchestrator as omod
    from orchestrator import orchestrator as orc
    from delivery import delivery

    # -- his voice lifts a test mute ------------------------------------------
    omod.speaker.silent_until = time.time() + 3000
    delivery.mute_until = omod.speaker.silent_until
    orc._voice_heard()
    check("a voice turn lifts the speaker mute", omod.speaker.silent_until == 0.0,
          omod.speaker.silent_until)
    check("...and the delivery mute with it", delivery.mute_until == 0.0, delivery.mute_until)
    check("...and remembers when he spoke", time.time() - orc.last_voice_ts < 2.0)

    # -- with no mute on, nothing is touched ----------------------------------
    omod.speaker.silent_until = 0.0
    orc._voice_heard()
    check("no mute, no change", omod.speaker.silent_until == 0.0)

    # -- the two voice paths call it, /text does not ---------------------------
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "orchestrator.py"), encoding="utf-8").read()
    check("both voice transcript paths lift the mute", src.count("self._voice_heard()") >= 2,
          src.count("self._voice_heard()"))
    text_path = src[src.index("async def handle_text"):] if "async def handle_text" in src else ""
    check("...a typed or injected turn does not (tests type)",
          "_voice_heard" not in text_path[:1500])

    # -- /health carries what a script must read first --------------------------
    import main as app_main
    orc.last_voice_ts = time.time() - 30
    omod.speaker.silent_until = time.time() + 120
    h = await app_main.health()
    check("/health says how long since he spoke by voice",
          h.get("last_voice_s") is not None and 29 <= h["last_voice_s"] <= 31, h)
    check("...and how long the mute has left", 118 <= (h.get("muted_s") or 0) <= 120, h)
    orc.last_voice_ts = 0.0
    omod.speaker.silent_until = 0.0
    h = await app_main.health()
    check("never spoken is None, not zero", h.get("last_voice_s") is None and h.get("muted_s") == 0.0, h)

    # -- the release and the scripts refuse to run over him -----------------------
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    rel = open(os.path.join(root, "scripts", "release.ps1"), encoding="utf-8").read()
    check("release.ps1 waits for quiet before installing", "last_voice_s" in rel and "not installing over him" in rel)
    check("...and unmutes at the end", "seconds = 0" in rel and "speaker unmuted" in rel)
    for name in ("workbench_variety.py", "variety_run.py"):
        p = os.path.join(root, ".agent", "scripts", name)
        if os.path.exists(p):
            s = open(p, encoding="utf-8").read()
            check(f"{name} checks his voice and mutes for minutes, not an hour",
                  "quiet_or_die" in s and '"seconds": 3600' not in s and '"seconds": 0' in s)

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
