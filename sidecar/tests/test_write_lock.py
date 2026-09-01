"""Nobody may hold the write lock after they are done.

This is the fault underneath the worst two days JARVIS has had.

`brain.load()` deletes stale seed examples on every start, but its only
`commit()` sat inside `if missing:`. On any start where rows were dropped and
nothing needed re-seeding, that connection kept an open write transaction — and
SQLite allows exactly one writer — for the whole life of the process.

Everything downstream then failed with "database is locked":
  * `log_turn` raised, and every turn died with it (2026-08-31);
  * the scheduler could not move a due reminder on, so it re-announced the
    9 p.m. retainer reminder every ten seconds and sent ~2,600 overnight
    messages (2026-09-01).

Both of those were treated as their own bugs and fixed as their own bugs. They
were symptoms. This is the disease, and this test is the one that would have
caught it: after a subsystem finishes its work, an INDEPENDENT connection must
still be able to write.

Offline: no app, no network, no audio.
Run: python tests/test_write_lock.py
"""
import asyncio
import os
import sqlite3
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB = os.path.join(tempfile.mkdtemp(), "lock.db")
os.environ.setdefault("JARVIS_DB", DB)

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


def writable(why: str) -> bool:
    """Can a SEPARATE connection take the write lock right now?"""
    c = sqlite3.connect(os.environ["JARVIS_DB"], timeout=3)
    try:
        c.execute("BEGIN IMMEDIATE")
        c.rollback()
        return True
    except sqlite3.OperationalError as e:
        print(f"      ({why}: {e})")
        return False
    finally:
        c.close()


def main() -> int:
    from config import DB_PATH
    os.environ["JARVIS_DB"] = str(DB_PATH)

    check("a fresh database is writable", writable("fresh"))

    # --- the exact 2026-08-31 shape --------------------------------------
    # Load the brain once so the table is fully seeded, then poison a row so
    # the NEXT load has something to delete and nothing to insert. That is the
    # combination that never reached a commit.
    from brain.router import brain
    asyncio.run(brain.load())
    check("the write lock is free after the first brain load",
          writable("after first load"))

    n = brain.db.execute("SELECT COUNT(*) FROM brain_examples").fetchone()[0]
    check("the brain actually seeded", n > 0, n)

    # The combination that never committed: rows that WILL be deleted, and
    # nothing left missing afterwards. Mislabelling a real seed does not do it —
    # deleting it makes it missing again, so the seeding branch commits and the
    # lock is released by luck. These phrases are in no skill's seed list, so
    # they are dropped as stale and nothing is inserted to replace them.
    import numpy as np
    blob = np.zeros(384, dtype=np.float32).tobytes()
    brain.db.executemany(
        "INSERT OR IGNORE INTO brain_examples (ts, text, skill, source, embedding) "
        "VALUES (?,?,?,?,?)",
        [(time.time(), f"zzz retired seed phrase {i}", "chitchat", "seed", blob)
         for i in range(3)])
    brain.db.commit()
    before = brain.db.execute("SELECT COUNT(*) FROM brain_examples").fetchone()[0]

    asyncio.run(brain.load())
    after = brain.db.execute("SELECT COUNT(*) FROM brain_examples").fetchone()[0]
    check("the stale rows were dropped, and nothing was re-seeded",
          after == before - 3, f"{before} -> {after}")
    check("...and the write lock is FREE afterwards",
          writable("after a delete-only load"),
          "the deletions were never committed, so this connection still holds "
          "the write lock — this is the bug behind both outages")

    # --- and a reminder can still be rescheduled --------------------------
    # The downstream consequence, asserted directly: with the lock free, the
    # scheduler advances its task instead of re-announcing it forever.
    from tasks.scheduler import Scheduler
    said = []

    async def announce(t):
        said.append(t)

    s = Scheduler()
    s.announce = announce
    s.db.execute("DELETE FROM tasks")
    s.db.commit()
    tid = s.add("wear my retainers", time.time() - 3600, "daily")

    async def ticks(n=5):
        for _ in range(n):
            now = time.time()
            for r in s.db.execute(
                    "SELECT id, due_ts, text, recurrence FROM tasks "
                    "WHERE status='pending' AND due_ts <= ?", (now,)).fetchall():
                if s._suppressed(r[0], r[1], now):
                    continue
                await s._fire(r[0], r[1], r[2], r[3])

    asyncio.run(ticks())
    check("a due reminder is announced once", len(said) == 1, f"{len(said)}")
    nxt = s.db.execute("SELECT due_ts FROM tasks WHERE id=?", (tid,)).fetchone()[0]
    check("...and its schedule actually moved", nxt > time.time(),
          "it did not advance — this is how the flood started")

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
