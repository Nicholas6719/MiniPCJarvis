"""Things he asked to be told about, and will be, when they happen.

    "Tell me when that download finishes."
    "Let me know when the build is done."
    "Tell me when it's charged."

Everything else JARVIS does begins and ends inside one exchange. These do not:
a request that outlives the sentence which created it, and survives a restart,
because the whole point of handing something to him is that you stop holding it
yourself. A promise forgotten when the application closes is worse than one
never made.

Four kinds, each genuinely observable rather than guessed at:

    process     a named program exits -- builds, renders, installers, exports
    download    a file finishes arriving in his Downloads folder
    file        something appears in a folder
    battery     the charge reaches a level

They fire once and then they are gone. A watch that keeps announcing itself is a
broken timer, and one that has to be cancelled after it fires is a chore.

Kept in the JARVIS database (the same file volatile facts and memories use),
in their own table. Every write commits at once and every failed write rolls
back: an uncommitted write here would hold SQLite's single write lock for the
life of the process and kill every turn (volatile.py tells that story).
"""
from __future__ import annotations

import logging
import os
import time

from config import open_db

log = logging.getLogger("jarvis.standing")

KINDS = ("process", "download", "file", "battery")
# A watch nobody ever collects is a slow leak of promises. A week is long
# enough for anything worth waiting on and short enough that a forgotten one
# does not surface months later with no context.
MAX_AGE_S = 7 * 24 * 3600
# JARVIS must never report his own exit, nor the model server's, nor the
# shell's: "tell me when jarvis finishes" cannot be answered by the thing it
# would be about, and a watch on "server" must not arm on llama-server.
SELF = frozenset({"jarvis-sidecar.exe", "llama-server.exe", "jarvis.exe"})

