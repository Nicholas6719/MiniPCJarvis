"""The urgent chase reaches his phone, so it answers to the same ceiling.

From the 2026-09-01 audit. An URGENT alert is sent and then chased — "Still
unanswered, sir …" — until he taps Got it. Bounded at three repeats, which is
fine on its own. But `_chase` called the Telegram API directly, so those
follow-ups never touched `delivery`'s hourly budget: a cap of 12 messages an
hour actually permitted up to 48, because every alert could carry three
invisible chases behind it. Overnight, at a cap of 3, it permitted 12.

Two changes, both here: chases are charged against the budget and stop when it
is spent, and at night there is ONE follow-up instead of three.

Also covers the briefing's `_seen` dict, which was capped at 400 when written to
disk and unbounded in memory.

Offline: no Telegram, no network. Run: python tests/test_chase_budget.py
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "chase.db"))

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


def main() -> int:
    import delivery as D
    from delivery import ALERT, URGENT, Delivery

    # --- chases are visible to the ceiling ----------------------------------
    d = Delivery()
    D._in_quiet_hours = lambda: False
    check("a fresh delivery has budget", d.has_budget(URGENT))

    for _ in range(12):
        d.note_sent()
    check("a chase spends the same budget a message does",
          not d.has_budget(URGENT), "12 sends did not exhaust a cap of 12")

    # ...and it never mutes him by accident
    broken = Delivery()
    broken._over_budget = lambda tier: (_ for _ in ()).throw(RuntimeError("boom"))
    check("if the budget cannot be read, he is NOT silenced",
          broken.has_budget(URGENT) is True)

    # --- the night schedule is one follow-up, not three ---------------------
    import inspect

    import remote_telegram
    src = inspect.getsource(type(remote_telegram.telegram)._chase)
    check("the chase asks delivery before each follow-up",
          "has_budget" in src, src[:200])
    check("...and charges the budget after sending",
          "note_sent" in src, src[:200])
    check("...and chases once at night, three times by day",
          "(600,) if _in_quiet_hours() else (300, 300, 600)" in src, src[:200])

    # --- the briefing's seen-set is bounded in memory ------------------------
    from briefing import briefing

    briefing._seen = {}
    for i in range(2000):
        briefing._remember_seen(f"headline-{i}")
    check("the seen-set stops growing", len(briefing._seen) <= briefing._SEEN_MAX * 2,
          len(briefing._seen))
    check("...and it keeps the NEWEST entries, not the oldest",
          "headline-1999" in briefing._seen and "headline-0" not in briefing._seen,
          sorted(briefing._seen)[:2])

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
