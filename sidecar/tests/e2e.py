"""End-to-end tests against a RUNNING JARVIS instance (default: the installed app).

Drives the production sidecar over its real API, listens to the real event
stream, verifies real side effects (volume, clipboard, files, processes).

Usage: .venv/Scripts/python.exe tests/e2e.py --port 58638 --token <token>
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent.parent))

BASE = ""
TOKEN = ""
EVENTS: list[dict] = []
RESULTS: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    line = f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else "")
    # Windows console may be cp1252 — never let a fancy character kill the run
    print(line.encode("ascii", errors="replace").decode())


async def api(method: str, path: str, body: dict | None = None) -> dict:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.request(method, f"{BASE}{path}",
                            json=body,
                            headers={"X-Jarvis-Token": TOKEN})
        r.raise_for_status()
        return r.json()


async def state() -> str:
    return (await api("GET", "/health"))["state"]


async def wait_state(targets: set[str], timeout: float = 90) -> str:
    t0 = time.time()
    while time.time() - t0 < timeout:
        s = await state()
        if s in targets:
            return s
        await asyncio.sleep(0.5)
    return await state()


async def wait_idle(timeout: float = 90) -> bool:
    return (await wait_state({"idle"}, timeout)) == "idle"


async def turn(text: str, timeout: float = 90) -> float:
    """Send a text turn, wait for completion; returns wall seconds."""
    t0 = time.time()
    r = await api("POST", "/text", {"text": text})
    if not r.get("ok"):
        print(f"  [warn] turn rejected ({r}) — recovering with /interrupt")
        await api("POST", "/interrupt")
        await wait_idle(15)
        r = await api("POST", "/text", {"text": text})
        if not r.get("ok"):
            return -1.0
    await asyncio.sleep(1.5)
    if not await wait_idle(timeout):
        print(f"  [warn] turn stuck in {await state()} — recovering with /interrupt")
        await api("POST", "/interrupt")
        await wait_idle(15)
        return -1.0
    return time.time() - t0


def recent_events(kind: str, n: int = 5) -> list[dict]:
    return [e for e in EVENTS if e.get("kind") == kind][-n:]


async def event_listener() -> None:
    import websockets
    url = BASE.replace("http", "ws") + f"/ws?token={TOKEN}"
    async with websockets.connect(url) as ws:
        async for msg in ws:
            try:
                EVENTS.append(json.loads(msg))
            except json.JSONDecodeError:
                pass


async def main() -> None:
    global BASE, TOKEN
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", required=True)
    ap.add_argument("--token", required=True)
    args = ap.parse_args()
    BASE = f"http://127.0.0.1:{args.port}"
    TOKEN = args.token

    listener = asyncio.create_task(event_listener())
    await asyncio.sleep(1)

    print("== JARVIS E2E against", BASE, "==")

    # 1. health
    h = await api("GET", "/health")
    record("health endpoint", h.get("ok") is True, f"state={h.get('state')}")

    # 2. plain QA
    secs = await turn("What is the capital of Japan? One short sentence.")
    reply = "".join(e.get("text", "") for e in EVENTS if e.get("kind") == "assistant_delta")
    record("plain QA turn", "tokyo" in reply.lower(), f"{secs:.1f}s")

    # 3. system stats tool
    n_tools = len(recent_events("tool_call", 99))
    secs = await turn("How much RAM am I using?")
    calls = [e for e in recent_events("tool_call", 99)[n_tools:] if e.get("tool") == "get_system_stats"]
    record("system stats tool", any(c.get("status") == "success" for c in calls), f"{secs:.1f}s")

    # 4. volume round-trip with real verification
    from tools.windows_tools import get_volume, set_volume
    orig = get_volume()["volume_percent"]
    await turn("Set the system volume to exactly 30 percent.")
    now = get_volume()["volume_percent"]
    record("volume set via voice pipeline", now == 30, f"actual={now}")
    set_volume(orig)

    # 5. clipboard
    await turn("Copy exactly the phrase 'jarvis e2e ok' to my clipboard.")
    from tools.windows_tools import get_clipboard
    clip = (get_clipboard().get("text") or "").lower()
    record("clipboard via voice pipeline", "jarvis e2e ok" in clip, f"clip={clip[:40]!r}")

    # 6. screenshot
    shots_dir = Path(os.path.expandvars(r"%APPDATA%\JARVIS\screenshots"))
    before = set(shots_dir.glob("*.png")) if shots_dir.exists() else set()
    await turn("Take a screenshot.")
    after = set(shots_dir.glob("*.png")) if shots_dir.exists() else set()
    record("screenshot via voice pipeline", len(after) > len(before),
           f"{len(after) - len(before)} new file(s)")

    # 7. open app (LOW) then close (MEDIUM -> approve confirmation)
    await turn("Open notepad.")
    await asyncio.sleep(2)
    import psutil
    notepad_up = any(p.info["name"] and p.info["name"].lower() == "notepad.exe"
                     for p in psutil.process_iter(["name"]))
    record("open_application notepad", notepad_up)

    async def approver():
        for _ in range(120):
            confirms = recent_events("confirmation_required", 3)
            fresh = [c for c in confirms if time.time() - c["ts"] < 30]
            if fresh:
                await api("POST", "/confirm",
                          {"confirm_id": fresh[-1]["confirm_id"], "approved": True})
                return True
            await asyncio.sleep(0.5)
        return False

    approve_task = asyncio.create_task(approver())
    await turn("Close notepad now, yes I'm sure.")
    approved = await approve_task
    await asyncio.sleep(2)
    notepad_gone = not any(p.info["name"] and p.info["name"].lower() == "notepad.exe"
                           for p in psutil.process_iter(["name"]))
    record("confirmation APPROVE path + close_application",
           approved and notepad_gone, f"approved={approved} gone={notepad_gone}")

    # 8. memory write + recall
    await turn("Remember that my favorite color is dark blue.")
    import sqlite3
    db = sqlite3.connect(os.path.expandvars(r"%APPDATA%\JARVIS\jarvis.db"))
    row = db.execute("SELECT content FROM memories WHERE content LIKE '%dark blue%' "
                     "ORDER BY id DESC LIMIT 1").fetchone()
    record("memory persisted", row is not None, (row or ("",))[0][:50])
    marker = len(EVENTS)
    await turn("What's my favorite color?")
    reply = "".join(e.get("text", "") for e in EVENTS[marker:] if e.get("kind") == "assistant_delta")
    record("memory recall in conversation", "blue" in reply.lower(), reply[:60])

    # 9. web search without key -> graceful
    marker = len(EVENTS)
    await turn("Search the web for the weather in Boston.")
    reply = "".join(e.get("text", "") for e in EVENTS[marker:] if e.get("kind") == "assistant_delta")
    graceful = any(w in reply.lower() for w in ("key", "config", "search", "unable", "isn't", "not"))
    record("web search w/o key degrades gracefully", graceful and len(reply) > 0, reply[:80])

    # 10. fetch_page summarize
    marker = len(EVENTS)
    secs = await turn("Fetch the page https://en.wikipedia.org/wiki/AMD_Ryzen and tell me in one sentence what it's about.", timeout=120)
    reply = "".join(e.get("text", "") for e in EVENTS[marker:] if e.get("kind") == "assistant_delta")
    record("fetch_page + summarize", any(w in reply.lower() for w in ("ryzen", "amd", "processor", "cpu")),
           f"{secs:.1f}s — {reply[:70]}")

    # 10b. reminder via voice pipeline
    await turn("Set a reminder for 9 PM tonight to stretch my legs.")
    import sqlite3 as _sq
    db2 = _sq.connect(os.path.expandvars(r"%APPDATA%\JARVIS\jarvis.db"))
    trow = db2.execute("SELECT text, status FROM tasks WHERE text LIKE '%stretch%' "
                       "ORDER BY id DESC LIMIT 1").fetchone()
    record("reminder set via voice pipeline", trow is not None and trow[1] == "pending",
           str(trow))

    # 10c. vision: analyze screen via voice pipeline (loads vision model ~15s)
    marker = len(EVENTS)
    secs = await turn("Look at my screen and tell me what you see in one sentence.",
                      timeout=150)
    reply = "".join(e.get("text", "") for e in EVENTS[marker:] if e.get("kind") == "assistant_delta")
    vcalls = [e for e in recent_events("tool_call", 10)
              if e.get("tool") == "analyze_screen" and e.get("status") == "success"]
    record("vision analyze_screen via voice pipeline",
           bool(vcalls) and len(reply) > 10, f"{secs:.1f}s — {reply[:70]}")

    # 11. interruption mid-speech
    await api("POST", "/text", {"text": "Count slowly from one to thirty, saying each number."})
    reached = await wait_state({"speaking"}, timeout=60)
    if reached == "speaking":
        await api("POST", "/interrupt")
        s = await wait_state({"idle", "interrupted", "listening"}, timeout=10)
        record("interrupt while speaking", s in ("idle", "interrupted", "listening"), f"state={s}")
        # listening may hang for PTT window; force back
        await api("POST", "/interrupt")
    else:
        record("interrupt while speaking", False, f"never reached speaking (got {reached})")
    await wait_idle(30)

    # 12. latency summary
    lats = [e["latency_ms"] for e in EVENTS if e.get("kind") == "turn_done" and e.get("latency_ms")]
    if lats:
        print(f"\n  turn latencies (ms, incl. speech playback): min={min(lats)} "
              f"median={sorted(lats)[len(lats)//2]} max={max(lats)}")

    listener.cancel()
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n== {passed}/{len(RESULTS)} PASSED ==")
    out = Path(__file__).parent / "e2e-results.json"
    out.write_text(json.dumps(
        [{"test": n, "ok": ok, "detail": d} for n, ok, d in RESULTS], indent=2), "utf-8")


if __name__ == "__main__":
    asyncio.run(main())
