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

    # it must be HIS browser: same profile, and reachable from the taskbar
    import psutil
    profile = (_real_profile() or "").lower()
    ours = [p for p in psutil.process_iter(["name", "cmdline"])
            if (p.info["name"] or "").lower() == "brave.exe"
            and any(profile in (a or "").lower() for a in (p.info["cmdline"] or []))]
    check("it is driving his own Brave profile, not a scratch one", bool(ours))

    # THE thing that made him give up on this: a Brave window appearing and taking the
    # screen while he was talking to JARVIS. Research is meant to happen in the background
    # and be read in the JARVIS panel. Assert it directly rather than trusting the flags.
    check(f"no Brave window is on screen ({visible_brave_windows() or 'none'})",
          not visible_brave_windows())

    # ...and the mirror image: a site he asked to OPEN must land somewhere he can see.
    # Chromium is single-instance per profile, so handing the URL to the running instance
    # put the tab inside JARVIS's hidden window and "open YouTube" did nothing visible.
    from tools.windows_tools import open_url
    open_url("example.com")
    await asyncio.sleep(6)
    shown = visible_brave_windows()
    check(f"a site he asks for opens in a window he can see ({shown or 'NOTHING'})", bool(shown))

    # HIS browser must survive JARVIS letting go of it. The idle reaper used to call
    # close() on the whole context, and main.py closes it on shutdown — which, now that
    # it is his own Brave, meant closing his browser and his tabs out from under him.
    his_tab = await brave_web._ctx.new_page()
    await his_tab.goto("https://example.com", wait_until="domcontentloaded", timeout=25000)
    await brave_web.close()
    await asyncio.sleep(2)
    still_running = bool(brave_pids())
    check("his Brave is still running after JARVIS lets go", still_running)
    try:
        check("and his own tab is untouched", not his_tab.is_closed())
    except Exception:
        check("and his own tab is untouched", False)

    print("\n" + ("BRAVE SEARCH: PASS" if not fails else f"BRAVE SEARCH: FAIL {fails}"))
    sys.stdout.flush()
    os._exit(1 if fails else 0)

asyncio.run(main())
