"""A reminder must never become a flood.

On the night of 2026-08-31 Nicholas had a standing 9 p.m. reminder to wear his
retainers - one he set himself, and wanted. An audit write had failed without
rolling back, so SQLite's write lock stayed held and the
`UPDATE tasks SET due_ts=?` that moves a recurring task to tomorrow could not
commit. The row stayed `pending` with a due time in the past. The scheduler loop
ticks every ten seconds, found it due, and announced it again. And again.

Six messages a minute. He shut JARVIS down after about fifty. It came back, and
he woke up to roughly seven hundred Telegram messages asking where his retainers
were.

The stuck lock was the fault. THIS is the bug: the announce ran before the
reschedule, and nothing anywhere treated "I could not record that I sent this"
as a reason to stop sending it. A missed reminder is a small failure. An
unstoppable one drove him to shut the whole assistant off.

Offline: no app, no network, no audio.
Run: python tests/test_reminder_flood.py
"""
import asyncio
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "flood.db"))

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


def main() -> int:
    from tasks.scheduler import Scheduler

    def fresh(announce_sink):
        s = Scheduler()
        s.announce = announce_sink
        # start from a clean slate whatever else the gate DB holds
        s.db.execute("DELETE FROM tasks")
        s.db.commit()
        return s

    # ---- the healthy case: a daily reminder fires ONCE and moves on ---------
    said = []

    async def announce(t, **kw):
        said.append(t)

    s = fresh(announce)
    # Ten minutes late, not an hour. This case is about firing ONCE, and the
    # lateness is incidental — but an hour sits exactly on the staleness
    # threshold added on 2026-09-02, so the original -3600 made this test decide
    # a coin flip on microseconds. A fixture on a boundary tests the boundary,
    # not the thing it claims to.
    yesterday_9pm = time.time() - 600             # due, and in the past
    tid = s.add("wear my retainers", yesterday_9pm, "daily")

    async def tick(sched, n=6):
        """Run the loop body n times without waiting 10s between each."""
        for _ in range(n):
            now = time.time()
            rows = sched.db.execute(
                "SELECT id, due_ts, text, recurrence FROM tasks "
                "WHERE status='pending' AND due_ts <= ?", (now,)).fetchall()
            for r in rows:
                if sched._suppressed(r[0], r[1], now):
                    continue
                # Mirrors the real loop exactly. If this helper drifts from
                # _loop() the gate stops describing the thing it guards.
                if sched._too_late(r[0], r[1], now, r[3]):
                    continue
                await sched._fire(r[0], r[1], r[2], r[3])

    asyncio.run(tick(s))
    check("a due daily reminder is announced", len(said) >= 1, said)
    check("...exactly once, not once per tick", len(said) == 1,
          f"{len(said)} announcements from 6 ticks")
    nxt = s.db.execute("SELECT due_ts FROM tasks WHERE id=?", (tid,)).fetchone()[0]
    check("...and it is rescheduled into the future", nxt > time.time(),
          f"next due {nxt - time.time():.0f}s from now")

    # ---- a one-shot reminder is marked done, not repeated -------------------
    said.clear()
    s2 = fresh(announce)
    s2.add("stretch", time.time() - 60, "none")
    asyncio.run(tick(s2))
    check("a one-shot reminder fires once", len(said) == 1, said)
    st = s2.db.execute("SELECT status FROM tasks WHERE text='stretch'").fetchone()[0]
    check("...and is marked done", st == "done", st)

    # ---- THE NIGHT OF 2026-08-31: the write fails ---------------------------
    # Exactly the real conditions: the row is due, and the database refuses the
    # update that would move it on. The old code announced on every tick.
    said.clear()
    s3 = fresh(announce)
    s3.add("wear my retainers", time.time() - 600, "daily")   # under the staleness bar

    real_db = s3.db

    class RefusesUpdates:
        """The connection as it behaved that night: reads fine, will not write."""

        def __init__(self, inner):
            self._inner = inner

        def execute(self, sql, *a, **k):
            if sql.strip().upper().startswith("UPDATE TASKS"):
                raise Exception("database disk image is malformed")
            return self._inner.execute(sql, *a, **k)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    s3.db = RefusesUpdates(real_db)
    asyncio.run(tick(s3, n=30))          # 30 ticks = five minutes of real time
    check("a reminder whose schedule cannot be written stays SILENT",
          len(said) == 0,
          f"it sent {len(said)} messages; the old code sent one per tick")

    # and it must not come straight back on the next tick either
    asyncio.run(tick(s3, n=30))
    s3.db = real_db
    check("...and does not resume on the next tick", len(said) == 0,
          f"{len(said)} after a further 30 ticks")

    # ---- the announce failing must not repeat it either ---------------------
    # The message may genuinely fail to send. That is not a reason to retry it
    # forever - the schedule has already moved on.
    tried = []

    async def refuses(t, **kw):
        tried.append(t)
        raise RuntimeError("telegram is down")

    s4 = fresh(refuses)
    s4.add("wear my retainers", time.time() - 600, "daily")   # under the staleness bar
    asyncio.run(tick(s4, n=20))
    check("a failing announce is attempted once, not per tick",
          len(tried) == 1, f"{len(tried)} attempts")

    # ---- and a reminder whose moment has long passed -----------------------
    # He was told "time to wear your retainers" at 6:17 in the MORNING on
    # 2026-09-02. It had been due at nine the night before; its advance had
    # failed, and both existing guards live in memory, so the restart that came
    # with a deploy cleared them and the scheduler said it nine hours late.
    # A reminder that late is not a reminder.
    late = []

    async def note_late(t, **kw):
        late.append(t)

    s5 = fresh(note_late)
    nine_hours = time.time() - 9 * 3600
    tid5 = s5.add("wear my retainers", nine_hours, "daily")
    asyncio.run(tick(s5, n=4))
    check("a recurring reminder nine hours late is NOT announced", not late, late)
    row = s5.db.execute("SELECT due_ts, status FROM tasks WHERE id=?", (tid5,)).fetchone()
    check("...it is moved to its next occurrence instead",
          row and row[0] > time.time(), row)
    check("...and stays pending, so tonight still happens",
          row and row[1] == "pending", row)

    # A ONE-OFF is different: he asked once, nobody told him, and dropping it
    # silently would be worse than telling him late.
    late.clear()
    s6 = fresh(note_late)
    s6.add("collect the parcel", time.time() - 9 * 3600, "none")
    asyncio.run(tick(s6, n=4))
    check("a ONE-OFF reminder is still delivered however late", len(late) == 1, late)

    # And an on-time one is untouched by the new guard.
    late.clear()
    s7 = fresh(note_late)
    s7.add("wear my retainers", time.time() - 60, "daily")
    asyncio.run(tick(s7, n=4))
    check("a reminder a minute late is still announced", len(late) == 1, late)

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
