"""Salvage a corrupted JARVIS database, row by row.

On 2026-08-31 every single turn started dying at `log_turn` with
"database is locked". The lock was a symptom; `PRAGMA integrity_check` reported
`btreeInitPage() returns error code 11` — SQLITE_CORRUPT — across ~60 pages of
tree 4, which is the `transcript` table. A corrupt b-tree page cannot be
checkpointed out of the WAL, so the 4MB write-ahead log went stale, the writer
never drained, and every subsequent write waited out its busy_timeout and
raised. JARVIS could not answer anything.

The important property of this file: it NEVER trusts a whole table. It walks
each one rowid by rowid and keeps what reads cleanly, so corruption in the
conversation log cannot cost him a single fact, memory or learned command. What
cannot be read is counted and reported rather than silently dropped — a repair
that quietly loses data is worse than the corruption.

Used two ways:
  * `python -m tools.db_repair <db>` — operator-driven, prints a full report
  * `check_and_repair()` — called at sidecar startup, repairs only on damage
"""
from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import time

log = logging.getLogger("jarvis.db_repair")

# Tables holding things he taught JARVIS. If these are ever unsalvageable the
# repair stops and says so rather than starting him over with a clean slate.
PRECIOUS = ("memories", "facts", "brain_examples", "brain_commands", "tasks")


def integrity(conn: sqlite3.Connection) -> list[str]:
    """Empty list means clean. Cheap enough to run on every boot."""
    try:
        rows = conn.execute("PRAGMA integrity_check").fetchall()
    except sqlite3.DatabaseError as e:
        return [f"integrity_check itself failed: {e}"]
    if len(rows) == 1 and rows[0][0] == "ok":
        return []
    return [r[0] for r in rows]


