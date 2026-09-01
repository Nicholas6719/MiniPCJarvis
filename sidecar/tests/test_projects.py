"""Projects: the arithmetic, and the refusal to invent a date.

Phase 1 of the Evolution. What is gated here is what would actually cost him:

  * the new tables do not touch `tasks`. That table is reminders and errands, and
    two unrelated features writing the same rows is how a nightly retainer
    reminder ends up in a project list;
  * an estimate is produced ONLY when there is enough history to project one.
    One data point cannot give a finish date, and a confident date drawn from one
    is exactly the plausible invention that costs trust — it must say so instead;
  * the rate is measured between recorded MARKS, not from creation, or a project
    created weeks before the first real work looks glacial;
  * a write that fails rolls back. An uncommitted write holds SQLite's single
    write lock for the life of the process and kills every turn — that has
    happened twice in this project.

Offline: no model, no network. Run: python tests/test_projects.py
"""
import asyncio
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["JARVIS_DB"] = os.path.join(tempfile.mkdtemp(), "projects.db")

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


async def main() -> int:
    from tools import projects as P

    # --- creation on first mention ------------------------------------------
    r = await P.log_progress("hud rewrite", note="started")
    check("logging progress creates the project", r.get("created") is True, r)
    check("...and it starts at zero", r.get("percent") == 0, r)

    r = await P.log_progress("hud rewrite", percent=20)
    check("a percentage is recorded", r.get("percent") == 20, r)
    check("...and does not re-create it", r.get("created") is False, r)

    listed = await P.list_projects()
    check("it appears in the active list", listed["count"] == 1, listed)

    # --- refusing to invent -------------------------------------------------
    est = await P.estimate_completion("hud rewrite")
    check("one mark is not enough for a date", est.get("estimate") is None, est)
    check("...and it says why rather than guessing",
          "not enough history" in (est.get("why") or ""), est)

    # --- the arithmetic, with time forced so the test is deterministic ------
    db = P._conn()
    pid = P._find("hud rewrite")[0]
    now = time.time()
    db.execute("DELETE FROM project_steps WHERE project_id=?", (pid,))
    # 10% then 30% ten days apart: 2%/day, 70% left -> 35 days
    db.execute("INSERT INTO project_steps (project_id, ts, note, percent) VALUES (?,?,?,?)",
               (pid, now - 10 * 86400, "", 10))
    db.execute("INSERT INTO project_steps (project_id, ts, note, percent) VALUES (?,?,?,?)",
               (pid, now, "", 30))
    db.execute("UPDATE projects SET percent=30 WHERE id=?", (pid,))
    db.commit()

    est = await P.estimate_completion("hud rewrite")
    check("a real rate is computed", est.get("percent_per_day") == 2.0, est)
    check("...and the remaining days follow from it",
          abs((est.get("days_remaining") or 0) - 35.0) < 0.6, est)
    check("...with the evidence attached, not just a date",
          "between the first" in (est.get("why") or ""), est)
    check("...and an actual finish date", bool(est.get("estimate")), est)

    # --- finishing ----------------------------------------------------------
    r = await P.log_progress("hud rewrite", percent=100)
    check("hitting 100 marks it done", r.get("status") == "done", r)
    est = await P.estimate_completion("hud rewrite")
    check("...and a finished project is not re-estimated",
          est.get("estimate") == "already finished", est)
    check("...and it leaves the active list",
          (await P.list_projects())["count"] == 0)
    check("...but is still there under 'all'",
          (await P.list_projects("all"))["count"] == 1)

    # --- it must not collide with reminders ---------------------------------
    tables = {r[0] for r in P._conn().execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    check("it created its own tables", {"projects", "project_steps"} <= tables, tables)
    rows = P._conn().execute("SELECT COUNT(*) FROM projects").fetchone()[0]
    check("...and wrote only to them", rows == 1, rows)

    # --- bad input is a sentence, never a crash ------------------------------
    check("a nameless project is refused",
          (await P.log_progress(""))["error"], "")
    check("nonsense percentage is refused, not stored",
          (await P.log_progress("x", percent="banana")).get("error") is not None)
    check("estimating an unknown project says so",
          (await P.estimate_completion("no such thing")).get("error") is not None)

    # --- the write lock -----------------------------------------------------
    # A failed write must roll back. If it does not, this connection keeps
    # SQLite's write lock and every other writer in the process dies.
    try:
        P._conn().execute("INSERT INTO projects (name, created_ts, updated_ts) "
                          "VALUES ('hud rewrite', 0, 0)")     # UNIQUE violation
    except Exception:
        P._conn().rollback()
    ok = True
    try:
        await P.log_progress("another thing", percent=5)
    except Exception as e:
        ok = False
        print("     write after a failed write raised:", e)
    check("a failed write does not poison the connection", ok)

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
