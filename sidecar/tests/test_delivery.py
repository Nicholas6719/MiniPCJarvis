"""Where he reaches Nicholas, and what is worth interrupting for.

One rule: **is he there?** At the PC, say it. Away, send it. Never wake a dark
screen; never talk to a room he has left.

Tiers decide whether it is worth interrupting for, not where it goes. Agreed
2026-08-30: interrupting a spreadsheet to say "there is breaking news" is welcome
when it is urgent — that is what being present means.

Offline: nothing is spoken and no message is sent.
Run: python tests/test_delivery.py
"""
import asyncio
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "gate.db"))
import delivery as dv  # noqa: E402
from state_machine import State  # noqa: E402

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


class FakeSM:
    def __init__(self, state):
        self.state = state


class FakeOrch:
    """Records what he was asked to say, and whether he was interrupted."""

    def __init__(self, state=State.IDLE):
        self.sm = FakeSM(state)
        self.said = []
        self.interrupted = False

    async def announce(self, text):
        self.said.append(text)

    async def interrupt(self):
        self.interrupted = True
        self.sm.state = State.IDLE


class FakeTelegram:
    def __init__(self):
        self.sent = []

    async def send_proactive(self, text, tier="notable", subject=""):
        self.sent.append((text, tier))
        return True          # the bridge reports whether Telegram accepted it


def setup(present: bool, state=State.IDLE, telegram=True):
    """Put the world in a known shape and return (orchestrator, telegram)."""
    orch, tg = FakeOrch(state), FakeTelegram()
    dv.delivery.orchestrator = orch
    dv.delivery._last.clear()
    dv.is_present = lambda: present
    dv.telegram_available = lambda: telegram
    sys.modules["remote_telegram"] = type(sys)("remote_telegram")
    sys.modules["remote_telegram"].telegram = tg
    return orch, tg


