"""Task scheduler: reminders and recurring routines, persisted in SQLite.

Due tasks are announced out loud (proactive speech) and emitted as events.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time

from config import DB_PATH
from events import bus
from memory.store import memory  # reuse the open SQLite connection's db file

log = logging.getLogger("jarvis.tasks")

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
                    await self._fire(tid, due_ts, text, recurrence)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("scheduler tick failed")

    async def _fire(self, tid: int, due_ts: float, text: str, recurrence: str) -> None:
        log.info("task due: %s", text)
        await bus.emit("task_due", task_id=tid, text=text)
        if self.announce is not None:
            try:
                await self.announce(f"A reminder: {text}")
            except Exception:
                log.exception("announce failed")
        if recurrence == "none":
            self.db.execute("UPDATE tasks SET status='done' WHERE id=?", (tid,))
        else:
            nxt = _next_occurrence(due_ts, recurrence)
            self.db.execute("UPDATE tasks SET due_ts=? WHERE id=?", (nxt, tid))
        self.db.commit()


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
