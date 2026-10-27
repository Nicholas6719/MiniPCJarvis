"""Readings kept over time, so he can answer "compared to what?".

He could already tell you memory is at 61 percent. He could not tell you it has
been climbing all week, which is the more useful sentence and the one the
films' JARVIS actually says — he offers conclusions, not readouts. A number
with no baseline is a readout, and this gate's first rule is that JARVIS says
so rather than implying a comparison he never made.

Also here: the app in front by exe NAME only (a window title never reaches
the disk), "what have I been doing" from minutes-in-front, the stats reflex's
period slot, and the RAM alert's "up N points on last week".

Offline, hermetic: synthetic timestamps in the gate database.
Run: python tests/test_readings.py
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "readings.db"))

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


def main() -> int:
    from memory.store import memory as M

    DAY = 86400
    NOW = 1_800_000_000.0                    # a fixed clock, far from any real row
    M.db.execute("DELETE FROM readings WHERE ts BETWEEN ? AND ?", (NOW - 30 * DAY, NOW + DAY))
    M.db.execute("DELETE FROM app_context WHERE ts BETWEEN ? AND ?", (NOW - 30 * DAY, NOW + DAY))
    M.db.commit()

    def fill(field, start, end, value, step=600):
        rows = [(t, value) for t in range(int(start), int(end), step)]
        M.db.executemany(f"INSERT INTO readings (ts, {field}) VALUES (?,?)", rows)
        M.db.commit()

    print("\n-- nothing yet --")
    s = M.readings_trend("memory", days=7, now=NOW)
    check("no readings: he says he has not watched long enough", "long enough" in s, s)
    check("an unknown field is named, with what he does keep",
          "no history for voltage" in M.readings_trend("voltage", days=7, now=NOW))
    check("no baseline for the RAM alert", M.readings_baseline("ram_pct", days=7, now=NOW) is None)

    print("\n-- a week of readings and nothing before it --")
    fill("ram_pct", NOW - 7 * DAY, NOW, 58)
    s = M.readings_trend("ram", days=7, now=NOW)
    check("the average is stated", "Memory has averaged 58 percent over the last 7 days" in s, s)
    check("...and a number that did not move is not given a range", "barely moved" in s, s)
    check("...and the missing baseline is admitted, not implied",
          "nothing earlier to compare it against yet" in s, s)
    check("still no baseline for the alert", M.readings_baseline("ram_pct", days=7, now=NOW) is None)

    print("\n-- then the week before arrives --")
    fill("ram_pct", NOW - 14 * DAY, NOW - 7 * DAY, 48)
    s = M.readings_trend("memory", days=7, now=NOW)
    check("the conclusion: higher than the week before", "10 points higher than the 7 days before" in s, s)
    base = M.readings_baseline("ram_pct", days=7, now=NOW)
    check("the alert gets its baseline", base is not None and abs(base - 48) < 0.5, base)

    from proactive import proactive as P
    import memory.store as _store
    real_time = _store.time.time
    _store.time.time = lambda: NOW          # the alert asks "now"; pin it
    try:
        line = P._against_last_week(70)
        check("the RAM alert says how far from last week", line == ", up 22 points on last week", line)
        check("...and 'about where it was' inside three points", "about where it was" in P._against_last_week(50))
    finally:
        _store.time.time = real_time

    print("\n-- today against yesterday, with a range --")
    M.db.execute("DELETE FROM readings WHERE ts BETWEEN ? AND ?", (NOW - 30 * DAY, NOW + DAY))
    M.db.commit()
    fill("cpu", NOW - 2 * DAY, NOW - DAY, 30)
    for i, v in enumerate((10, 20, 30, 40, 50, 60) * 4):
        M.db.execute("INSERT INTO readings (ts, cpu) VALUES (?,?)", (NOW - DAY + i * 3000, v))
    M.db.commit()
    s = M.readings_trend("processor", days=1, now=NOW)
    check("today is 'today', with its range", s.startswith("The processor has averaged 35 percent today, between 10 and 60."), s)
    check("...against YESTERDAY, in points", "5 points higher than yesterday" in s, s)
    M.db.execute("UPDATE readings SET cpu=33 WHERE cpu=30 AND ts BETWEEN ? AND ?", (NOW - 2 * DAY, NOW - DAY))
    M.db.commit()
    s = M.readings_trend("processor", days=1, now=NOW)
    check("...and 'much the same' inside three points", "Much the same" in s, s)
    fill("disk_free_gb", NOW - DAY, NOW, 1200)
    check("disk is in gigabytes", "1200 gigabytes" in M.readings_trend("disk", days=1, now=NOW),
          M.readings_trend("disk", days=1, now=NOW))

    print("\n-- the app in front, by name only --")
    M.db.execute("DELETE FROM app_context WHERE ts BETWEEN ? AND ?", (NOW - 30 * DAY, NOW + DAY))
    M.db.commit()
    check("nothing recorded: nothing much", "Nothing much" in M.app_minutes(8, now=NOW))
    M.db.executemany("INSERT INTO app_context (ts, exe) VALUES (?,?)",
                     [(NOW - 7 * 3600 + i * 60, "code.exe") for i in range(120)]
                     + [(NOW - 2 * 3600 + i * 60, "brave.exe") for i in range(30)]
                     + [(NOW - 600, "explorer.exe"), (NOW - 540, "explorer.exe"), (NOW - 60, "obscure.exe")])
    M.db.commit()
    s = M.app_minutes(8, now=NOW)
    check("minutes in front, most first, in English",
          s == "Mostly VS Code for 2 hours, and Brave for 30 minutes, sir.", s)
    check("...the shell and a single sample are not 'what he was doing'", "explorer" not in s and "obscure" not in s, s)
    before = M.db.execute("SELECT COUNT(*) FROM app_context").fetchone()[0]
    M.record_app("Quarterly numbers - Excel")
    M.record_app(r"C:\Program Files\thing\tool.exe")
    after = M.db.execute("SELECT COUNT(*) FROM app_context").fetchone()[0]
    check("a window title never reaches the disk; a path is reduced to its name", after == before + 1)
    check("...lowercased", M.db.execute("SELECT exe FROM app_context ORDER BY rowid DESC LIMIT 1").fetchone()[0] == "tool.exe")
    M.record_reading(cpu=1.0, ram_pct=2.0, disk_free_gb=3.0, battery=None, plugged=None)
    row = M.db.execute("SELECT cpu, ram_pct, disk_free_gb, battery FROM readings ORDER BY rowid DESC LIMIT 1").fetchone()
    check("a live sample lands with its fields", tuple(row) == (1.0, 2.0, 3.0, None), row)

    print("\n-- the reflexes --")
    from brain import skills as S
    check("'has memory been climbing' asks for a week's trend of memory",
          S.slots_stats("has memory been climbing") == {"focus": "memory", "period": 7.0})
    check("'how has the cpu been today' asks for today", S.slots_stats("how has the cpu been today") == {"focus": "cpu", "period": 1.0})
    check("'how much ram am i using' is still a reading", S.slots_stats("how much ram am i using") == {"focus": "memory"})
    _store.time.time = lambda: NOW
    try:
        said = S.say_stats({"focus": "cpu", "period": 1.0}, {"cpu_percent": 5})
        check("the stats reflex answers a period from history", "has averaged 35 percent today" in said, said)
        said = S.say_stats({"focus": "memory", "period": 7.0}, {"ram_percent": 60})
        check("...and admits an empty history", "long enough" in said, said)
        check("'what have i been doing today' is the app summary",
              S.slots_been_doing("what have i been doing today") == {"hours": 16.0})
        check("'what have you been doing' is not", S.slots_been_doing("what have you been doing today") is None)
        check("'how much ram am i using' is not either", S.slots_been_doing("how much ram am i using") is None)
        said = S.say_been_doing({"hours": 8.0}, {})
        check("...and it answers in minutes in front", said.startswith("Mostly VS Code for 2 hours"), said)
    finally:
        _store.time.time = real_time

    M.db.execute("DELETE FROM readings WHERE ts BETWEEN ? AND ?", (NOW - 30 * DAY, NOW + DAY))
    M.db.execute("DELETE FROM app_context WHERE ts BETWEEN ? AND ?", (NOW - 30 * DAY, NOW + DAY))
    M.db.commit()
    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
