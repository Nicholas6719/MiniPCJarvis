"""Facts with a shelf life: where he is, what his watch says.

The handoff calls for phone-derived data to be stored "as volatile/timestamped,
same durability tier as a stock price". A stock price is never stored at all — it
is fetched live and spoken with an as-of time — but a location fix and a heart
rate arrive when the PHONE decides to send them, so they have to land somewhere.
This is that somewhere, and it keeps the property that matters: **nothing is ever
read back without its age.**

Deliberately not the `memories` table. Memories are things he told JARVIS to
remember and they are true until he corrects them. These expire: a location fix
from four hours ago is not where he is, and answering with it as though it were
current is precisely the failure this module exists to prevent. Every read
returns `age_s`, and `fresh()` returns nothing at all once past its window.

Deliberately not the `tasks` table either, and not `facts` — those are reminders
and cached research answers respectively. One table, one meaning.

Every write commits immediately. An uncommitted write here would hold SQLite's
single write lock for the life of the process and kill every turn, which has
happened twice in this project and is not going to happen a third time.
"""
from __future__ import annotations

import json
import logging
import time

from config import open_db

log = logging.getLogger("jarvis.volatile")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS volatile_facts (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL,          -- JSON
    ts    REAL NOT NULL,
    source TEXT NOT NULL DEFAULT ''
);
"""

_db = None


def _conn():
    global _db
    if _db is None:
        _db = open_db()
        _db.executescript(_SCHEMA)
        _db.commit()
    return _db


def put(key: str, value: dict, source: str = "") -> bool:
    """Store the newest reading for `key`. Never raises — this is called from the
    Telegram poller, and a bad payload must not take the channel down."""
    try:
        db = _conn()
        db.execute(
            "INSERT INTO volatile_facts (key, value, ts, source) VALUES (?,?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, ts=excluded.ts, "
            "source=excluded.source",
            (key, json.dumps(value), time.time(), source))
        db.commit()                     # immediately; see the module docstring
        return True
    except Exception:
        try:
            _conn().rollback()          # never leave the write lock held
        except Exception:
            log.debug("volatile rollback failed", exc_info=True)
        log.exception("volatile put failed for %r", key)
        return False


def get(key: str) -> dict | None:
    """The stored reading plus how old it is, or None. Age is not optional."""
    try:
        row = _conn().execute(
            "SELECT value, ts, source FROM volatile_facts WHERE key=?", (key,)).fetchone()
    except Exception:
        log.exception("volatile get failed for %r", key)
        return None
    if not row:
        return None
    try:
        value = json.loads(row[0])
    except Exception:
        log.warning("volatile %r holds unreadable JSON", key)
        return None
    age = max(0.0, time.time() - float(row[1]))
    return {"value": value, "ts": float(row[1]), "age_s": age,
            "age_minutes": round(age / 60.0, 1), "source": row[2]}


def fresh(key: str, max_age_minutes: float) -> dict | None:
    """The reading only if it is still worth believing. Stale returns None rather
    than a value the caller might use without checking — the whole point."""
    got = get(key)
    if not got or got["age_minutes"] > max_age_minutes:
        return None
    return got


def forget(key: str) -> bool:
    try:
        db = _conn()
        db.execute("DELETE FROM volatile_facts WHERE key=?", (key,))
        db.commit()
        return True
    except Exception:
        log.exception("volatile forget failed for %r", key)
        return False


def spoken_age(age_minutes: float) -> str:
    """"as of two minutes ago" — how a person says it, for a spoken answer."""
    if age_minutes < 1.5:
        return "just now"
    if age_minutes < 60:
        return f"{int(round(age_minutes))} minutes ago"
    hours = age_minutes / 60.0
    if hours < 2:
        return "about an hour ago"
    if hours < 24:
        return f"about {int(round(hours))} hours ago"
    days = hours / 24.0
    return "yesterday" if days < 2 else f"{int(round(days))} days ago"
