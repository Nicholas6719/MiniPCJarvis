"""No scheduled task of mine may outlive the thing it ran.

THIS IS A BUILD GATE BECAUSE THE DISCIPLINE FAILED THREE TIMES.

Real-session verification on this machine goes through `schtasks`, because the
agent shell's %APPDATA% is a virtualized shadow and cannot see what the app
actually reads. The habit that grew around that was
`schtasks /Create /SC ONCE /ST 23:59` plus an immediate `/Run` — with 23:59
treated as a "never" time. It is not never. It is tonight.

  * 2026-09-01: `JARVIS_SUITES_FULL` was left registered. It fired at 23:59 and
    ran the whole e2e suite including `telegram_e2e`. Nicholas woke to a stock
    quote, a button prompt, a test voice clip and a weather answer, all stamped
    12:09 AM. His words: "I know for a fact we weren't running any tests at
    midnight."
  * 2026-08-29: `JARVIS_SOUND`, written the same way, had already fired at 23:59.
  * 2026-09-02: `JARVIS_PCLIVE`, registered for the phase B live check, was still
    armed to run a generate-and-slice against his live app that night.

A rule written in a handoff is not a guard; it is a hope, and it had already been
written down when the third one happened. So the build refuses to produce a
sidecar while agent debris is registered. The build is the right chokepoint: it
is the last thing that runs before anything is deployed.

WHAT COUNTS AS DEBRIS. Anything under `.agent\\` — that directory is mine, is
gitignored, and nothing in it is ever meant to be scheduled permanently — plus
anything named `JARVIS_RUNONCE_*`. His own tasks are left alone, and the one
legitimate permanent task is allowlisted by name.

Run: python tests/check_stray_tasks.py
"""
import csv
import io
import os
import subprocess
import sys

# The only scheduled task this project is supposed to own. scripts/selftest.cmd,
# daily at 03:30: its suite list contains no Telegram suite and it deletes the
# test reminder it creates, so it cannot message his phone.
ALLOWED = {"JARVIS_SELFTEST"}

AGENT_DIR = os.path.join("JARVIS", ".agent")


def tasks() -> list[dict]:
    try:
        # No shell. Git Bash rewrites a leading "/Query" into a filesystem path,
        # which is how this returned an empty list — and therefore passed — the
        # first time it was written.
        out = subprocess.run(
            ["schtasks.exe", "/Query", "/FO", "CSV", "/V"],
            capture_output=True, text=True, timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError) as e:
        print(f"  could not query the scheduler ({e}) — skipping")
        return []
    if out.returncode != 0:
        print(f"  schtasks exited {out.returncode} — skipping")
        return []
    rows = []
    for row in csv.DictReader(io.StringIO(out.stdout)):
        if row.get("TaskName") and row.get("TaskName") != "TaskName":
            rows.append(row)
    return rows


def main() -> int:
    stray = []
    for t in tasks():
        name = (t.get("TaskName") or "").lstrip("\\")
        action = t.get("Task To Run") or ""
        if name in ALLOWED:
            continue
        looks_mine = (AGENT_DIR.lower() in action.lower().replace("/", "\\")
                      or name.upper().startswith("JARVIS_RUNONCE_"))
        if looks_mine:
            stray.append((name, action.strip(), t.get("Next Run Time", "?")))

    if not stray:
        print("  PASS  no agent scheduled tasks are left registered")
        return 0

    print("  FAIL  scheduled tasks left registered by the agent:")
    for name, action, nxt in stray:
        print(f"          {name}  ->  {action}")
        print(f"          next run: {nxt}")
    print()
    print("  These fire on their own schedule whether or not anyone is watching,")
    print("  and 23:59 means tonight, not never. Delete them:")
    for name, _, _ in stray:
        print(f"      schtasks /Delete /TN {name} /F")
    print()
    print("  Use scripts/runonce.ps1 instead — it deletes the task in a `finally`,")
    print("  so a one-off check cannot outlive itself.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
