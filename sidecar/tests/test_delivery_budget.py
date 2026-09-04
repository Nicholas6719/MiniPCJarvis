"""Nothing JARVIS decides on his own may reach him thousands of times.

The night of 2026-08-31 needed two bugs at once. `tasks/scheduler.py` re-fired a
reminder every ten seconds because the write that moves it on kept failing. And
`delivery._too_soon` began:

    if not key or tier == URGENT:
        return False

`scheduler.announce` passes no key, so that read "no key, no limit" — the
caller least able to promise it will not repeat was the one exempted from every
check. Nicholas woke up to about 2,600 Telegram messages about his retainers.

Either fix alone bounds it. Both are here, plus a ceiling that does not care
what caused the flood — because the next flood will have a different cause.

Offline: no app, no network, no audio, no Telegram.
Run: python tests/test_delivery_budget.py
"""
import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "budget.db"))

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


def main() -> int:
    import delivery as D
    from delivery import ALERT, NOTABLE, URGENT, Delivery

    sent = []

    def fresh():
        d = Delivery()
        d.orchestrator = None            # never present -> the phone route
        return d

    # Route everything to "Telegram" and capture it, with no network anywhere.
    D.is_present = lambda: False
    D.telegram_available = lambda: True
    # Pin the clock's effect: the night ceiling is lower, so a test that did not
    # say which it wanted would pass by day and fail at 23:00.
    D._in_quiet_hours = lambda: False

    class FakeTelegram:
        async def send_proactive(self, text, tier=NOTABLE, subject=""):
            sent.append((tier, text))
            return True          # the bridge now reports whether Telegram took it

    fake_module = type(sys)("remote_telegram")
    fake_module.telegram = FakeTelegram()
    sys.modules["remote_telegram"] = fake_module
    D._remember_proactive = lambda text: None

    # ---- the exact shape of that night --------------------------------------
    # An ALERT with NO key, delivered over and over, as the stuck scheduler did.
    sent.clear()
    d = fresh()

    async def flood(dev, n, tier=ALERT):
        for _ in range(n):
            await dev.deliver("A reminder: wear my retainers", tier=tier)

    asyncio.run(flood(d, 400))
    check("a keyless repeated alert does not send 400 messages",
          len(sent) < 20, f"{len(sent)} messages got through")
    check("...it is deduplicated down to one",
          len(sent) == 1, f"{len(sent)} messages")

    # ---- URGENT repeats, but is not exempt ----------------------------------
    sent.clear()
    d2 = fresh()
    asyncio.run(flood(d2, 400, tier=URGENT))
    check("an urgent message still gets through", len(sent) >= 1, len(sent))
    check("...but repeating it 400 times does not send 400",
          len(sent) <= 2, f"{len(sent)} urgent messages")

    # ---- the ceiling holds even when every message is DIFFERENT -------------
    # Dedup cannot help here: 500 distinct alerts. This is the backstop that
    # does not care what went wrong upstream.
    sent.clear()
    d3 = fresh()

    async def many_distinct(dev, n):
        for i in range(n):
            await dev.deliver(f"distinct alert number {i}", tier=ALERT)

    asyncio.run(many_distinct(d3, 500))
    cap = 12
    check("500 distinct alerts are capped for the hour",
          len(sent) <= cap, f"{len(sent)} sent, cap {cap}")
    check("...and the ones that did get through are not zero",
          len(sent) == cap, f"{len(sent)} sent")

    # ---- and a normal day is untouched --------------------------------------
    sent.clear()
    d4 = fresh()

    async def a_normal_day(dev):
        await dev.deliver("Shooting near a Lawrence school.", tier=ALERT)
        await dev.deliver("Sudbury bridge closing this week.", tier=ALERT)
        await dev.deliver("NVDA is down 6% and you own it.", tier=ALERT)

    asyncio.run(a_normal_day(d4))
    check("three genuinely different alerts all reach him", len(sent) == 3, sent)

    # ---- NOTABLE is held, and does not spend the budget ---------------------
    sent.clear()
    d5 = fresh()

    async def notables(dev):
        for i in range(50):
            await dev.deliver(f"minor item {i}", tier=NOTABLE)
        await dev.deliver("something that matters", tier=ALERT)

    asyncio.run(notables(d5))
    check("notable items are held, not sent", all(t != NOTABLE for t, _ in sent), sent)
    check("...and holding them does not spend the alert budget",
          len(sent) == 1, f"{len(sent)}")

    # ---- and at night the ceiling is lower still ----------------------------
    # The flood reached him between midnight and seven, on a phone beside a
    # sleeping man. Tiers are unchanged - an urgent thing still wakes him - but
    # there is much less room for a mistake to spend while he is asleep.
    sent.clear()
    D._in_quiet_hours = lambda: True
    d6 = fresh()
    asyncio.run(many_distinct(d6, 500))
    check("at night the ceiling is tighter than by day",
          0 < len(sent) <= 3, f"{len(sent)} sent overnight")
    D._in_quiet_hours = lambda: False

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
