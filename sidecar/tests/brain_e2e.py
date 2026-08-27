"""Brain reflex end-to-end against a running app: sends typed turns, watches the
event stream, reports whether each was a reflex (no LLM) and how fast.
Run: python tests/brain_e2e.py PORT TOKEN"""
import asyncio, json, sys, time
import httpx, websockets

port, tok = sys.argv[1], sys.argv[2]
H = {"X-Jarvis-Token": tok}
BASE = f"http://127.0.0.1:{port}"

CASES = [
    ("what time is it", "time"),
    ("what's the date today", "date"),
    ("set the volume to 40 percent", "volume_set"),
    ("how's the computer doing", "stats"),
    ("what windows do i have open", "windows"),
    ("remember that i like my coffee black", "remember"),
    ("remind me in 90 minutes to stretch", "reminder"),
    ("tell me a one sentence fun fact about octopuses", "general"),   # LLM path, brain flags it general
]


async def one(ws, text, want):
    t0 = time.time()
    for _ in range(60):              # wait out a busy app instead of aborting the suite
        r = httpx.post(BASE + "/text", headers=H, json={"text": text}, timeout=10).json()
        if r.get("ok"):
            break
        await asyncio.sleep(1)
    assert r.get("ok"), r
    reflex = None; first = None; reply = ""; done = None
    while time.time() - t0 < 120:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=120)
        except asyncio.TimeoutError:
            break
        e = json.loads(raw)
        k = e.get("kind")
        if k == "reflex":
            reflex = e["skill"]
        elif k == "assistant_delta":
            if first is None:
                first = time.time() - t0
            reply += e["text"]
        elif k == "turn_done":
            done = time.time() - t0
            break
    ok = reflex == want
    print(f"  {'PASS' if ok else 'FAIL'} {text[:44]:44} reflex={str(reflex):10} first={first and round(first,2)}s total={done and round(done,1)}s | {reply.strip()[:70]}")
    return ok


async def main():
    async with websockets.connect(f"ws://127.0.0.1:{port}/ws?token={tok}", max_size=None) as ws:
        results = []
        for text, want in CASES:
            results.append(await one(ws, text, want))
            await asyncio.sleep(1.5)
    # CLEAN UP AFTER OURSELVES: this suite sets a real 90-minute reminder on the
    # user's real machine. Eight test runs on 2026-08-27 meant eight surprise
    # "stretch" alerts on his phone through the evening. Tests must not leak.
    try:
        pend = httpx.get(BASE + "/tasks", headers=H).json().get("tasks", [])
        for t in pend:
            if "stretch" in (t.get("text") or "").lower():
                httpx.delete(f"{BASE}/tasks/{t['id']}", headers=H, timeout=10)
                print(f"  cleaned up test reminder #{t['id']}")
    except Exception as e:
        print(f"  WARNING could not clean up the test reminder: {e}")
    m = httpx.get(BASE + "/metrics", headers=H).json()
    print("\nmetrics:", json.dumps(m["summary"]))
    b = httpx.get(BASE + "/brain", headers=H).json()
    print("brain:", b["examples"], "examples", b["stats"])
    print(f"\n{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1

sys.exit(asyncio.run(main()))
