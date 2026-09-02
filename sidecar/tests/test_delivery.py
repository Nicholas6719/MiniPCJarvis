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

        # --- nowhere to send it ----------------------------------------------
        orch, tg = setup(present=False, telegram=False)
        r = asyncio.run(dv.delivery.deliver("Something", dv.ALERT))
        check("with no phone paired it is kept, not thrown away",
              "held" in r["delivered"], r)

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

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
