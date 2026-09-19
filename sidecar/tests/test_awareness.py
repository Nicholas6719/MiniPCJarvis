"""Situational awareness, interrupt tiers, phone status, calendar warning, wit
(2026-09-18). Run: python tests/test_awareness.py"""
import asyncio
import datetime as dt
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "aware.db"))
ROOT = Path(__file__).resolve().parent.parent
fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


def main() -> int:
    import awareness as A
    import volatile
    from tools import phone as P
    from brain import wit as W

    print("\n-- the phone as a sensor --")
    check("a status payload is recognised", P.kind_of('{"type":"status","battery":18,"charging":false}') == "status")
    r = P.ingest_payload('{"type":"status","battery":"18%","charging":"false","place":"left campus"}')
    st = P.status()
    check("...stored and readable", r.get("stored") == 1 and st and st["battery"] == 18 and st["charging"] is False and st["place"] == "left campus", (r, st))
    P.ingest_payload('{"type":"battery","battery":0.42,"charger":"connected"}')
    st = P.status()
    check("a 0-1 fraction and 'connected' are understood", st["battery"] == 42 and st["charging"] is True, st)
    check("speech is never a payload", P.kind_of("my phone is at 18 percent") is None)

    print("\n-- the watcher decides a tier per note --")
    a = A.Awareness()
    a.first_scan = False
    P.ingest_payload('{"type":"status","battery":12,"charging":false}')
    notes = a._phone()
    check("12 percent unplugged -> NEXT_PAUSE", any(n.tier == A.NEXT_PAUSE and "12 percent" in n.text for n in notes), [(n.tier, n.text) for n in notes])
    P.ingest_payload('{"type":"status","battery":4,"charging":false}')
    notes = a._phone()
    check("4 percent unplugged -> CUT_IN", any(n.tier == A.CUT_IN for n in notes), [(n.tier, n.text) for n in notes])
    P.ingest_payload('{"type":"status","battery":4,"charging":true}')
    check("4 percent but charging -> nothing to say", not any(n.tier != A.HOLD for n in a._phone()))
    soon = (dt.datetime.now() + dt.timedelta(minutes=8)).isoformat(timespec="minutes")
    later = (dt.datetime.now() + dt.timedelta(minutes=45)).isoformat(timespec="minutes")
    P.ingest_payload('{"type":"calendar","events":[{"title":"Chemistry lecture","start":"%s","location":"Room 204"},{"title":"Dinner","start":"%s"}]}' % (soon, later))
    up = P.upcoming(12)
    check("upcoming() finds the one within twelve minutes", len(up) == 1 and up[0]["title"] == "Chemistry lecture" and 6 <= up[0]["minutes"] <= 8, up)
    notes = a._calendar()
    check("an event in eight minutes -> NEXT_PAUSE, with the room", len(notes) == 1 and notes[0].tier == A.NEXT_PAUSE and "Room 204" in notes[0].text and ("in 7 minute" in notes[0].text or "in 8 minute" in notes[0].text), [(n.tier, n.text) for n in notes])
    check("...and only once", a._calendar() == [])
    n = A.Note("disk", "the C drive is down to 8 gigabytes free", A.NEXT_PAUSE, "disk:low")
    check("a HOLD note is never spoken", not a._worth_saying(A.Note("download", "a download landed", A.HOLD, "dl:x")))
    check("a NEXT_PAUSE note is, once", a._worth_saying(n))
    a.said[n.key] = time.time()
    check("...and not again inside its cooldown", not a._worth_saying(n))

    print("\n-- the front window becomes context, slowly --")
    a2 = A.Awareness()
    a2.front, a2.front_since = ("Chemistry.pdf - Adobe Reader", "acrord32.exe"), time.time() - 42 * 60
    ctx = a2.context_line()
    check("a window in front for 42 minutes is in the turn note", "Chemistry.pdf" in ctx and "42 minutes" in ctx, ctx)
    a3 = A.Awareness()
    a3.front, a3.front_since = ("Spotify", "spotify.exe"), time.time() - 60
    check("...but not after one minute", "Spotify" not in a3.context_line())

    print("\n-- speaking goes through delivery, by tier --")
    src = (ROOT / "awareness.py").read_text(encoding="utf-8")
    check("CUT_IN is URGENT (interrupts)", 'await delivery.deliver(text, URGENT, key=key, subject="awareness")' in src)
    check("NEXT_PAUSE waits for a silence, then ALERT", "State.IDLE, State.SLEEPING" in src and 'await delivery.deliver(text, ALERT, key=key, subject="awareness")' in src)
    check("phone-battery notes are for the desk only", 'if key.startswith("phone:") and not is_present():' in src)
    check("the loop is switchable and off the loop", 'config.get("awareness", "enabled", default=True)' in src and "asyncio.to_thread(self.observe)" in src)
    check("wired at boot with the delivery speaker", "_aw.awareness.speak = _aw.speak_via_delivery" in (ROOT / "main.py").read_text(encoding="utf-8"))
    check("the turn note carries it", "_aw.context_line()" in (ROOT / "llm" / "prompts.py").read_text(encoding="utf-8"))
    check("the Telegram poller accepts a status payload without a receipt", '"status": "phone status"' in (ROOT / "remote_telegram.py").read_text(encoding="utf-8"))

    print("\n-- wit, on a budget --")
    W.reset_for_tests()
    from audio.io import speaker
    speaker.silent_until = 0.0
    line = W.remark("refusal", "order")
    check("a refusal to order food gets one dry line", line and "pizza" in line or "feed" in line, line)
    check("...and the hour's budget is spent", W.remark("mistake") == "")
    W.reset_for_tests()
    check("never on the phone", W.remark("mistake", remote=True) == "")
    W.reset_for_tests()
    check("late only between one and five", W.remark("late", hour=14) == "" and W.remark("late", hour=3) != "")
    W.reset_for_tests()
    speaker.silent_until = time.time() + 60
    check("never in a muted room (a test)", W.remark("mistake") == "")
    speaker.silent_until = 0.0
    orch = (ROOT / "orchestrator.py").read_text(encoding="utf-8")
    check("hooked after an apology, a refusal and a late wake",
          'wit.remark("mistake", remote=self.remote_turn)' in orch and 'wit.remark("refusal"' in orch and 'wit.remark("late", hour=' in orch)
    from config import DEFAULTS
    check("switchable in config", DEFAULTS["persona"].get("wit") is True and DEFAULTS["awareness"]["enabled"] is True)

    # THE GATES SHARE ONE DATABASE: leave no fake calendar or phone behind
    # (test_briefing read "Chemistry lecture" as today's reminder, release 80).
    volatile.forget(P.KEY_CAL)
    volatile.forget(P.KEY_STATUS)
    volatile.forget(P.KEY_REM)

    print()
    if fails:
        print(f"FAILED: {len(fails)}: {fails}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
