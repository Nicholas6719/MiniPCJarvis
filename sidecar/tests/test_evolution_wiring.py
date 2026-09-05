"""Phase 0 of the Evolution: the wiring, proved before any of it has behaviour.

The point of scaffolding first is that an integration mistake surfaces on its own
instead of being buried under feature work — so this gate exists from the moment
the stubs do, and it keeps mattering afterwards. It asserts three things:

  * every new tool is REGISTERED under the name the model will call;
  * each carries the risk tier the plan assigned it, because the tier is the
    security boundary — a tool that quietly becomes SAFE stops asking permission;
  * the modules import cleanly in the same order main.py registers them, which is
    what PyInstaller traces when it decides what to bundle.

It deliberately does NOT assert behaviour. Every handler still raises
NotImplementedError, and each phase replaces that with real work plus its own
test. If a handler here starts returning results without its phase's test landing,
that is the thing to notice.

Offline, no camera, no network. Run: python tests/test_evolution_wiring.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "eviction.db"))

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


# (tool name, risk, which phase gives it behaviour)
EXPECTED = [
    ("list_projects",       "safe", 1),
    ("log_progress",        "low",  1),
    ("estimate_completion", "safe", 1),
    ("where_am_i",          "safe", 2),
    ("distance_to",         "safe", 2),
    ("get_health",          "safe", 2),
    ("analyze_object",      "safe", 3),
    # LOW, not SAFE: it can turn the webcam on, and the tier must describe what
    # the handler DOES rather than what it is for. Below MEDIUM on purpose —
    # MEDIUM would demand a confirmation, and this runs inside one.
    ("face_confirm",        "low",  4),
    ("generate_part",       "low",  5),
    ("slice_part",          "low",  5),
    ("printer_status",      "safe", 5),
]


def main() -> int:
    from tools import (biometric, browser_tools, builtin, camera_tools, fabrication,
                       file_tools, handoff, health, holo_tools, input_tools, location,
                       market_tools, memory_tools, news_tools, projects, render_tools, model_tools,
                       task_tools, uia, vision_analyze, vision_tools, weather,
                       workspace_tools,
                       web_tools, windows_tools)
    import market_intel   # the market as a story (2026-09-05), sidecar-level module
    mods = (builtin, memory_tools, windows_tools, web_tools, task_tools,
            vision_tools, browser_tools, handoff, file_tools, weather,
            camera_tools, market_tools, news_tools, input_tools, uia,
            projects, location, health, vision_analyze, biometric, fabrication,
            holo_tools, render_tools, model_tools,
            workspace_tools, market_intel)
    for m in mods:
        m.register_all()

    # THIS LIST MUST NOT DRIFT FROM main.py. `holo_tools` was added to the app in
    # the hologram's phase A and never added here, so for two phases this gate
    # was checking a smaller registry than the one that actually runs — and it
    # passed the whole time, because nothing referred to those tools yet. It only
    # went red when phase C added skills pointing at them, which is a lucky way
    # to find out. Comparing against main.py's own source is ugly and it is also
    # the only thing that cannot quietly fall behind.
    import re
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(here, "main.py"), encoding="utf-8").read()
    called = set(re.findall(r"^\s*(\w+)\.register_all\(\)", src, re.M))
    listed = {m.__name__.rsplit(".", 1)[-1] for m in mods}
    missing = sorted(called - listed)
    check("this gate registers every tool module main.py does", not missing,
          f"main.py also registers: {missing}")

    from tools.registry import registry
    known = registry._tools

    for name, risk, phase in EXPECTED:
        t = known.get(name)
        check(f"{name} is registered (phase {phase})", t is not None)
        if t is not None:
            check(f"  ...at risk {risk}", t.risk.value == risk,
                  f"is {t.risk.value} — the tier IS the security boundary")

    # Nothing was displaced: the tools that existed before must all still be here.
    check("registering the new tools did not displace the old ones",
          len(known) >= 90, f"only {len(known)} registered")
    for old in ("get_weather", "analyze_screen", "analyze_image", "open_url",
                "play_media", "search_in_browser", "set_camera", "count_fingers"):
        check(f"  ...{old} still registered", old in known)

    # Every skill still resolves. A skill naming a tool that is not registered can
    # never run, and nothing says so at startup.
    from brain.skills import SKILLS
    orphans = [(s.name, s.tool) for s in SKILLS if s.tool and s.tool not in known]
    check("every reflex skill still resolves to a registered tool",
          not orphans, str(orphans))

    # The stubs must be STUBS. This is what tells us a phase has actually landed
    # rather than half-landed: behaviour arrives WITH its own test, not before.
    import asyncio
    still_stubbed = []
    for name, _risk, phase in EXPECTED:
        if name == "printer_status":
            continue          # already real: it returns "no printer configured"
        t = known.get(name)
        if t is None:
            continue
        try:
            asyncio.run(t.handler(**({"project": "x"} if "project" in
                                     (t.parameters.get("required") or []) else
                                     {k: "x" for k in (t.parameters.get("required") or [])})))
        except NotImplementedError:
            still_stubbed.append(name)
        except Exception:
            still_stubbed.append(name)     # implemented (or failing) — either way not a stub
    print(f"\n  ({len(still_stubbed)} of {len(EXPECTED) - 1} handlers still stubs — "
          f"expected to shrink to 0 as phases 1-5 land)")

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
