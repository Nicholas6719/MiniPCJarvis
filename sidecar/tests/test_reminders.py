"""Reminders: "every night" means every night, and he says what he actually stored.

From a real Telegram exchange on 2026-08-30:

    > Remind me every night at 9 pm to wear my retainers please
    < Reminder set for 9:00 PM Sunday.          <- ONE Sunday, not every night
    > Not just Sunday, every night
    < Reminder set for 9:00 PM daily.           <- untrue: it stored 3:46 PM

Three separate failures in four lines: the recurrence was dropped, correcting it
left the first reminder in place so there were two, and the confirmation
described something that had not been stored. The last is the worst — being told
the wrong time is worse than being set the wrong time, because there is nothing
to notice.

Run: python tests/test_reminders.py
"""
import datetime as dt
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "gate.db"))
from brain.skills import say_reminder, slots_reminder  # noqa: E402
from tools.task_tools import list_reminders, set_reminder  # noqa: E402

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


def main() -> int:
    # --- the exact request that failed ---------------------------------------
    said = "remind me every night at 9 pm to wear my retainers please"
    s = slots_reminder(said)
    check("it is heard at all", s is not None, s)
    check("'every night' is a STANDING reminder", s.get("recurrence") == "daily", s)
    check("at the time he asked for", s.get("at_time") == "21:00", s)
    check("and 'every night' is not part of what he is reminded OF",
          "every" not in s.get("text", "").lower(), s.get("text"))
    check("nor is the politeness", "please" not in s.get("text", "").lower(), s.get("text"))

    res = set_reminder(**s)
    check("it is stored as recurring", res.get("recurrence") == "daily", res)
    check("at 21:00", str(res.get("due", "")).endswith("21:00"), res.get("due"))

    # --- what he is TOLD must match what is stored ---------------------------
    line = say_reminder(s, res)
    check("he is told the right time", "9:00 PM" in line, line)
    check("...and that it repeats", "every day" in line.lower(), line)

    # --- asking again replaces, it does not pile up --------------------------
    before = len(list_reminders()["reminders"])
    again = set_reminder(**s)
    after = list_reminders()["reminders"]
    check("the same reminder twice is one reminder", len(after) == before, len(after))
    check("...and it says so", again.get("replaced") == 1, again.get("replaced"))

    # --- the other shapes still read like a person wrote them ----------------
    for req, want in (
        ("remind me at 6 pm to start dinner", "6:00 PM"),
        ("remind me every weekday at 7 am to leave for work", "every weekday"),
        ("remind me every monday at 9 am to submit the report", "every Monday"),
        ("remind me every day at 8 am to take my pills", "every day"),
    ):
        sl = slots_reminder(req)
        check(f"{req[:44]!r} is understood", sl is not None)
        if sl:
            spoken = say_reminder(sl, set_reminder(**sl))
            check(f"...and reads back with {want!r}", want in spoken, spoken)

    # --- a relative reminder still works -------------------------------------
    sl = slots_reminder("remind me in 25 minutes to call dad")
    check("'in 25 minutes' is still understood",
          sl and sl.get("minutes_from_now") == 25, sl)
    check("...and is not made recurring by accident",
          sl and "recurrence" not in sl, sl)

    # --- a failure must never be reported as a success ------------------------
    bad = set_reminder("something", at_time="not a time")
    check("a bad time is an error, not a cheerful confirmation", "error" in bad, bad)
    check("...and he says so", say_reminder({}, bad).startswith("I couldn't"),
          say_reminder({}, bad))

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