def _tables(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    return conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()


def _max_rowid(src: sqlite3.Connection, name: str) -> int:
    """The highest rowid in a table whose b-tree may be damaged.

    This is the difference between a repair that takes a minute and one that
    never finishes. `MAX(rowid)` walks the tree and raises on exactly the tables
    that need salvaging, and the first version of this answered that by probing
    powers of ten up to 10,000,000 - so a corrupt `audit_log` produced 50,000
    range scans, each falling back to 200 single-row reads. It burned fifteen
    minutes on the first table and never reached the others.

    `sqlite_sequence` holds the AUTOINCREMENT high-water mark for each table in
    its own tiny b-tree, which is intact precisely because it is not the table
    that got corrupted. Ask it first.
    """
    try:
        hi = src.execute(f'SELECT MAX(rowid) FROM "{name}"').fetchone()[0]
        if hi:
            return int(hi)
    except sqlite3.DatabaseError:
        pass
    try:
        row = src.execute("SELECT seq FROM sqlite_sequence WHERE name=?",
                          (name,)).fetchone()
        if row and row[0]:
            log.info("%s: taking max rowid %s from sqlite_sequence", name, row[0])
            return int(row[0])
    except sqlite3.DatabaseError:
        pass
    # Last resort: bound the scan by what the FILE could possibly hold. The
    # obvious probe - widening `WHERE rowid < N LIMIT 1` until it fails - is
    # worthless here, because that query reads the damaged first page and fails
    # immediately, so it answered 0 and the table was skipped entirely without
    # a single row being tried. Size is the one thing still knowable when the
    # b-tree is not: no table can hold more rows than the file has room for.
    try:
        pages = src.execute("PRAGMA page_count").fetchone()[0]
        size = src.execute("PRAGMA page_size").fetchone()[0]
        hi = min(int(pages) * int(size) // 24, 4_000_000)
    except sqlite3.DatabaseError:
        hi = 100_000
    log.warning("%s: no reliable max rowid; scanning up to %d", name, hi)
    return max(hi, 1000)


def _salvage_keys(src: sqlite3.Connection, name: str) -> list[str]:
    """Write out every key an intact index can still name, for a damaged table.

    This does not recover the rows - the payload is on the pages that broke.
    It recovers the ABILITY TO SAY WHAT WAS LOST, which is the difference
    between "521 learned examples, 451 unreadable, here they are" and a brain
    that is quietly 451 examples smaller than it was yesterday.
    """
    out: list[str] = []
    try:
        idxs = src.execute(f"PRAGMA index_list({name})").fetchall()
    except sqlite3.DatabaseError:
        return out
    for idx in idxs:
        iname = idx[1]
        try:
            icols = [r[2] for r in src.execute(f"PRAGMA index_info({iname})")]
            if not icols:
                continue
            sel = ",".join(f'"{c}"' for c in icols)
            rows = src.execute(
                f'SELECT {sel} FROM "{name}" ORDER BY {sel}').fetchall()
        except sqlite3.DatabaseError:
            continue
        if not rows:
            continue
        out = [" | ".join("" if v is None else str(v) for v in r) for r in rows]
        break
    if out:
        path = os.path.join(os.path.dirname(os.path.abspath(
            src.execute("PRAGMA database_list").fetchall()[0][2] or ".")),
            f"salvaged-keys-{name}.txt")
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("\n".join(out))
            log.warning("%s: %d keys recovered from its index -> %s",
                        name, len(out), path)
        except OSError:
            log.warning("%s: %d keys recovered but could not be written out",
                        name, len(out))
    return out


def _copy_table(src: sqlite3.Connection, dst: sqlite3.Connection,
                name: str) -> tuple[int, int]:
    """Copy one table, skipping only the rows that will not read.

    A corrupt page kills the whole SELECT that touches it, so a failed bulk read
    is retried one rowid at a time and only the individual unreadable rows are
    lost.

    Returns (kept, rejected, unreadable), and the difference between the last
    two is why the repair could never run. `rejected` is a row we READ and the
    new file would not take — a real loss and our fault. `unreadable` is a probe
    that raised, which may be corruption, a deleted row, or a rowid that never
    existed; it cannot be told apart and so must never be counted as a row.
    """
    cols = [r[1] for r in src.execute(f"PRAGMA table_info({name})")]
    if not cols:
        return (0, 0, 0)
    collist = ",".join(f'"{c}"' for c in cols)
    marks = ",".join("?" * len(cols))
    # PLAIN INSERT, deliberately. `OR IGNORE` swallows a unique-key collision
    # and a genuinely rejected row into the same silent no-op, and those are
    # opposite things: the first is deduplication and the second is data loss.
    insert = f'INSERT INTO "{name}" ({collist}) VALUES ({marks})'

    def put(rows) -> tuple[int, int, int]:
        """Write rows, one at a time if the batch is refused. Never raises.

        Keeping this separate from the READ is the whole point. The first
        version let a destination error propagate into the reader's
        `except DatabaseError`, so a single row the new table would not accept
        was recorded as source corruption and took its whole 200-row batch with
        it: `brain_examples` reported 91 rows kept out of 521, and 430 of his
        learned examples would have been thrown away by the repair rather than
        by the fault it was repairing.
        """
        if not rows:
            return (0, 0, 0)
        # ALL OR NOTHING, so the row-by-row pass starts from a known state.
        # Without the savepoint a failed `executemany` leaves the rows before
        # the failure behind, the retry then counts them as duplicates, and the
        # report says "kept 9" about a table holding 17 rows. A repair whose
        # own numbers cannot be trusted is not much of a repair.
        try:
            dst.execute("SAVEPOINT batch")
            dst.executemany(insert, rows)
            dst.execute("RELEASE batch")
            return (len(rows), 0, 0)
        except sqlite3.DatabaseError:
            try:
                dst.execute("ROLLBACK TO batch")
                dst.execute("RELEASE batch")
            except sqlite3.DatabaseError:
                pass
        ok = dup = bad = 0
        for r in rows:
            try:
                dst.execute(insert, r)
                ok += 1
            except sqlite3.IntegrityError:
                # Already there. A damaged b-tree can hand the same logical row
                # back through more than one rowid, which is why the source
                # yielded 38 `brain_examples` describing 17 distinct ones.
                dup += 1
            except sqlite3.DatabaseError as e:
                bad += 1
                log.debug("%s: destination refused a row: %s", name, e)
        return (ok, dup, bad)

    # Fast path: the table is fine, take it in one pass.
    try:
        rows = src.execute(f'SELECT {collist} FROM "{name}"').fetchall()
    except sqlite3.DatabaseError as e:
        rows = None
        log.warning("table %s will not bulk-read (%s); falling back to row-by-row", name, e)
    if rows is not None:
        ok, dup, bad = put(rows)
        if dup:
            log.info("%s: %d duplicate row(s) collapsed", name, dup)
        dst.commit()
        return (ok, bad, 0)

    # Before walking the table, try its indexes. An index is a SEPARATE b-tree,
    # so it routinely survives when the table it describes does not: on
    # 2026-08-31 `brain_examples` returned 71 rows by rowid and all 521 keys
    # through `sqlite_autoindex_brain_examples_1`. The payload still lives on
    # the damaged pages and mostly cannot be read back — but the KEYS can, and
    # writing them out means a lost row is a row we can name and relearn rather
    # than one that silently stopped existing.
    _salvage_keys(src, name)

    # Slow path: walk it. Tables with a rowid can be scanned by key so one bad
    # page only costs the rows on it; a WITHOUT ROWID table gets LIMIT/OFFSET.
    kept = rejected = unreadable = dups = 0
    has_rowid = True
    try:
        src.execute(f'SELECT rowid FROM "{name}" LIMIT 1').fetchall()
    except sqlite3.DatabaseError:
        has_rowid = False

    if has_rowid:
        hi = _max_rowid(src, name)
        step = 200
        lo = 0
        while lo <= hi:
            top = lo + step
            try:
                batch = src.execute(
                    f'SELECT {collist} FROM "{name}" WHERE rowid>=? AND rowid<?',
                    (lo, top)).fetchall()
            except sqlite3.DatabaseError:
                batch = None
            if batch is None:
                for rid in range(lo, top):        # isolate the damage to single rows
                    try:
                        row = src.execute(
                            f'SELECT {collist} FROM "{name}" WHERE rowid=?', (rid,)).fetchone()
                    except sqlite3.DatabaseError:
                        # Damage, a deleted row, or a rowid that never was.
                        unreadable += 1
                        continue
                    if row is not None:
                        ok, dup, bad = put([row])
                        kept += ok
                        dups += dup
                        rejected += bad
            else:
                ok, dup, bad = put(batch)
                kept += ok
                dups += dup
                rejected += bad
            lo = top
    else:
        # Bounded, deliberately. The first version incremented the offset and
        # `continue`d on every error with no exit — and a table that raises on
        # EVERY read (which is exactly what a corrupt `turn_stats` does) made
        # that an infinite loop. The repair hung there twice for the full
        # timeout and never reached the tables after it.
        off = 0
        misses = 0
        cap = max(_max_rowid(src, name), 1) + 1000
        while off < cap and misses < 20:
            try:
                rows = src.execute(
                    f'SELECT {collist} FROM "{name}" LIMIT 500 OFFSET ?', (off,)).fetchall()
            except sqlite3.DatabaseError:
                # ONE FAILED READ, NOT 500 LOST ROWS. This line is what made
                # `tasks` report 1500 rows gone from a table whose high-water
                # mark is 229, and that fiction refused every repair.
                unreadable += 1
                misses += 1
                off += 500
                continue
            misses = 0
            if not rows:
                break
            ok, dup, bad = put(rows)
            kept += ok
            dups += dup
            rejected += bad
            off += len(rows)
    if dups:
        log.info("%s: %d duplicate row(s) collapsed", name, dups)
    dst.commit()
    return (kept, rejected, unreadable)


def repair(db_path: str, *, keep_backup: bool = True,
           force_precious: bool = False) -> dict:
    """Rebuild `db_path` into a clean file, preserving every readable row."""
    db_path = os.path.abspath(db_path)
    tmp = db_path + ".rebuild"
    for leftover in (tmp, tmp + "-wal", tmp + "-shm"):
        if os.path.exists(leftover):
            os.remove(leftover)

    src = sqlite3.connect(db_path, timeout=30)
    # NEVER CHOKE ON A MANGLED UTF-8 BLOB — and actually mean it. The line
    # that set `bytes` here was overwritten by `str` on the very next line, so a
    # text cell damaged by the corruption raised "Could not decode to UTF-8",
    # which is a DatabaseError, which counted the row as unreadable and dropped
    # it — in a PRECIOUS table, a memory quietly gone after a repair that
    # reported success. Decode with replacement instead: a memory with one
    # bad character in it is a memory; a missing one is not.
    src.text_factory = lambda b: b.decode("utf-8", "replace")
    # Best effort: fold whatever the WAL still holds into the main file so the
    # salvage sees the newest data. On a corrupt tree this legitimately fails.
    try:
        src.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.DatabaseError as e:
        log.warning("could not checkpoint the WAL before salvage: %s", e)

    report: dict = {"tables": {}, "lost": 0, "kept": 0, "unreadable": 0,
                    "precious_lost": [], "precious_unreadable": []}
    dst = sqlite3.connect(tmp, timeout=30)
    dst.execute("PRAGMA journal_mode=WAL")
    dst.execute("PRAGMA synchronous=NORMAL")

    schema = _tables(src)
    for name, sql in schema:
        if sql:
            try:
                dst.execute(sql)
            except sqlite3.DatabaseError as e:
                log.error("could not recreate %s: %s", name, e)
                continue
    # indexes and triggers after the data lands, so a corrupt row cannot trip a
    # unique constraint mid-copy and abort the whole salvage
    extras = src.execute(
        "SELECT sql FROM sqlite_master WHERE type IN ('index','trigger','view') "
        "AND sql IS NOT NULL AND name NOT LIKE 'sqlite_%'").fetchall()

    for name, _sql in schema:
        t0 = time.time()
        kept, rejected, unreadable = _copy_table(src, dst, name)
        log.info("%-16s kept %6d  rejected %4d  unreadable %5d  (%.1fs)",
                 name, kept, rejected, unreadable, time.time() - t0)
        report["tables"][name] = {"kept": kept, "rejected": rejected,
                                  "unreadable": unreadable}
        report["kept"] += kept
        report["lost"] += rejected
        report["unreadable"] += unreadable
        if name in PRECIOUS:
            # A row we READ and then dropped is our bug and stops the swap.
            if rejected:
                report["precious_lost"].append(f"{name}:{rejected}")
            # A precious table we cannot read at all is said out loud and does
            # NOT stop the swap: refusing there keeps a database in which those
            # rows are exactly as unreadable, and loses everything else too.
            if unreadable and not kept:
                report["precious_unreadable"].append(name)
                log.error("%s is unreadable in the damaged file — nothing to "
                          "recover, and keeping the damage would not bring it "
                          "back", name)

    for (sql,) in extras:
        try:
            dst.execute(sql)
        except sqlite3.DatabaseError as e:
            log.warning("could not rebuild index/trigger: %s", e)
    dst.commit()

    remaining = integrity(dst)
    report["clean"] = not remaining
    if remaining:
        report["still_bad"] = remaining[:10]
    dst.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    dst.close()
    src.close()

    if not report["clean"]:
        os.remove(tmp)
        report["swapped"] = False
        report["refused"] = "the rebuilt file is still not clean"
        return report

    # A repair that quietly loses what he taught JARVIS is worse than the fault
    # it repairs. Transcript and audit rows are logs and may be dropped; a
    # memory, a fact, a learned example or a task may not be. If any of those
    # did not survive, keep the damaged original and say so - a human can then
    # decide, with both files still on disk.
    if report["precious_lost"] and not force_precious:
        os.remove(tmp)
        report["swapped"] = False
        report["refused"] = ("irreplaceable rows did not survive the salvage: "
                             + ", ".join(report["precious_lost"]))
        log.error("REFUSING to swap: %s", report["refused"])
        return report

    stamp = time.strftime("%Y%m%d-%H%M%S")
    if keep_backup:
        shutil.copy2(db_path, f"{db_path}.corrupt-{stamp}.bak")
    for side in ("-wal", "-shm"):
        try:
            if os.path.exists(db_path + side):
                os.remove(db_path + side)
        except OSError:
            pass                    # a live reader still has it; replace decides

    # Windows refuses to replace a file any process still has open, and the
    # repair must not die at the final step with a clean rebuild in hand. Retry
    # briefly for a handle that is on its way out, then report honestly and
    # leave BOTH files on disk rather than raising into the caller.
    for attempt in range(5):
        try:
            os.replace(tmp, db_path)
            break
        except OSError as e:
            if attempt == 4:
                report["swapped"] = False
                report["refused"] = (
                    f"rebuilt cleanly but could not be swapped in ({e}); "
                    f"the repaired database is at {tmp}")
                log.error("%s", report["refused"])
                return report
            time.sleep(0.4)
    report["swapped"] = True
    report["backup"] = f"{db_path}.corrupt-{stamp}.bak" if keep_backup else None
    return report


def check_and_repair(db_path: str) -> dict | None:
    """Boot guard. Returns None when the database was already healthy.

    This runs BEFORE any connection is handed out, because a corrupt page does
    not announce itself — it surfaces hours later as a stuck writer and a dead
    turn, which is exactly how this was found.
    """
    if not os.path.exists(db_path):
        return None
    try:
        conn = sqlite3.connect(db_path, timeout=10)
        bad = integrity(conn)
        conn.close()
    except sqlite3.DatabaseError as e:
        bad = [str(e)]
    if not bad:
        return None
    log.error("database is damaged (%d findings, first: %s) — repairing",
              len(bad), bad[0])
    rep = repair(db_path)
    log.error("repair %s: kept %d rows, lost %d%s",
              "succeeded" if rep.get("swapped") else "FAILED",
              rep["kept"], rep["lost"],
              f" (irreplaceable: {rep['precious_lost']})" if rep["precious_lost"] else "")
    return rep


if __name__ == "__main__":
    import json
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    target = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.environ["APPDATA"], "JARVIS", "jarvis.db")
    print(f"target: {target}")
    c = sqlite3.connect(target, timeout=10)
    before = integrity(c)
    c.close()
    print(f"integrity before: {'CLEAN' if not before else str(len(before)) + ' findings'}")
    if not before:
        sys.exit(0)
    result = repair(target)
    print(json.dumps(result, indent=2)[:4000])
    sys.exit(0 if result.get("swapped") else 1)
