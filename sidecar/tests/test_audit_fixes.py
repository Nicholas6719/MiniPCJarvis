"""Regression guards for the 2026-08-23 codebase audit. Pure/offline (no running app).
Run: python tests/test_audit_fixes.py"""
import sys, asyncio
sys.path.insert(0, ".")
from brain.skills import _number, slots_app, slots_reminder, slots_correction  # noqa: E402
from brain.router import _light, _norm  # noqa: E402
from tools.file_tools import _resolve  # noqa: E402

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


# --- number words in text order + compound ---
check("number 'twenty five' = 25", _number("twenty five") == 25, _number("twenty five"))
check("number 'ninety nine' = 99", _number("ninety nine") == 99)
check("number 'forty' = 40", _number("forty") == 40)
check("number 'to max' = 100", _number("to max") == 100)
check("number digit still wins", _number("set to 35") == 35)

# --- shutdown never becomes close-app; generic close names rejected ---
check("'shut down the pc' not close-app", _norm("shut down the pc") == "close APP" and slots_app(_light("shut down the pc")) is None or _norm("shut down the pc") != "close APP",
      f"norm={_norm('shut down the pc')} slots={slots_app(_light('shut down the pc'))}")
check("slots_app rejects 'down the pc'", slots_app("down the pc") is None)
check("slots_app rejects 'all windows'", slots_app("close all windows") is None)
check("slots_app keeps 'spotify'", slots_app("close spotify") == {"name": "spotify"})

# --- reminders honor tomorrow + evening PM ---
r = slots_reminder("tomorrow at 9 to call mom")
check("reminder tomorrow sets a date", r and r.get("date") and r.get("at_time") == "09:00", r)
r = slots_reminder("at 9 tonight to lock up")
check("reminder 'tonight' = PM", r and r.get("at_time") == "21:00", r)

# --- correction not triggered by polite decline ---
check("'no thanks' is not a correction", slots_correction("no thanks") is None)
check("'no i meant X' is a correction", (slots_correction("no i meant open spotify") or {}).get("rest") == "open spotify")

# --- file-tool sandbox: relative traversal blocked ---
check("traversal '../../../Windows/..' blocked", _resolve("../../../Windows/System32/drivers/etc/hosts") is None)
check("traversal '../..' blocked", _resolve("../../AppData/Roaming/JARVIS/config.json") is None)
check("legit 'downloads' resolves", _resolve("downloads") is not None)

# --- close_application: exact/whole-word matching, no fan-out ---
from tools.builtin import close_application  # noqa: E402
# 'close x' (too generic after stopword strip) must refuse, not nuke the desktop
res = close_application("all windows")
check("close 'all windows' refuses", "error" in res, res)

print(f"\n{'ALL PASS' if not fails else 'FAILURES: ' + ', '.join(fails)}")
sys.exit(1 if fails else 0)
