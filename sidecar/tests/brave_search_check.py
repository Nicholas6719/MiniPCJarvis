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

    await brave_web.close()
    print("\n" + ("BRAVE SEARCH: PASS" if not fails else f"BRAVE SEARCH: FAIL {fails}"))
    sys.stdout.flush()
    os._exit(1 if fails else 0)

asyncio.run(main())