_SCHEMA = """
CREATE TABLE IF NOT EXISTS standing_watches (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,
    target      TEXT NOT NULL DEFAULT '',
    folder      TEXT NOT NULL DEFAULT '',
    level       REAL NOT NULL DEFAULT 0,
    description TEXT NOT NULL DEFAULT '',
    armed       INTEGER NOT NULL DEFAULT 1,
    created_ts  REAL NOT NULL
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


def _rollback() -> None:
    try:
        _conn().rollback()
    except Exception:
        log.debug("standing rollback failed", exc_info=True)


def _row(r) -> dict:
    return {"id": r[0], "kind": r[1], "target": r[2], "folder": r[3], "level": r[4],
            "description": r[5], "armed": bool(r[6]), "created_ts": r[7]}


# ---------------------------------------------------------------- keeping them
def add(kind: str, target: str = "", *, folder: str = "", level: float = 0.0,
        description: str = "", armed: bool | None = None) -> dict:
    """Keep one. A process watch starts UNARMED: it has to see the thing
    running before it can see it stop, or "tell me when the build is done"
    fires at once when the build has not started yet -- true and useless."""
    if kind not in KINDS:
        raise ValueError(f"unknown watch kind {kind!r}")
    if armed is None:
        armed = kind != "process"
    try:
        db = _conn()
        cur = db.execute(
            "INSERT INTO standing_watches (kind, target, folder, level, description, armed, created_ts) "
            "VALUES (?,?,?,?,?,?,?)",
            (kind, (target or "").strip(), folder or "", float(level or 0), description or "",
             int(bool(armed)), time.time()))
        db.commit()
        log.info("standing watch #%s: %s", cur.lastrowid, description or kind)
        row = db.execute("SELECT id, kind, target, folder, level, description, armed, created_ts "
                         "FROM standing_watches WHERE id=?", (cur.lastrowid,)).fetchone()
        return _row(row)
    except Exception:
        _rollback()
        log.exception("could not keep a standing watch")
        raise


def all_watches() -> list[dict]:
    try:
        rows = _conn().execute(
            "SELECT id, kind, target, folder, level, description, armed, created_ts "
            "FROM standing_watches ORDER BY id").fetchall()
        return [_row(r) for r in rows]
    except Exception:
        log.exception("could not read the standing watches")
        return []


def cancel(query: str = "") -> list[dict]:
    """Drop matching watches. An empty query drops all of them."""
    q = (query or "").lower().strip()
    dropped = [w for w in all_watches()
               if not q or q in w["description"].lower() or q in w["target"].lower()
               or q in w["kind"]]
    _delete([w["id"] for w in dropped])
    return dropped


def _delete(ids: list[int]) -> None:
    if not ids:
        return
    try:
        db = _conn()
        db.executemany("DELETE FROM standing_watches WHERE id=?", [(i,) for i in ids])
        db.commit()
    except Exception:
        _rollback()
        log.exception("could not drop standing watches %s", ids)


def _arm(watch_id: int) -> None:
    try:
        db = _conn()
        db.execute("UPDATE standing_watches SET armed=1 WHERE id=?", (watch_id,))
        db.commit()
    except Exception:
        _rollback()
        log.exception("could not arm standing watch %s", watch_id)


def expire(now: float | None = None) -> int:
    now = now or time.time()
    old = [w["id"] for w in all_watches() if now - w["created_ts"] > MAX_AGE_S]
    if old:
        log.info("dropped %d standing watch(es) older than a week", len(old))
        _delete(old)
    return len(old)


# ---------------------------------------------------------------- checking them
def running_processes() -> set[str]:
    """Names of everything running, lowercase, with and without .exe -- minus
    JARVIS himself and the things he runs."""
    import psutil
    names: set[str] = set()
    for p in psutil.process_iter(["name"]):
        try:
            name = (p.info.get("name") or "").lower()
        except Exception:
            continue
        if not name or name in SELF:
            continue
        names.add(name)
        if name.endswith(".exe"):
            names.add(name[:-4])
    return names


def process_running(target: str, processes: set[str] | None = None) -> bool:
    """Case-insensitive, on the exe stem or any substring of it."""
    t = (target or "").lower().strip()
    if t.endswith(".exe"):
        t = t[:-4]
    if not t:
        return False
    if processes is None:
        processes = running_processes()
    return any(t == n or t in n for n in processes)


def _is_new_file(path: str, since: float) -> bool:
    try:
        st = os.stat(path)
    except OSError:
        return False
    # Written OR created since the watch was set: on Windows st_ctime is the
    # creation time, so a year-old file copied in still counts as appearing.
    # One second of grace: NTFS stamps files from the coarse system tick,
    # time.time() reads the precise clock, and a file written right after
    # the watch was set stamped up to 16 ms EARLIER than the watch (the gate
    # caught it on 2026-10-27). A file an hour old is still an hour old.
    return os.path.isfile(path) and max(st.st_mtime, st.st_ctime) >= float(since) - 1.0


def check(arrived: list[str] | None = None, *, battery=None,
          processes: set[str] | None = None) -> list[tuple[str, str]]:
    """Everything that has just come true, as (sentence, dedup key).

    Fired watches are deleted HERE, before they are returned, so a slow or
    failed announcement cannot cause the same thing to be reported twice.
    `battery` and `processes` are for the gate; live they are read from psutil.
    """
    expire()
    watches = all_watches()
    if not watches:
        return []
    out: list[tuple[str, str]] = []
    done: list[int] = []
    try:
        if processes is None and any(w["kind"] == "process" for w in watches):
            processes = running_processes()
        if battery is None and any(w["kind"] == "battery" for w in watches):
            import psutil
            battery = psutil.sensors_battery()
    except Exception:
        log.debug("could not read the machine for standing watches", exc_info=True)
        return []

    for w in watches:
        kind = w["kind"]
        try:
            if kind == "process":
                running = process_running(w["target"], processes or set())
                if running and not w["armed"]:
                    _arm(w["id"])            # seen it start: now it can finish
                elif w["armed"] and not running:
                    out.append((f"{w['target']} has finished, sir.", f"watch:{w['id']}"))
                    done.append(w["id"])
            elif kind == "download":
                target = w["target"].lower()
                for name in arrived or []:
                    if not target or target in name.lower():
                        out.append((f"{name} has finished downloading, sir.", f"watch:{w['id']}"))
                        done.append(w["id"])
                        break
            elif kind == "file":
                folder, pattern = w["folder"], w["target"].lower()
                if folder and os.path.isdir(folder):
                    # APPEARS means new: a file that was already there when he
                    # asked is not the one he is waiting for, and an empty
                    # pattern ("a file") would otherwise fire on the folder.
                    hit = next((n for n in os.listdir(folder)
                                if pattern in n.lower() and _is_new_file(os.path.join(folder, n), w["created_ts"])
                                and not n.lower().endswith((".crdownload", ".part", ".tmp"))), None)
                    if hit:
                        out.append((f"{hit} has appeared in {os.path.basename(folder) or folder}, sir.",
                                    f"watch:{w['id']}"))
                        done.append(w["id"])
            elif kind == "battery" and battery is not None:
                pct = float(getattr(battery, "percent", 0) or 0)
                if pct >= float(w["level"]):
                    if w["level"] >= 100:
                        out.append(("The battery is fully charged, sir.", f"watch:{w['id']}"))
                    else:
                        out.append((f"The battery has reached {int(pct)} percent, sir.", f"watch:{w['id']}"))
                    done.append(w["id"])
        except Exception:
            log.debug("standing watch %s failed to evaluate", w["id"], exc_info=True)

    _delete(done)
    return out