def main() -> int:
    real_present, real_avail = dv.is_present, dv.telegram_available
    real_quiet = dv._in_quiet_hours
    # PIN THE CLOCK. Without this the suite passes by day and fails at night:
    # the hourly ceiling drops from twelve to three inside quiet hours, so five
    # of these cases ran out of budget and reported "nothing delivered" — which
    # looks exactly like a routing bug and is not one. Found at 06:05 on
    # 2026-09-02, having passed every run before that. test_delivery_budget.py
    # already pins it this way; this file never did.
    dv._in_quiet_hours = lambda: False
    try:
        # --- he is at the machine -------------------------------------------
        orch, tg = setup(present=True)
        r = asyncio.run(dv.delivery.deliver("Breaking news, sir.", dv.ALERT))
        check("present: it is spoken", orch.said == ["Breaking news, sir."], r)
        check("present: nothing is sent to his phone", tg.sent == [], tg.sent)

        # --- he is not ------------------------------------------------------
        orch, tg = setup(present=False)
        r = asyncio.run(dv.delivery.deliver("Breaking news, sir.", dv.ALERT))
        check("away: nothing is spoken to an empty room", orch.said == [], orch.said)
        check("away: it goes to his phone", len(tg.sent) == 1, tg.sent)

        # --- urgent may interrupt him; a brief may not -----------------------
        orch, tg = setup(present=True, state=State.SPEAKING)
        asyncio.run(dv.delivery.deliver("There is an active shooter nearby.", dv.URGENT))
        check("urgent interrupts what he is doing", orch.interrupted and orch.said)

        orch, tg = setup(present=True, state=State.SPEAKING)
        asyncio.run(dv.delivery.deliver("Your morning brief, sir.", dv.BRIEF))
        check("a brief waits rather than interrupting", not orch.interrupted)
        check("...and reaches him anyway rather than being lost",
              orch.said == [] and len(tg.sent) == 1, (orch.said, tg.sent))

        # --- asleep is not an empty room, but it is not a conversation either -
        orch, tg = setup(present=True, state=State.SLEEPING)
        asyncio.run(dv.delivery.deliver("Breaking news, sir.", dv.ALERT))
        check("he does not start talking to a window he put away",
              orch.said == [], orch.said)
        check("...it goes to the phone instead", len(tg.sent) == 1, tg.sent)

        # --- notable is not worth anyone's attention now ---------------------
        orch, tg = setup(present=False)
        r = asyncio.run(dv.delivery.deliver("A mildly interesting thing.", dv.NOTABLE))
        check("notable is held for the next brief", "held" in r["delivered"], r)
        check("...and is not sent on its own", tg.sent == [], tg.sent)

        # --- a test mute holds the phone too ---------------------------------
        # /debug/silence quietened only the speaker, and a render finished by a
        # test while he was out went to his phone (2026-09-05 12:03).
        orch, tg = setup(present=False)
        dv.delivery.mute_until = time.time() + 60
        r = asyncio.run(dv.delivery.deliver("Your duck is ready, sir.", dv.ALERT, key="render-done:1"))
        check("muted: an alert is held, not sent", tg.sent == [] and r["why"] == "muted for a test", r)
        check("...and the ledger says so", dv.delivery.ledger and dv.delivery.ledger[-1]["why"] == "muted for a test",
              dv.delivery.ledger[-1:] )
        r = asyncio.run(dv.delivery.deliver("Active shooter in Natick.", dv.URGENT, key="urgent:1"))
        check("...but an emergency still reaches him", len(tg.sent) == 1, tg.sent)
        dv.delivery.mute_until = 0.0
        orch, tg = setup(present=False)
        r = asyncio.run(dv.delivery.deliver("Your duck is ready, sir.", dv.ALERT, key="render-done:2"))
        check("unmuted: it goes to the phone again", len(tg.sent) == 1, tg.sent)

        # --- nowhere to send it ----------------------------------------------
        orch, tg = setup(present=False, telegram=False)
        r = asyncio.run(dv.delivery.deliver("Something", dv.ALERT))
        check("with no phone paired it is kept, not thrown away",
              "held" in r["delivered"], r)

        # --- the phone was there and Telegram REFUSED -------------------------
        # This used to file the message as delivered regardless: _sent charged,
        # the text remembered as told, "telegram" returned — with the network
        # down while he was away, an ALERT vanished and "any update on that?"
        # was later answered as though he had heard it.
        orch, tg = setup(present=False)
        _original_send = FakeTelegram.send_proactive      # BEFORE overriding it
        async def refused(self, text, tier="notable", subject=""):
            tg.sent.append((text, tier))
            return False
        type(tg).send_proactive = refused
        before = len(dv.delivery._sent)
        r = asyncio.run(dv.delivery.deliver("The oven is on fire.", dv.ALERT, key="fire"))
        check("a send Telegram refused is reported as held, not delivered",
              r.get("delivered") == "held" and "accept" in r.get("why", ""), r)
        check("...and is not charged to the hourly budget",
              len(dv.delivery._sent) == before, len(dv.delivery._sent))
        check("...and is not remembered as told, so it can be sent again",
              "fire" not in dv.delivery._last, list(dv.delivery._last)[:4])
        type(tg).send_proactive = _original_send

        # --- he is not told the same thing over and over ---------------------
        orch, tg = setup(present=False)
        asyncio.run(dv.delivery.deliver("The same story", dv.ALERT, key="story-1"))
        asyncio.run(dv.delivery.deliver("The same story", dv.ALERT, key="story-1"))
        check("the same story twice is sent once", len(tg.sent) == 1, tg.sent)
        # ...but urgent is exempt: if it is worth waking him for, it is worth repeating
        asyncio.run(dv.delivery.deliver("Evacuate", dv.URGENT, key="story-1"))
        check("urgent is never suppressed as a repeat", len(tg.sent) == 2, tg.sent)

        # --- an empty message is not a message --------------------------------
        orch, tg = setup(present=True)
        r = asyncio.run(dv.delivery.deliver("   ", dv.ALERT))
        check("nothing is said about nothing", orch.said == [] and tg.sent == [], r)

        # --- a locked workstation is away, however recent the keystroke ------
        dv.is_present = real_present
        dv.workstation_locked = lambda: True
        check("a locked machine means he is not there", dv.is_present() is False)
    finally:
        dv.is_present, dv.telegram_available = real_present, real_avail
        dv._in_quiet_hours = real_quiet
        sys.modules.pop("remote_telegram", None)

    # --- what he is told unprompted is part of the conversation --------------
    # He got a Nepal alert at 1:42 on 2026-08-31, asked "Any updates on that?" at
    # 1:53, and was told "I can't pull the current weather data right now." The
    # model was not confused - proactive messages never entered the history, so
    # the newest thing it could see was a weather question from 1:34, and "that"
    # honestly meant the weather.
    import sys as _sys
    import types as _types

    import delivery as _dl
    noted = []

    class _FakeOrch:
        def note_proactive(self, text):
            noted.append(text)

    fake_mod = _types.ModuleType("orchestrator")
    fake_mod.orchestrator = _FakeOrch()
    real_mod = _sys.modules.get("orchestrator")
    _sys.modules["orchestrator"] = fake_mod
    try:
        _dl._remember_proactive("Two people were shot in Brockton — WCVB.")
        check("a proactive item joins the conversation", len(noted) == 1, noted)
        check("...with what he was actually told", "Brockton" in noted[0], noted)
        noted.clear()
        _dl._remember_proactive("   ")
        _dl._remember_proactive("")
        check("empty announcements are not recorded", noted == [], noted)
    finally:
        if real_mod is not None:
            _sys.modules["orchestrator"] = real_mod
        else:
            _sys.modules.pop("orchestrator", None)

    # --- a dead speaker must not swallow the message ------------------------
    # On 2026-09-02 his monitor's speakers were asleep, the output device refused
    # the audio, and he heard nothing at all when a render finished. The design
    # already says the phone is better than losing it — this is the check that
    # says so out loud, because "he is present" and "he can be spoken to" are two
    # different questions and only the first was ever asked.
    import asyncio as _aio

    import delivery as _D
    from audio.io import SpeakerStalled
    from state_machine import State as _S

    sent = []

    class _DeafOrch:
        class sm:
            state = _S.IDLE

        async def announce(self, text):
            raise SpeakerStalled("the audio output device is not accepting data")

    class _FakeTG:
        async def send_proactive(self, text, tier=None, subject=None):
            sent.append(text)
            return True

    real_orch = _D.delivery.orchestrator
    real_present, real_avail = _D.is_present, _D.telegram_available
    import remote_telegram as _rt
    real_tg = _rt.telegram
    # START FROM AN UNSPENT BUDGET. The cases above this one send messages, and
    # the hourly ceiling from the 2,600-message night is global — so this check
    # was measuring the ceiling ("hourly message budget spent") rather than the
    # thing it asks about, and failed for a reason that had nothing to do with
    # speakers. An order-dependent test is a test that will fail on someone else.
    real_sent = list(_D.delivery._sent)
    _D.delivery._sent.clear()
    try:
        _D.delivery.orchestrator = _DeafOrch()
        _D.is_present = lambda: True          # he IS at the machine...
        _D.telegram_available = lambda: True
        _rt.telegram = _FakeTG()
        r = _aio.run(_D.delivery.deliver("The plate is ready, sir.", _D.ALERT,
                                         key="render-done:gate"))
        check("a render he cannot hear still reaches his phone",
              r.get("delivered") == "telegram"
              and sent == ["The plate is ready, sir."], (r, sent))
        check("...and says why, rather than claiming he was away",
              "could not speak" in (r.get("why") or ""), r)
    finally:
        _D.delivery.orchestrator = real_orch
        _D.is_present, _D.telegram_available = real_present, real_avail
        _rt.telegram = real_tg
        _D.delivery._sent[:] = real_sent

    # --- on return, say what was held. Once. --------------------------------
    # What could not reach him (no phone paired, Telegram refused) sits in the
    # ledger as "held". When he comes back it is read out — the first sentence
    # of up to three things — and then marked, so neither the presence
    # watcher a minute later nor "what did I miss" reads it again.
    print("\n-- on return, what was held is said once --")
    real_quiet = dv._in_quiet_hours
    dv._in_quiet_hours = lambda: False
    real_sent = list(dv.delivery._sent)
    dv.delivery._sent.clear()
    try:
        orch, tg = setup(present=False, telegram=False)
        t0 = time.time()
        asyncio.run(dv.delivery.deliver("The disk is down to 9 gigabytes. Worth clearing some space.",
                                        dv.ALERT, key="held-test:1"))
        asyncio.run(dv.delivery.deliver("HandBrake has finished, sir.", dv.ALERT, key="held-test:2"))
        asyncio.run(dv.delivery.deliver("Evacuate now.", dv.URGENT, key="held-test:3"))
        held = [e for e in dv.delivery.entries(t0) if e.get("outcome") == "held"]
        check("three things were held while he was away", len(held) == 3, held)
        check("...each with a row id for marking later", all(e.get("id") for e in held), held)
        # the phone: a message that reached it is never read back
        orch, tg = setup(present=False, telegram=True)
        asyncio.run(dv.delivery.deliver("Markets are down, sir.", dv.ALERT, key="held-test:4"))
        # he is back
        orch, tg = setup(present=True)
        r = asyncio.run(dv.delivery.replay_held(t0))
        check("back at the desk: the held things are spoken", r.get("delivered") == "spoken", r)
        said = orch.said[-1] if orch.said else ""
        check("...first sentences only", "The disk is down to 9 gigabytes." in said and "Worth clearing" not in said, said)
        check("...the finished build too", "HandBrake has finished, sir." in said, said)
        check("...never the emergency", "Evacuate" not in said, said)
        check("...never what reached his phone", "Markets" not in said, said)
        check("...and marked told", all(e.get("replayed") for e in held if e.get("tier") != dv.URGENT), held)
        from memory.store import memory as _m
        rows = {e["id"]: e for e in _m.recent_deliveries(t0)}
        check("...on disk as well, so a restart does not forget he was told",
              all(rows[e["id"]]["replayed"] for e in held if e.get("tier") != dv.URGENT), rows)
        orch, tg = setup(present=True)
        r = asyncio.run(dv.delivery.replay_held(t0))
        check("a second return says nothing", r.get("delivered") == "nothing" and orch.said == [], (r, orch.said))
        # ...and even if the marking had failed, the key blocks a re-read
        for e in held:
            e["replayed"] = False
        orch, tg = setup(present=True)
        dv.delivery._last[f"held:{int(held[1]['ts'])}"] = (time.time(), dv.ALERT)
        r = asyncio.run(dv.delivery.replay_held(t0))
        check("...and _too_soon blocks it on the key regardless", r.get("why") == "said recently" and orch.said == [], (r, orch.said))
        check("the ledger keeps up to 300 characters now, not 160",
              _m.db.execute("SELECT MAX(LENGTH(text)) FROM deliveries").fetchone() is not None)

        # the greeting path claims the same key and stands the watcher down
        orch, tg = setup(present=False, telegram=False)
        t1 = time.time()
        asyncio.run(dv.delivery.deliver("Your print finished, sir.", dv.ALERT, key="held-test:5"))
        dv.delivery.note_presence(False, now=t1)
        dv.delivery.note_presence(True, now=t1 + 400)
        dv.delivery.note_presence(True, now=t1 + 430)
        line = dv.delivery.held_line(t1)
        check("the wake greeting gets the held line", line.startswith("Namely: Your print finished, sir."), line)
        check("...and the watcher's pending return is consumed with it", dv.delivery.take_return() is None)
        orch, tg = setup(present=True)
        r = asyncio.run(dv.delivery.replay_held(t1))
        check("...so nothing is read twice", r.get("delivered") == "nothing" and orch.said == [], (r, orch.said))

        print("\n-- the presence watcher: two samples make a return --")
        d = dv.Delivery()
        t = 1000.0
        check("present from the start is not a return", not d.note_presence(True, now=t) and not d.note_presence(True, now=t + 30))
        d.note_presence(False, now=t + 60)
        check("one keystroke on the way past is not a return", not d.note_presence(True, now=t + 600))
        check("...the second sample is", d.note_presence(True, now=t + 630))
        away = d.take_return()
        check("take_return says how long, once", away is not None and abs(away - 570) < 1, away)
        check("...and only once", d.take_return() is None)
        check("...and the catch-up window starts when he left", abs(d.away_since - (t + 60)) < 1, d.away_since)
    finally:
        dv._in_quiet_hours = real_quiet
        dv.delivery._sent[:] = real_sent
        sys.modules.pop("remote_telegram", None)

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
