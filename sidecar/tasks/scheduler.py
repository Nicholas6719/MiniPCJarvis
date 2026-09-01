"""Task scheduler: reminders and recurring routines, persisted in SQLite.

Due tasks are announced out loud (proactive speech) and emitted as events.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time

from events import bus
from memory.store import memory  # reuse the open SQLite connection's db file

log = logging.getLogger("jarvis.tasks")

# How long a task whose schedule could not be written stays silent. Long on
# purpose: the failure mode this exists for is a database that stays broken for
# hours, and retrying a reminder every ten seconds is what produced ~700
# overnight messages. Missing a reminder is recoverable; a flood is not.
_BLOCKED_RETRY_S = 3600.0

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_ts REAL NOT NULL,
    due_ts REAL NOT NULL,
    text TEXT NOT NULL,
    recurrence TEXT NOT NULL DEFAULT 'none',  -- none|daily|weekdays|weekly
    status TEXT NOT NULL DEFAULT 'pending'    -- pending|done|cancelled
);
"""


class Scheduler:
    def __init__(self) -> None:
        self.db = memory.db  # same connection (thread-safe usage is serialized here)
        self.db.executescript(_SCHEMA)
        self.db.commit()
        self._task: asyncio.Task | None = None
        self.announce = None  # set by orchestrator wiring
        # tid -> the due_ts already announced, so one due time is one message
        self._fired: dict[int, float] = {}
        # tid -> time before which it must stay silent (its schedule would not write)
        self._blocked: dict[int, float] = {}

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    def stop(self) -> None:
        if self._task:
            self._task.cancel()

    # ---------- CRUD ----------

    def add(self, text: str, due_ts: float, recurrence: str = "none") -> int:
        cur = self.db.execute(
            "INSERT INTO tasks (created_ts, due_ts, text, recurrence) VALUES (?,?,?,?)",
            (time.time(), due_ts, text, recurrence))
        self.db.commit()
        return cur.lastrowid

    def list_pending(self) -> list[dict]:
        rows = self.db.execute(
            "SELECT id, due_ts, text, recurrence FROM tasks "
            "WHERE status='pending' ORDER BY due_ts LIMIT 50").fetchall()
        return [{"id": r[0],
                 "due": dt.datetime.fromtimestamp(r[1]).strftime("%Y-%m-%d %H:%M"),
                 "text": r[2], "recurrence": r[3]} for r in rows]

    def cancel(self, task_id: int) -> bool:
        cur = self.db.execute(
            "UPDATE tasks SET status='cancelled' WHERE id=? AND status='pending'",
            (task_id,))
        self.db.commit()
        return cur.rowcount > 0

    # ---------- loop ----------

    async def _loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(10)
                now = time.time()
                rows = self.db.execute(
                    "SELECT id, due_ts, text, recurrence FROM tasks "
                    "WHERE status='pending' AND due_ts <= ?", (now,)).fetchall()
                for tid, due_ts, text, recurrence in rows:
                    if self._suppressed(tid, due_ts, now):
                        continue
                    await self._fire(tid, due_ts, text, recurrence)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("scheduler tick failed")

    def _suppressed(self, tid: int, due_ts: float, now: float) -> bool:
        """Whether this task must NOT be announced right now.

        Two independent guards, because the one that mattered was missing.
        On 2026-08-31 a failed write elsewhere left SQLite's write lock held
        (an audit insert that caught its error without rolling back), so
        `UPDATE tasks SET due_ts=?` could not commit. The row stayed `pending`
        with a due time in the past, and this loop - which ticks every ten
        seconds - re-announced the same 9 p.m. retainer reminder six times a
        minute all night. Nicholas shut JARVIS
        down after about fifty messages and woke up to roughly seven hundred.

        The write failing was the fault. The harm was that nothing treated
        "I could not record that I sent this" as a reason to STOP sending it.
        """
        if self._fired.get(tid) == due_ts:
            return True                     # already announced for this due time
        until = self._blocked.get(tid, 0.0)
        if until > now:
            return True                     # its schedule could not be written
        return False

    def _advance(self, tid: int, due_ts: float, recurrence: str) -> bool:
        """Move the task on BEFORE it is announced. False if that did not stick.

        Order matters and it is the whole fix: claim the slot, then speak. If
        the claim cannot be committed, nothing is said at all - a missed
        reminder is a small failure, and an unstoppable one is not.
        """
        try:
            if recurrence == "none":
                self.db.execute("UPDATE tasks SET status='done' WHERE id=?", (tid,))
            else:
                nxt = _next_occurrence(due_ts, recurrence)
                self.db.execute("UPDATE tasks SET due_ts=? WHERE id=?", (nxt, tid))
            self.db.commit()
            return True
        except Exception:
            log.exception("could not advance task %s; it will NOT be announced", tid)
            try:
                self.db.rollback()   # never leave the write lock held on the shared conn
            except Exception:
                log.warning('rollback failed after a task write', exc_info=True)
            return False

    async def _fire(self, tid: int, due_ts: float, text: str, recurrence: str) -> None:
        # Mark it fired in memory first, so that even a crash between here and
        # the commit cannot produce a second copy in this process.
        self._fired[tid] = due_ts
        if not self._advance(tid, due_ts, recurrence):
            # The database would not take the update. Stay quiet and try again
            # much later, rather than every ten seconds forever.
            self._blocked[tid] = time.time() + _BLOCKED_RETRY_S
            return
        log.info("task due: %s", text)
        await bus.emit("task_due", task_id=tid, text=text)
        if self.announce is not None:
            try:
                # The RAW text, and a key tied to this task. The announcer puts
                # it into JARVIS's own words, so the sentence differs night to
                # night - which means the sentence cannot be what delivery
                # de-duplicates on. The task can.
                await self.announce(text, key=f"task:{tid}")
            except Exception:
                log.exception("announce failed")


def _next_occurrence(due_ts: float, recurrence: str) -> float:
    d = dt.datetime.fromtimestamp(due_ts)
    now = dt.datetime.now()
    while d <= now:
        if recurrence == "daily":
            d += dt.timedelta(days=1)
        elif recurrence == "weekly":
            d += dt.timedelta(weeks=1)
        elif recurrence == "weekdays":
            d += dt.timedelta(days=1)
            while d.weekday() >= 5:
                d += dt.timedelta(days=1)
        else:
            d += dt.timedelta(days=1)
    return d.timestamp()


scheduler = Scheduler()
