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
-- One row per time a reminder actually went off (2026-11-07): whether he said
-- he did it (the Telegram "Done", or telling JARVIS), and the one nudge.
CREATE TABLE IF NOT EXISTS reminder_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL,
    text TEXT NOT NULL,
    due_ts REAL NOT NULL,
    fired_ts REAL NOT NULL,
    done_ts REAL,
    done_via TEXT,
    nudged_ts REAL
);
"""

# The nudge: once, quietly, if the Done never came.
NUDGE_AFTER_S = 30 * 60
# ...and never for a reminder that went off long ago (a restart after a day
# away must not nudge him about last night's retainers).
NUDGE_WINDOW_S = 3 * 3600


class Scheduler:
    # How late a RECURRING reminder may be and still be worth saying. An hour is
    # generous for "wear your retainers at nine"; nine hours, which is what he
    # actually got, is not a reminder, it is an apology.
    STALE_AFTER_S = 3600.0

    def __init__(self) -> None:
        self.db = memory.db  # same connection (thread-safe usage is serialized here)
        self.db.executescript(_SCHEMA)
        self.db.commit()
        self._task: asyncio.Task | None = None
        self.announce = None  # set by orchestrator wiring
        self.nudge = None     # set by main: says the one quiet nudge
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

    # ---------- what he did about them ----------

    def _write(self, sql: str, args: tuple) -> int | None:
        """One write, committed, or rolled back: the shared connection must
        never be left holding the write lock (the 2,600-message night)."""
        try:
            cur = self.db.execute(sql, args)
            self.db.commit()
            return cur.lastrowid if sql.lstrip().upper().startswith("INSERT") else cur.rowcount
        except Exception:
            log.exception("reminder log write failed")
            try:
                self.db.rollback()
            except Exception:
                log.warning("rollback failed after a reminder log write", exc_info=True)
            return None

    def log_occurrence(self, tid: int, text: str, due_ts: float) -> int | None:
        """This reminder just went off. Returns the occurrence id."""
        return self._write("INSERT INTO reminder_log (task_id, text, due_ts, fired_ts) VALUES (?,?,?,?)",
                           (int(tid), str(text), float(due_ts), time.time()))

    def mark_done(self, occ_id: int, via: str = "") -> bool:
        """He did it. True only the first time - a second press changes nothing."""
        n = self._write("UPDATE reminder_log SET done_ts=?, done_via=? WHERE id=? AND done_ts IS NULL",
                        (time.time(), str(via or ""), int(occ_id)))
        return bool(n)

    def open_occurrences(self, within_s: float = NUDGE_WINDOW_S, words: str = "") -> list[dict]:
        """Reminders that went off in the last `within_s` and are not done,
        newest first; `words` narrows them to the ones about that."""
        rows = self.db.execute(
            "SELECT id, task_id, text, fired_ts FROM reminder_log "
            "WHERE done_ts IS NULL AND fired_ts >= ? ORDER BY fired_ts DESC",
            (time.time() - within_s,)).fetchall()
        out = [{"id": r[0], "task_id": r[1], "text": r[2], "fired_ts": r[3]} for r in rows]
        if words:
            want = _content_words(words)
            out = [o for o in out if want & _content_words(o["text"])]
        return out

    def mark_latest_done(self, within_s: float = 2 * 3600, via: str = "",
                         words: str = "") -> int | None:
        """The newest open reminder (about `words`, if given) is done.
        Returns its occurrence id, or None when nothing was open."""
        for o in self.open_occurrences(within_s, words):
            if self.mark_done(o["id"], via):
                return o["id"]
        return None

    def due_nudges(self, now: float | None = None) -> list[dict]:
        """Occurrences past NUDGE_AFTER_S with no Done and no nudge yet, inside
        NUDGE_WINDOW_S."""
        now = now or time.time()
        rows = self.db.execute(
            "SELECT id, task_id, text, fired_ts FROM reminder_log "
            "WHERE done_ts IS NULL AND nudged_ts IS NULL AND fired_ts <= ? AND fired_ts >= ?",
            (now - NUDGE_AFTER_S, now - NUDGE_WINDOW_S)).fetchall()
        return [{"id": r[0], "task_id": r[1], "text": r[2], "fired_ts": r[3]} for r in rows]

    def claim_nudge(self, occ_id: int) -> bool:
        """Claim the one nudge BEFORE saying it. False if it could not be
        recorded (then it is not said: a missed nudge is fine, a repeated one
        is the thing this exists to prevent) or was already claimed."""
        return bool(self._write("UPDATE reminder_log SET nudged_ts=? WHERE id=? AND nudged_ts IS NULL",
                                (time.time(), int(occ_id))))

    def history(self, words: str, since_ts: float | None = None) -> dict:
        """What the record says about one reminder since `since_ts` (default a
        week ago): {text, fired, done, missed_days, done_days} for occurrences
        whose text shares a content word with `words`. Empty when none match."""
        since = since_ts if since_ts is not None else time.time() - 7 * 86400
        want = _content_words(words)
        rows = self.db.execute(
            "SELECT text, fired_ts, done_ts FROM reminder_log WHERE fired_ts >= ? ORDER BY fired_ts",
            (since,)).fetchall()
        hits = [r for r in rows if want and want & _content_words(r[0])]
        if not hits:
            return {}
        day = lambda ts: dt.datetime.fromtimestamp(ts).strftime("%A")  # noqa: E731
        return {"text": hits[-1][0], "fired": len(hits),
                "done": sum(1 for r in hits if r[2]),
                "done_days": [day(r[1]) for r in hits if r[2]],
                "missed_days": [day(r[1]) for r in hits if not r[2]]}

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
                    if self._too_late(tid, due_ts, now, recurrence):
                        continue
                    await self._fire(tid, due_ts, text, recurrence)
                await self._nudge_tick(now)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("scheduler tick failed")

    async def _nudge_tick(self, now: float) -> None:
        """One quiet nudge for a reminder with no Done after half an hour -
        claimed first, so it can never be said twice."""
        if self.nudge is None:
            return
        for o in self.due_nudges(now):
            if not self.claim_nudge(o["id"]):
                continue
            try:
                await self.nudge(o["text"], key=f"nudge:{o['id']}", occurrence=o["id"])
            except Exception:
                log.exception("nudge failed")

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

    def _too_late(self, tid: int, due_ts: float, now: float, recurrence: str) -> bool:
        """A RECURRING reminder whose moment has long passed is not worth saying.

        On 2026-09-02 he was told "time to wear your retainers" at 6:17 in the
        morning. The reminder was due at nine the previous night; its advance had
        failed, and both guards above are in MEMORY, so the restart that came
        with a deploy cleared them and the scheduler said it nine hours late.
        The write-lock fix stopped the flood; nothing stopped the staleness.

        Recurring tasks are skipped silently and moved to their next occurrence —
        the moment is gone, and it comes round again tonight anyway. One-off
        reminders are NOT skipped however late they are: he asked once, nobody
        told him, and quietly dropping it would be worse than telling him late.
        """
        late = now - due_ts
        if late <= self.STALE_AFTER_S:
            return False
        if (recurrence or "none") == "none":
            return False                      # a one-off still gets said
        if self._advance(tid, due_ts, recurrence):
            log.info("skipped a stale recurring reminder (%d): %.1f h late, "
                     "moved to its next occurrence", tid, late / 3600.0)
            return True
        # The advance did not stick, so the write lock is held again. Saying it
        # now would be the 2,600-message night starting over; stay quiet and let
        # the existing block do its work.
        return True

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
        # The occurrence, so a Done (or its absence) has somewhere to land.
        occ = self.log_occurrence(tid, text, due_ts)
        await bus.emit("task_due", task_id=tid, text=text)
        if self.announce is not None:
            try:
                # The RAW text, and a key tied to this task: delivery
                # de-duplicates on the task, never on the sentence.
                await self.announce(text, key=f"task:{tid}", occurrence=occ)
            except Exception:
                log.exception("announce failed")


_STOP = {"have", "has", "had", "take", "taken", "took", "taking", "wear", "wore", "worn", "wearing",
         "put", "puts", "your", "mine", "this", "that", "week", "today", "yesterday", "night",
         "last", "days", "much", "many", "times", "with", "been", "into", "done", "remind",
         "reminder", "reminders", "please", "always", "every", "daily", "morning", "evening"}


def _content_words(text: str) -> set[str]:
    """The words that say what a reminder is ABOUT: 'take my supplements' and
    'have I taken my supplements this week' share 'supplements'. Plurals are
    folded so 'retainer' finds 'retainers'."""
    import re as _re
    out = set()
    for w in _re.findall(r"[a-z]{4,}", (text or "").lower()):
        if w in _STOP:
            continue
        out.add(w[:-1] if w.endswith("s") and not w.endswith("ss") else w)
    return out


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
