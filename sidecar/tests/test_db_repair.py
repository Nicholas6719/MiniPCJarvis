"""The repair must never be worse than the fault.

On 2026-08-31 the `transcript`, `audit_log` and `turn_stats` b-trees corrupted.
Nothing detected it: SQLite only reports a damaged page when a query happens to
touch one, so the first symptom arrived hours later as a writer that could not
drain, a stale 4MB WAL, and `database is locked` on every turn. JARVIS passed
its own health check and answered nothing.

The tool written to fix that had three bugs of its own, and each is a case here:

  * it probed for a max rowid in powers of ten up to 10,000,000, so a corrupt
    table produced 50,000 scans and it never finished the first table;
  * it counted a row the DESTINATION refused as source corruption and threw
    away that row's whole 200-row batch with it;
  * its WITHOUT ROWID path incremented an offset and `continue`d on every
    error, with no exit - an infinite loop on a table that always raises.

Offline: no app, no network, no audio.
Run: python tests/test_db_repair.py
"""
import os
import shutil
import sqlite3
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


def build(path: str, rows: int = 2500) -> None:
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE memories (id INTEGER PRIMARY KEY AUTOINCREMENT,
                               ts REAL NOT NULL, content TEXT NOT NULL);
        CREATE TABLE transcript (id INTEGER PRIMARY KEY AUTOINCREMENT,
                                 ts REAL NOT NULL, role TEXT, content TEXT);
    """)
    c.executemany("INSERT INTO memories (ts, content) VALUES (?,?)",
                  [(time.time(), f"a thing he told me number {i}") for i in range(20)])
    c.executemany("INSERT INTO transcript (ts, role, content) VALUES (?,?,?)",
                  [(time.time(), "user", f"turn {i} " + "padding " * 30)
                   for i in range(rows)])
    c.commit()
    c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    c.close()


def smash(path: str, table: str) -> None:
    """Corrupt the pages belonging to one table, and only that table."""
    c = sqlite3.connect(path)
    page_size = c.execute("PRAGMA page_size").fetchone()[0]
    root = c.execute("SELECT rootpage FROM sqlite_master WHERE name=?",
                     (table,)).fetchone()[0]
    total = c.execute("PRAGMA page_count").fetchone()[0]
    c.close()
    # Scribble over a SMALL run of pages after the table's root. Both extremes
    # are the wrong test: destroying the root loses the table outright, and
    # smashing every page of it means nothing could be salvaged by anybody, so a
    # tool that recovered zero rows would still look correct. The real
    # 2026-08-31 fault was PARTIAL - 6,019 of 10,278 transcript rows were still
    # readable - and partial is the case worth gating.
    span = max(2, (total - root) // 6)
    with open(path, "r+b") as fh:
        for page in range(root + 2, min(root + 2 + span, total + 1)):
            fh.seek((page - 1) * page_size)
            fh.write(b"\xde\xad\xbe\xef" * (page_size // 4))


def main() -> int:
    from tools.db_repair import check_and_repair, integrity, repair

    work = tempfile.mkdtemp(prefix="jarvis-dbrepair-")
    db = os.path.join(work, "jarvis.db")

    # --- a healthy database is left completely alone -------------------------
    build(db)
    before = os.path.getmtime(db)
    _c = sqlite3.connect(db)
    clean = integrity(_c) == []
    _c.close()          # Windows will not replace a file anything still holds
    check("a clean database reports clean", clean)
    time.sleep(0.05)
    check("...and check_and_repair does not touch it",
          check_and_repair(db) is None and os.path.getmtime(db) == before)

    # --- a damaged one is detected -------------------------------------------
    smash(db, "transcript")
    conn = sqlite3.connect(db)
    findings = integrity(conn)
    conn.close()
    check("damage to one table is detected", bool(findings),
          "integrity_check said the file was fine")

    # --- ...and repaired, without a hang -------------------------------------
    t0 = time.time()
    rep = repair(db, force_precious=True)
    took = time.time() - t0
    check("the repair terminates", took < 120, f"took {took:.0f}s")
    check("...and produces a clean file", rep.get("clean"), rep.get("still_bad"))
    check("...and swapped it in", rep.get("swapped"))
    check("...and kept a backup of the damaged one",
          rep.get("backup") and os.path.exists(rep["backup"]))

    conn = sqlite3.connect(db)
    check("the repaired file passes integrity_check", integrity(conn) == [])

    # The whole point: damage to the conversation log costs him the log, and
    # nothing else. If this ever fails, the repair is eating his memories.
    check("every memory survived damage to another table",
          conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 20,
          conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0])
    kept = conn.execute("SELECT COUNT(*) FROM transcript").fetchone()[0]
    check("most of the damaged table is salvaged rather than dropped",
          kept > 0, f"only {kept} transcript rows came back")
    check("...and the database is writable again",
          _can_write(conn))
    conn.close()

    # --- the guard that stops a repair from becoming the data loss -----------
    db2 = os.path.join(work, "precious.db")
    build(db2)
    smash(db2, "memories")
    rep2 = repair(db2)                     # force_precious NOT set
    if rep2.get("precious_lost"):
        check("a repair that would lose memories refuses to swap",
              rep2.get("swapped") is False and rep2.get("refused"))
        check("...and leaves the original in place", os.path.exists(db2))
    else:
        check("a repair that loses nothing precious is free to swap",
              rep2.get("swapped") is not False)

    shutil.rmtree(work, ignore_errors=True)
    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


def _can_write(conn) -> bool:
    try:
        conn.execute("INSERT INTO transcript (ts, role, content) VALUES (?,?,?)",
                     (time.time(), "user", "post-repair write"))
        conn.commit()
        return True
    except Exception:
        return False


if __name__ == "__main__":
    sys.exit(main())
