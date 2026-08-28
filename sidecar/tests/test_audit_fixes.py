"""Regression guards for the 2026-08-23 codebase audit. Pure/offline (no running app).
Run: python tests/test_audit_fixes.py"""
import sys, asyncio
sys.path.insert(0, ".")
import os, tempfile  # noqa: E402
# never touch the real jarvis.db: the gates get a throwaway file that
# brain.load() re-seeds from the SKILLS list
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "gate.db"))
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

# --- search/image queries are keywords, never the spoken command (2026-08-26) ---
from tools.query_clean import clean_image_query, clean_search_query  # noqa: E402
from brain.skills import slots_images  # noqa: E402

check("'show me iron man' -> 'iron man'", clean_image_query("show me iron man") == ("iron man", None),
      clean_image_query("show me iron man"))
check("'show me 5 images of spiderman' -> ('spiderman', 5)",
      clean_image_query("show me 5 images of spiderman") == ("spiderman", 5),
      clean_image_query("show me 5 images of spiderman"))
check("'pictures of a nebula please' -> 'a nebula'",
      clean_image_query("pictures of a nebula please")[0] == "a nebula")
check("'show me spiderman pictures' -> 'spiderman'",
      clean_image_query("show me spiderman pictures")[0] == "spiderman")
check("'a couple of photos of mars' -> ('mars', 2)",
      clean_image_query("a couple of photos of mars") == ("mars", 2),
      clean_image_query("a couple of photos of mars"))
check("a question is left alone", clean_image_query("how do i show images in css")[0] == "how do i show images in css")
check("slots_images 'show me iron man'", (slots_images("show me iron man") or {}).get("query") == "iron man",
      slots_images("show me iron man"))
check("slots_images passes the count", (slots_images("show me 5 images of spiderman") or {}) == {"query": "spiderman", "count": 5},
      slots_images("show me 5 images of spiderman"))
check("'search the web for the best mini pc' -> keywords",
      clean_search_query("search the web for the best mini pc") == "the best mini pc")
check("'look up who won the game' -> keywords", clean_search_query("look up who won the game") == "who won the game")
check("'show me the latest nvidia drivers' -> keywords",
      clean_search_query("show me the latest nvidia drivers") == "the latest nvidia drivers")
check("'research the best mini pc of 2026' strips the verb",
      clean_search_query("research the best mini pc of 2026") == "the best mini pc of 2026")
check("'research methods in psychology' keeps its noun",
      clean_search_query("research methods in psychology") == "research methods in psychology")
check("search questions pass through", clean_search_query("what is the tallest building") == "what is the tallest building")
check("clean is idempotent", clean_search_query("the best mini pc") == "the best mini pc")

# --- the database must be WAL: four subsystems write to one file from
# background tasks, and rollback-journal locking made that a "database is
# locked" waiting to happen (2026-08-27 audit) ---
from config import open_db  # noqa: E402
_c = open_db()
check("database opens in WAL mode", _c.execute("PRAGMA journal_mode").fetchone()[0] == "wal")
check("busy_timeout is set", _c.execute("PRAGMA busy_timeout").fetchone()[0] >= 5000)
_c.close()

# --- background work must survive the garbage collector: asyncio holds only a
# WEAK reference to a running task, so fire-and-forget turns could vanish ---
from events import spawn, _background  # noqa: E402


async def _spawn_survives():
    done = []

    async def _work():
        await asyncio.sleep(0.05)
        done.append(True)
    spawn(_work())
    referenced = len(_background) == 1
    import gc
    gc.collect()                      # the exact hazard: a collection mid-flight
    await asyncio.sleep(0.2)
    return referenced and done == [True] and len(_background) == 0


check("spawned work is referenced, survives GC, then releases",
      asyncio.run(_spawn_survives()))

# --- file-tool sandbox: relative traversal blocked ---
check("traversal '../../../Windows/..' blocked", _resolve("../../../Windows/System32/drivers/etc/hosts") is None)
check("traversal '../..' blocked", _resolve("../../AppData/Roaming/JARVIS/config.json") is None)
check("legit 'downloads' resolves", _resolve("downloads") is not None)

# --- close_application: exact/whole-word matching, no fan-out ---
from tools.builtin import close_application  # noqa: E402
# 'close x' (too generic after stopword strip) must refuse, not nuke the desktop
res = close_application("all windows")
check("close 'all windows' refuses", "error" in res, res)

# Windows 11 keeps the frame of a CLOSED UWP app (Settings, Calculator, Store) alive and
# suspended; IsWindowVisible still says True, so JARVIS insisted "you have Settings open"
# for hours after it was closed - twice over, since these apps own two windows each.
# DWM cloaking is the only reliable signal.
from tools.windows_tools import _is_cloaked, _visible_windows  # noqa: E402
import win32gui as _wg  # noqa: E402

_titles = [t for _, t in _visible_windows()]
_cloaked_any = []


def _scan(h, _):
    if _wg.IsWindowVisible(h) and _wg.GetWindowText(h).strip() and _is_cloaked(h):
        _cloaked_any.append(_wg.GetWindowText(h))
    return True


_wg.EnumWindows(_scan, None)
check("cloaked (closed UWP / other-desktop) windows are not reported",
      not any(t in _titles for t in _cloaked_any))
check("real windows are still reported", "JARVIS" in _titles or len(_titles) > 0)

# Re-teaching a phrase that already exists writes a row into the command matrix. That
# matrix is loaded with np.frombuffer, which hands back a READ-ONLY view, so the write
# raised "assignment destination is read-only" and killed the whole turn - the reflex
# fired, then the turn died and turn_done never arrived.
import asyncio as _asyncio  # noqa: E402
from brain.router import brain as _brain  # noqa: E402


async def _reteach():
    await _brain.load()
    steps = [{"skill": "volume_set", "args": {"percent": 35}}]
    await _brain.teach_command("audit selftest phrase", steps)
    await _brain.teach_command("audit selftest phrase", steps)   # the one that used to die
    await _brain.forget_command("audit selftest phrase")
    return True


try:
    check("re-teaching an existing command doesn't hit a read-only matrix",
          _asyncio.run(_reteach()))
except Exception as _e:
    check(f"re-teaching an existing command doesn't hit a read-only matrix ({_e})", False)

print(f"\n{'ALL PASS' if not fails else 'FAILURES: ' + ', '.join(fails)}")
sys.exit(1 if fails else 0)
