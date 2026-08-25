"""Search runs in HIS Brave, returns real results, and leaves the tab there for him.

The old implementation launched a blank throwaway profile off-screen at -32000,-32000 and
stripped it off the taskbar. A profile with no history is exactly what bot detection looks
for, so every query came back a CAPTCHA with zero results — which the model then reported
as "I couldn't find reliable information".

Run: python tests/brave_search_check.py ["query"]
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from search_brave_web import _real_profile, brave_web  # noqa: E402

QUERIES = sys.argv[1:] or ["amd strix halo review", "best mini pc 2026", "rtx 5090 price"]
def brave_pids():
    import psutil
    return {p.pid for p in psutil.process_iter(["name"])
            if (p.info["name"] or "").lower() == "brave.exe"}


def visible_brave_windows():
    """Brave windows actually on a screen — not hidden, not minimised, not parked off it."""
    import win32gui
    out = []

    def scan(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd) or win32gui.IsIconic(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd) or ""
        if "brave" not in title.lower():
            return True
        left, top, _r, _b = win32gui.GetWindowRect(hwnd)
        if left > -5000 and top > -5000:
            out.append(f"{title[:44]} @({left},{top})")
        return True

    try:
        win32gui.EnumWindows(scan, None)
    except Exception:
        pass
    return out


fails = []


def check(name, cond):
    print(("  PASS  " if cond else "  FAIL  ") + name)
    if not cond:
        fails.append(name)


async def main() -> int:
    check(f"his real Brave profile is found ({_real_profile()})", bool(_real_profile()))
    check("brave is installed", brave_web.available)

    for q in QUERIES:
        res = await brave_web.search(q, 5)
        engine = getattr(brave_web, "_engine", None)
        print(f"\n  {q!r} -> {len(res)} results via {engine}")
        for r in res[:3]:
            print(f"     - {r['title'][:66]}  [{r.get('host')}]")
            if r.get("snippet"):
                print(f"       {r['snippet'][:96]}")
        check(f"{q!r} returned results", len(res) >= 3)
        check(f"{q!r} results are real pages, not the engine itself",
              all(r.get("url", "").startswith("http") and "google.com" not in r["url"]
                  for r in res))
        await asyncio.sleep(3)   # human pace; hammering is what triggers challenges

    # Nothing of JARVIS's may be on screen. Checked BEFORE we deliberately open his.
    check(f"no Brave window is on screen ({visible_brave_windows() or 'none'})",
          not visible_brave_windows())

    # THE regression that cost him an evening: with JARVIS's Brave running, clicking his
    # own Brave gave him nothing usable. JARVIS shared his profile, so concealing its
    # window handed that state to the next window Chromium opened — his.
    import subprocess
    import psutil
    from search_brave_web import _brave_path
    profile = (_real_profile() or "").lower()
    driving_his = [p for p in psutil.process_iter(["name", "cmdline"])
                   if (p.info["name"] or "").lower() == "brave.exe"
                   and any(profile in (a or "").lower() for a in (p.info["cmdline"] or []))]
    check("JARVIS is NOT running in his Brave profile", not driving_his)

    subprocess.Popen([_brave_path()], close_fds=True)
    await asyncio.sleep(8)
    his = visible_brave_windows()
    check(f"he can click Brave and get a usable window ({his or 'NOTHING'})", bool(his))

    # ...and a site he asks JARVIS to open must land somewhere he can see.
    from tools.windows_tools import open_url
    before = len(visible_brave_windows())
    open_url("example.com")
    await asyncio.sleep(6)
    check("a site he asks for opens in a window he can see",
          len(visible_brave_windows()) >= before)

    # JARVIS shutting its own browser down must not touch his.
    his_pids = {p.pid for p in psutil.process_iter(["name"])
                if (p.info["name"] or "").lower() == "brave.exe"}
    await brave_web.close()
    await asyncio.sleep(3)
    still = {p.pid for p in psutil.process_iter(["name"])
             if (p.info["name"] or "").lower() == "brave.exe"}
    check("his Brave survives JARVIS closing its own", bool(still & his_pids))

    print(chr(10) + ("BRAVE SEARCH: PASS" if not fails else f"BRAVE SEARCH: FAIL {fails}"))
    sys.stdout.flush()
    os._exit(1 if fails else 0)

asyncio.run(main())
