"""What the desk remembers: every transmission it saw (scanner_hits), the
incidents it built from them (scanner_incidents) and every time it sent him
something (scanner_sent).

Every write commits or rolls back before it returns. An open write
transaction on this shared file is what killed every turn on 2026-08-31 and
let a reminder fire 2,600 times; a failed write here costs a row, never the
assistant.
"""
from __future__ import annotations

import json
import logging
import time

from config import open_db

log = logging.getLogger("jarvis.scanner")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS scanner_hits (
    id INTEGER PRIMARY KEY,          -- the console's own hit id
    ts REAL NOT NULL,                -- start of the transmission
    end_ts REAL,
    duration REAL,
    channel INTEGER,
    name TEXT,                       -- the channel label he typed
    freq REAL,
    town TEXT,
    agency TEXT,
    quality TEXT,
    audio_path TEXT,
    transcript TEXT,
    asr_conf REAL,
    incident_id INTEGER
);
CREATE INDEX IF NOT EXISTS scanner_hits_ts ON scanner_hits(ts);
CREATE INDEX IF NOT EXISTS scanner_hits_name ON scanner_hits(name, ts);
CREATE TABLE IF NOT EXISTS scanner_incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    first_ts REAL NOT NULL,
    last_ts REAL NOT NULL,
    town TEXT,
    miles REAL,
    agency TEXT,
    channel TEXT,
    family TEXT,
    kind TEXT,
    label TEXT,
    severity INTEGER DEFAULT 0,
    types TEXT DEFAULT '[]',
    address TEXT DEFAULT '',
    street TEXT DEFAULT '',
    roads TEXT DEFAULT '[]',
    units TEXT DEFAULT '[]',
    said TEXT DEFAULT '[]',          -- the last few transcripts: [{ts, text, hit}]
    summary TEXT DEFAULT '',         -- the model's one line, when there is one
    hits INTEGER DEFAULT 0,
    burst INTEGER DEFAULT 0,
    status TEXT DEFAULT 'active',    -- active | over
    escalations TEXT DEFAULT '[]',
    tier TEXT DEFAULT '',            -- the highest tier he was actually sent
    delivered_ts REAL DEFAULT 0,
    decided TEXT DEFAULT '',         -- why it was or was not sent, last decision
    pending TEXT DEFAULT '',         -- a tier waiting on delivery's repeat window
    msg_id INTEGER DEFAULT 0,        -- his Telegram message for it, edited as details arrive
    details TEXT DEFAULT '[]',       -- facts heard: vehicles, injuries, transport...
    edited_ts REAL DEFAULT 0,        -- when that message was last brought up to date
    clip_mid INTEGER DEFAULT 0,      -- the recording threaded under it
    more_mid INTEGER DEFAULT 0,      -- the last "tell me more" answer about it
    paged TEXT DEFAULT ''            -- the two-tone page heard with it ("905/770"), if any
);
CREATE INDEX IF NOT EXISTS scanner_incidents_last ON scanner_incidents(last_ts);
CREATE TABLE IF NOT EXISTS scanner_sent (
    ts REAL NOT NULL,
    incident_id INTEGER,
    tier TEXT,
    outcome TEXT,
    why TEXT
);
CREATE INDEX IF NOT EXISTS scanner_sent_ts ON scanner_sent(ts);
"""
_JSON = ("types", "roads", "units", "said", "escalations", "details")
_INC_COLS = ("first_ts", "last_ts", "town", "miles", "agency", "channel", "family", "kind", "label",
             "severity", "types", "address", "street", "roads", "units", "said", "summary", "hits",
             "burst", "status", "escalations", "tier", "delivered_ts", "decided", "pending",
             "msg_id", "details", "edited_ts", "clip_mid", "more_mid", "paged")
# columns added after the first install: (name, definition) - see _migrate
_ADDED = (("msg_id", "INTEGER DEFAULT 0"), ("details", "TEXT DEFAULT '[]'"), ("edited_ts", "REAL DEFAULT 0"),
          ("clip_mid", "INTEGER DEFAULT 0"), ("more_mid", "INTEGER DEFAULT 0"),
          ("paged", "TEXT DEFAULT ''"))
_HIT_COLS = ("id", "ts", "end_ts", "duration", "channel", "name", "freq", "town", "agency", "quality",
             "audio_path", "transcript", "asr_conf", "incident_id")

_db = None


def conn():
    global _db
    if _db is None:
        db = open_db()
        try:
            db.executescript(_SCHEMA)
            db.commit()
            _migrate(db)
        except Exception:
            _rollback(db)
            raise
        _db = db
    return _db


def _migrate(db) -> list[str]:
    """Columns this build needs that an older install's table lacks.
    CREATE TABLE IF NOT EXISTS leaves an existing table as it was, so a new
    column has to be added by hand - once, committed. Returns what was added."""
    have = {r[1] for r in db.execute("PRAGMA table_info(scanner_incidents)").fetchall()}
    added = []
    for name, decl in _ADDED:
        if name not in have:
            db.execute(f"ALTER TABLE scanner_incidents ADD COLUMN {name} {decl}")
            added.append(name)
    if added:
        db.commit()
        log.info("scanner store: added %s to scanner_incidents", ", ".join(added))
    return added


def _rollback(db) -> None:
    try:
        db.rollback()
    except Exception:
        log.debug("scanner rollback failed", exc_info=True)


def _write(sql: str, params: tuple = ()) -> int | None:
    """Run one write and commit it. Returns lastrowid (or 0), None on failure."""
    try:
        db = conn()
    except Exception:
        log.exception("scanner store unavailable")
        return None
    try:
        cur = db.execute(sql, params)
        db.commit()
        return int(cur.lastrowid or 0)
    except Exception:
        _rollback(db)                     # never leave the write lock held
        log.exception("scanner store write failed: %s", sql.split("(")[0][:60])
        return None


def _rows(sql: str, params: tuple = ()) -> list[tuple]:
    try:
        return conn().execute(sql, params).fetchall()
    except Exception:
        log.exception("scanner store read failed")
        return []


# ------------------------------------------------------------------ hits
def log_hit(h: dict) -> bool:
    """A finished transmission, once. Seeing the same transmission again is a
    no-op; a DIFFERENT transmission under an old id (his console's log was
    reset and its ids started again) replaces the stale row."""
    q = h.get("quality")
    vals = (int(h["id"]), float(h["ts"]), h.get("end_ts"), h.get("duration"), h.get("channel"),
            h.get("name") or "", h.get("freq"), h.get("town") or "", h.get("agency") or "",
            json.dumps(q) if isinstance(q, dict) else (q or None), h.get("audio_path") or "",
            h.get("transcript") or None, h.get("asr_conf"), h.get("incident_id"))
    sets = ", ".join(f"{c}=excluded.{c}" for c in _HIT_COLS if c != "id")
    return _write(f"INSERT INTO scanner_hits ({', '.join(_HIT_COLS)}) "
                  f"VALUES ({', '.join('?' * len(_HIT_COLS))}) "
                  f"ON CONFLICT(id) DO UPDATE SET {sets} WHERE abs(scanner_hits.ts - excluded.ts) > 1", vals) is not None


def set_transcript(hit_id: int, text: str, conf: float | None, incident_id: int | None = None) -> bool:
    return _write("UPDATE scanner_hits SET transcript=?, asr_conf=?, incident_id=COALESCE(?, incident_id) "
                  "WHERE id=?", (text, conf, incident_id, int(hit_id))) is not None


def _hit(row: tuple) -> dict:
    d = dict(zip(_HIT_COLS, row))
    try:
        d["quality"] = json.loads(d["quality"]) if d.get("quality") else None
    except (TypeError, ValueError):
        d["quality"] = None
    return d


def hit(hit_id: int) -> dict | None:
    r = _rows(f"SELECT {', '.join(_HIT_COLS)} FROM scanner_hits WHERE id=?", (int(hit_id),))
    return _hit(r[0]) if r else None


def last_hit() -> dict | None:
    r = _rows(f"SELECT {', '.join(_HIT_COLS)} FROM scanner_hits ORDER BY ts DESC, id DESC LIMIT 1")
    return _hit(r[0]) if r else None


def hits_since(ts: float, town: str = "", until: float | None = None) -> list[dict]:
    sql = f"SELECT {', '.join(_HIT_COLS)} FROM scanner_hits WHERE ts >= ?"
    args: list = [float(ts)]
    if until is not None:
        sql += " AND ts < ?"
        args.append(float(until))
    if town:
        sql += " AND town = ?"
        args.append(town)
    return [_hit(r) for r in _rows(sql + " ORDER BY ts", tuple(args))]


def count_hits(name: str, since: float, until: float | None = None) -> int:
    if until is None:
        r = _rows("SELECT COUNT(*) FROM scanner_hits WHERE name=? AND ts >= ?", (name, float(since)))
    else:
        r = _rows("SELECT COUNT(*) FROM scanner_hits WHERE name=? AND ts >= ? AND ts < ?",
                  (name, float(since), float(until)))
    return int(r[0][0]) if r else 0


def baseline(name: str, hour: int, before: float, window_s: float, days: int = 14) -> tuple[float, int]:
    """(transmissions per window, days observed) on this channel at this hour
    of the day over the last `days` days, counting only days the desk was
    actually listening at that hour - a day JARVIS was off is not a quiet day."""
    since = before - days * 86400
    hour_sql = "CAST(strftime('%H', ts, 'unixepoch', 'localtime') AS INTEGER) = ?"
    n = _rows(f"SELECT COUNT(*) FROM scanner_hits WHERE name=? AND ts >= ? AND ts < ? AND {hour_sql}",
              (name, since, before, int(hour)))
    d = _rows(f"SELECT COUNT(DISTINCT date(ts, 'unixepoch', 'localtime')) FROM scanner_hits "
              f"WHERE ts >= ? AND ts < ? AND {hour_sql}", (since, before, int(hour)))
    count = int(n[0][0]) if n else 0
    observed = int(d[0][0]) if d else 0
    per_window = count / max(1, observed) / max(1.0, 3600.0 / window_s)
    return per_window, observed


def max_hit_id() -> int:
    r = _rows("SELECT MAX(id) FROM scanner_hits")
    return int(r[0][0] or 0) if r else 0


# ------------------------------------------------------------------ incidents
def _inc(row: tuple) -> dict:
    d = dict(zip(("id",) + _INC_COLS, row))
    for k in _JSON:
        try:
            d[k] = json.loads(d.get(k) or "[]")
        except (TypeError, ValueError):
            d[k] = []
    return d


def _enc(fields: dict) -> dict:
    out = {}
    for k, v in fields.items():
        if k not in _INC_COLS:
            continue
        out[k] = json.dumps(v) if k in _JSON else v
    return out


def create_incident(fields: dict) -> int | None:
    f = _enc(fields)
    cols = list(f)
    rid = _write(f"INSERT INTO scanner_incidents ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                 tuple(f[c] for c in cols))
    return rid or None


def update_incident(iid: int, fields: dict) -> bool:
    f = _enc(fields)
    if not f:
        return True
    sets = ", ".join(f"{c}=?" for c in f)
    return _write(f"UPDATE scanner_incidents SET {sets} WHERE id=?", tuple(f.values()) + (int(iid),)) is not None


def incident(iid: int) -> dict | None:
    r = _rows(f"SELECT id, {', '.join(_INC_COLS)} FROM scanner_incidents WHERE id=?", (int(iid),))
    return _inc(r[0]) if r else None


def incidents_since(ts: float, town: str = "") -> list[dict]:
    sql = f"SELECT id, {', '.join(_INC_COLS)} FROM scanner_incidents WHERE last_ts >= ?"
    args: list = [float(ts)]
    if town:
        sql += " AND town = ?"
        args.append(town)
    return [_inc(r) for r in _rows(sql + " ORDER BY last_ts DESC", tuple(args))]


def open_on_channel(channel: str, since: float) -> list[dict]:
    """Active incidents on one channel heard from since `since`, newest first."""
    return [_inc(r) for r in _rows(
        f"SELECT id, {', '.join(_INC_COLS)} FROM scanner_incidents WHERE channel = ? AND status = 'active' "
        "AND last_ts >= ? ORDER BY last_ts DESC", (channel or "", float(since)))]


def incident_by_message(mid: int, since: float) -> dict | None:
    """The call one of his Telegram messages is about - its own message, the
    recording under it, or a "tell me more" answer - heard from since `since`."""
    if not mid:
        return None
    r = _rows(f"SELECT id, {', '.join(_INC_COLS)} FROM scanner_incidents WHERE last_ts >= ? AND "
              "(msg_id = ? OR clip_mid = ? OR more_mid = ?) ORDER BY id DESC LIMIT 1",
              (float(since), int(mid), int(mid), int(mid)))
    return _inc(r[0]) if r else None


def pending_incidents(since: float) -> list[dict]:
    return [_inc(r) for r in _rows(
        f"SELECT id, {', '.join(_INC_COLS)} FROM scanner_incidents WHERE pending != '' AND last_ts >= ?",
        (float(since),))]


# ------------------------------------------------------------------ what reached him
def record_sent(iid: int, tier: str, outcome: str, why: str, ts: float | None = None) -> bool:
    return _write("INSERT INTO scanner_sent (ts, incident_id, tier, outcome, why) VALUES (?,?,?,?,?)",
                  (ts or time.time(), int(iid), tier, outcome, why[:300])) is not None


def last_sent_ts(iid: int) -> float:
    """When the desk last tried to send this incident (any outcome); 0 if never."""
    r = _rows("SELECT MAX(ts) FROM scanner_sent WHERE incident_id=?", (int(iid),))
    return float(r[0][0] or 0.0) if r else 0.0


def count_since(since: float, town: str = "", until: float | None = None) -> tuple[int, int]:
    """(transmissions, of which transcribed) since `since`, optionally in one town."""
    sql = "SELECT COUNT(*), SUM(CASE WHEN transcript IS NOT NULL AND transcript != '' THEN 1 ELSE 0 END) " \
          "FROM scanner_hits WHERE ts >= ?"
    args: list = [float(since)]
    if until is not None:
        sql += " AND ts < ?"
        args.append(float(until))
    if town:
        sql += " AND town = ?"
        args.append(town)
    r = _rows(sql, tuple(args))
    return (int(r[0][0] or 0), int(r[0][1] or 0)) if r else (0, 0)


def sent_count(tier: str, since: float) -> int:
    """How many times he was actually reached at this tier (spoken or on his phone)."""
    r = _rows("SELECT COUNT(*) FROM scanner_sent WHERE tier=? AND ts >= ? AND outcome IN ('spoken','telegram')",
              (tier, float(since)))
    return int(r[0][0]) if r else 0


def prune(now: float | None = None, keep_days: int = 30) -> None:
    """Bounded: a month of transmissions is plenty for baselines and briefs."""
    cutoff = (now or time.time()) - keep_days * 86400
    _write("DELETE FROM scanner_hits WHERE ts < ?", (cutoff,))
    _write("DELETE FROM scanner_incidents WHERE last_ts < ?", (cutoff,))
    _write("DELETE FROM scanner_sent WHERE ts < ?", (cutoff,))
