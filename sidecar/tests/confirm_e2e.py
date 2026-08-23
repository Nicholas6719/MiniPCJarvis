"""Risk-gated confirmations answered by voice (MEDIUM action = recycle a file).
Run: python tests/confirm_e2e.py PORT TOKEN"""
import asyncio, json, os, sys, time
import httpx, websockets

port, tok = sys.argv[1], sys.argv[2]
H = {"X-Jarvis-Token": tok}
BASE = f"http://127.0.0.1:{port}"
DOCS = r"C:\Users\nicho\Documents"
FNAME = "jarvis_confirm_test.txt"
results = []


def rec(item, ok, detail=""):
    results.append((item, bool(ok), str(detail)[:150]))
    print(f"  {'PASS' if ok else 'FAIL'}  {item[:50]:50} {str(detail)[:85]}")


async def wait_idle(limit=150):
    t0 = time.time()
    while time.time() - t0 < limit:
        try:
            if httpx.get(BASE + "/health", timeout=5).json().get("state") == "idle":
                return True
        except Exception:
            pass
        await asyncio.sleep(1)
    return False


def exists():
    return os.path.exists(os.path.join(DOCS, FNAME))


async def run(ws, text, answer=None, timeout=200):
    while True:                      # drain events left over from a previous turn
        try:
            await asyncio.wait_for(ws.recv(), timeout=0.3)
        except asyncio.TimeoutError:
            break
    t0 = time.time()
    httpx.post(BASE + "/text", headers=H, json={"text": text}, timeout=15)
    ev = {"confirm": None, "waiting": False, "reply": "", "answered": None, "tools": []}
    while time.time() - t0 < timeout:
        try:
            e = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
        except asyncio.TimeoutError:
            break
        k = e.get("kind")
        if k == "confirmation_required":
            ev["confirm"] = e.get("tool")
            if answer is not None:
                await asyncio.sleep(3.0)
                ev["answered"] = httpx.post(BASE + "/text", headers=H, json={"text": answer}, timeout=15).json()
        elif k == "state" and e.get("state") == "waiting":
            ev["waiting"] = True
        elif k == "tool_call" and e.get("status") == "pending":
            ev["tools"].append(e["tool"])
        elif k == "assistant_delta":
            ev["reply"] += e["text"]
        elif k == "turn_done":
            break
    return ev


async def main():
    """Only shutdown/restart still confirm. We NEVER answer yes here."""
    open(os.path.join(DOCS, FNAME), "w").write("confirmation test")
    async with websockets.connect(f"ws://127.0.0.1:{port}/ws?token={tok}", max_size=None) as ws:
        await wait_idle()
        ev = await run(ws, "restart the computer", answer="no")
        rec("shutdown/restart asks for confirmation", ev["confirm"] == "power_action", f"confirm={ev['confirm']} tools={ev['tools']}")
        rec("he asks out loud", "say yes or no" in ev["reply"].lower(), ev["reply"][:80])
        rec("orb shows WAITING", ev["waiting"], ev["waiting"])
        rec("spoken 'no' declines and he stops asking", "leaving it" in ev["reply"].lower() and "?" not in ev["reply"].split("Say yes or no.")[-1], ev["reply"][-90:])
        await wait_idle()
        ev = await run(ws, "restart the computer", answer="what time is it")
        rec("unrelated speech = implicit no (then he answers it)", ev["confirm"] == "power_action" and "leaving it" in ev["reply"].lower(), f"confirm={ev['confirm']} | {ev['reply'][-60:]}")
        await wait_idle(); await asyncio.sleep(8); await wait_idle()   # let the implicit-no answer finish
        ev = await run(ws, f"send the file {FNAME} in my documents to the recycle bin")
        rec("recycling a file needs NO confirmation now", ev["confirm"] is None and not exists(), f"confirm={ev['confirm']} exists={exists()} | {ev['reply'][:60]}")

    print(f"  TOTAL {sum(1 for r in results if r[1])}/{len(results)}")
    for i, ok, d in results:
        if not ok:
            print(f"    FAIL {i} :: {d}")
    await wait_idle()
    return 0 if all(r[1] for r in results) else 1

sys.exit(asyncio.run(main()))
