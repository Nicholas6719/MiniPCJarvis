"""Projects and how far along they are.

Deliberately NOT the existing `tasks` table: that one is reminders and errands
("remind me at nine to take my retainers out"). Two unrelated features writing
the same rows is how a nightly reminder ends up in a project list. These get
`projects` and `project_steps` of their own in the same jarvis.db.

The estimate is not new machine learning. It is structured data — when the
project started, what fraction is done, how recently it moved — handed to the
model already running, which reasons about it the way a person would. Where the
arithmetic is honest (a steady rate over elapsed time) this does it in code and
says so; where it is a judgement call it says that instead of inventing a date.

Every write commits immediately. An uncommitted write holds SQLite's single
write lock for the life of the process and kills every subsequent turn; that has
happened twice here and will not happen again through this module.
"""
from __future__ import annotations

import datetime as dt
import logging
import time

from config import open_db
from tools.registry import Risk, Tool, registry

log = logging.getLogger("jarvis.tools.projects")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE COLLATE NOCASE,
    created_ts REAL NOT NULL,
    updated_ts REAL NOT NULL,
    percent    INTEGER NOT NULL DEFAULT 0,
    status     TEXT NOT NULL DEFAULT 'active'      -- active | done
);
CREATE TABLE IF NOT EXISTS project_steps (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    ts         REAL NOT NULL,
    note       TEXT NOT NULL DEFAULT '',
    percent    INTEGER
);
CREATE INDEX IF NOT EXISTS idx_steps_project ON project_steps(project_id, ts);
"""

_db = None


def _conn():
    global _db
    if _db is None:
        _db = open_db()
        _db.executescript(_SCHEMA)
        _db.commit()
    return _db


def _find(name: str):
    return _conn().execute(
        "SELECT id, name, created_ts, updated_ts, percent, status FROM projects "
        "WHERE name = ? COLLATE NOCASE", (name.strip(),)).fetchone()


def _row(r) -> dict:
    started = dt.datetime.fromtimestamp(r[2])
    return {"name": r[1], "percent": r[4], "status": r[5],
            "started": started.strftime("%Y-%m-%d"),
            "days_running": max(0, int((time.time() - r[2]) // 86400)),
            "days_since_update": max(0, int((time.time() - r[3]) // 86400))}


async def list_projects(status: str = "active") -> dict:
    """What he is working on, and how far along."""
    try:
        q = ("SELECT id, name, created_ts, updated_ts, percent, status FROM projects "
             + ("" if status == "all" else "WHERE status = ? ")
             + "ORDER BY updated_ts DESC")
        rows = (_conn().execute(q).fetchall() if status == "all"
                else _conn().execute(q, (status,)).fetchall())
    except Exception as e:
        log.exception("list_projects failed")
        return {"error": f"I couldn't read the project list: {e}"}
    return {"projects": [_row(r) for r in rows], "count": len(rows)}


async def log_progress(project: str, note: str = "", percent: int | None = None) -> dict:
    """Record a step. Creates the project on first mention — he should not have
    to declare a project before he can say he made progress on it."""
    name = (project or "").strip()
    if not name:
        return {"error": "which project, sir?"}
    if percent is not None:
        try:
            percent = max(0, min(100, int(percent)))
        except (TypeError, ValueError):
            return {"error": "that percentage didn't make sense"}
    db = _conn()
    try:
        now = time.time()
        existing = _find(name)
        created = False
        if not existing:
            db.execute("INSERT INTO projects (name, created_ts, updated_ts, percent) "
                       "VALUES (?,?,?,?)", (name, now, now, percent or 0))
            created = True
            existing = _find(name)
        pid = existing[0]
        db.execute("INSERT INTO project_steps (project_id, ts, note, percent) "
                   "VALUES (?,?,?,?)", (pid, now, note or "", percent))
        if percent is not None:
            db.execute("UPDATE projects SET percent=?, updated_ts=?, "
                       "status=CASE WHEN ?>=100 THEN 'done' ELSE status END WHERE id=?",
                       (percent, now, percent, pid))
        else:
            db.execute("UPDATE projects SET updated_ts=? WHERE id=?", (now, pid))
        db.commit()
    except Exception as e:
        try:
            db.rollback()               # never leave the write lock held
        except Exception:
            log.debug("projects rollback failed", exc_info=True)
        log.exception("log_progress failed")
        return {"error": f"I couldn't record that: {e}"}
    r = _find(name)
    out = _row(r)
    out["created"] = created
    out["logged"] = note or (f"{percent}%" if percent is not None else "progress")
    return out


async def estimate_completion(project: str) -> dict:
    """When it will be done, from elapsed-versus-remaining.

    Returns the ARITHMETIC and the evidence for it rather than a bare date. Where
    there is not enough history to project honestly it says so — a confident
    finish date drawn from one data point is exactly the kind of plausible
    invention that costs trust.
    """
    name = (project or "").strip()
    r = _find(name)
    if not r:
        return {"error": f"I don't have a project called {name}"}
    pid, percent, created_ts = r[0], r[4], r[2]
    steps = _conn().execute(
        "SELECT ts, note, percent FROM project_steps WHERE project_id=? ORDER BY ts",
        (pid,)).fetchall()
    base = _row(r)
    base["updates"] = len(steps)
    if r[5] == "done" or percent >= 100:
        return {**base, "estimate": "already finished"}

    marks = [(s[0], s[2]) for s in steps if s[2] is not None]
    if percent <= 0 or len(marks) < 2:
        return {**base, "estimate": None,
                "why": "not enough history to project a finish date — "
                       f"{len(marks)} progress mark(s) recorded"}

    # Rate from the FIRST to the LAST measured mark, not from creation: a project
    # created weeks before the first real work would otherwise look glacial.
    (t0, p0), (t1, p1) = marks[0], marks[-1]
    gained, elapsed_days = p1 - p0, (t1 - t0) / 86400.0
    if gained <= 0 or elapsed_days <= 0:
        return {**base, "estimate": None,
                "why": "no forward progress between the recorded marks"}
    per_day = gained / elapsed_days
    days_left = (100 - percent) / per_day
    finish = dt.datetime.now() + dt.timedelta(days=days_left)
    return {**base, "percent_per_day": round(per_day, 2),
            "days_remaining": round(days_left, 1),
            "estimate": finish.strftime("%Y-%m-%d"),
            "why": f"{gained}% over {round(elapsed_days, 1)} days between the first "
                   f"and last recorded mark"}


def register_all() -> None:
    registry.register(Tool(
        name="list_projects",
        description="List the user's tracked projects and how far along each one is.",
        parameters={"type": "object", "properties": {
            "status": {"type": "string", "enum": ["active", "done", "all"]}},
            "required": []},
        risk=Risk.SAFE, handler=list_projects, timeout=20))
    registry.register(Tool(
        name="log_progress",
        description="Record progress on a project — a note, a percentage, or both. "
                    "Creates the project if it is new.",
        parameters={"type": "object", "properties": {
            "project": {"type": "string"},
            "note": {"type": "string"},
            "percent": {"type": "integer", "minimum": 0, "maximum": 100}},
            "required": ["project"]},
        risk=Risk.LOW, handler=log_progress, timeout=20))
    registry.register(Tool(
        name="estimate_completion",
        description="Estimate when a tracked project will finish, from how fast it has "
                    "actually been moving. Says so plainly when there is too little "
                    "history to project.",
        parameters={"type": "object", "properties": {
            "project": {"type": "string"}}, "required": ["project"]},
        risk=Risk.SAFE, handler=estimate_completion, timeout=60))
