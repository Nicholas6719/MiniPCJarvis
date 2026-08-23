"""Risk-gated confirmations, answered by voice/text. Run: python tests/confirm_e2e.py PORT TOKEN"""
import asyncio, json, sys, time
import httpx, psutil, websockets

port, tok = sys.argv[1], sys.argv[2]
H = {"X-Jarvis-Token": tok}
BASE = f"http://127.0.0.1:{port}"
results = []


def rec(item, ok, detail=""):
    results.append((item, bool(ok), str(detail)[:150]))
    print(f"  {'PASS' if ok else 'FAIL'}  {item[:50]:50} {str(detail)[:85]}")


async def wait_idle(limit=90):
    t0 = time.time()
    while time.time() - t0 < limit:
        try:
            if httpx.get(BASE + "/health", timeout=5).json().get("state") == "idle":
                return True
        except Exception:
            pass
        await asyncio.sleep(1)
    return False


def calcs():
    return [p.pid for p in psutil.process_iter(["name"]) if "calc" in (p.info["name"] or "").lower()]


async def run(ws, text, answer=None, timeout=180):
    """Send a turn; when a confirmation appears, answer it with `answer` (yes/no)."""
    t0 = time.time()
    httpx.post(BASE + "/text", headers=H, json={"text": text}, timeout=15)
    ev = {"confirm": None, "asked_aloud": "", "state_waiting": False, "reply": "", "answered": None}
    while time.time() - t0 < timeout:
        try:
            e = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
        except asyncio.TimeoutError:
            break
        k = e.get("kind")
        if k == "confirmation_required":
            ev["confirm"] = e.get("tool")
            if answer is not None:
                await asyncio.sleep(2.5)          # let him finish asking out loud
                r = httpx.post(BASE + "/text", headers=H, json={"text": answer}, timeout=15).json()
                ev["answered"] = r
        elif k == "state" and e.get("state") == "waiting":
            ev["state_waiting"] = True
        elif k == "assistant_delta":
            ev["reply"] += e["text"]
        elif k == "turn_done":
            break
    ev["total"] = round(time.time() - t0, 1)
    return ev


async def main():
    async with websockets.connect(f"ws://127.0.0.1:{port}/ws?token={tok}", max_size=None) as ws:
        await wait_idle()
        await run(ws, "open calculator")
        await asyncio.sleep(6)
        before = calcs()
        rec("calculator is running (setup)", bool(before), before)

        # --- decline by voice ---
        await wait_idle()
        ev = await run(ws, "close calculator", answer="no")
        await asyncio.sleep(1.5)
        rec("confirmation is requested for a MEDIUM action", ev["confirm"] == "close_application", ev["confirm"])
        rec("he asks out loud (was silent before)", "say yes or no" in ev["reply"].lower(), ev["reply"][:70])
        rec("orb shows WAITING while asking", ev["state_waiting"], ev["state_waiting"])
        rec("spoken 'no' declines it", bool(calcs()) and "leaving it open" in ev["reply"].lower(), f"still running={bool(calcs())} | {ev['reply'][-60:]}")

        # --- approve by voice ---
        await wait_idle()
        ev = await run(ws, "close calculator", answer="yes")
        await asyncio.sleep(2)
        rec("spoken 'yes' approves and it closes", not calcs(), f"after={calcs()} | {ev['reply'][-70:]}")

        # --- unrelated speech must NOT approve ---
        await wait_idle()
        await run(ws, "open calculator")
        await asyncio.sleep(6)
        ev = await run(ws, "close calculator", answer="what time is it", timeout=150)
        rec("unrelated speech never approves a risky action", bool(calcs()), f"still running={bool(calcs())}")
        # clean up: approve it now
        await wait_idle(150)
        ev = await run(ws, "close calculator", answer="yes")
        await asyncio.sleep(2)
        rec("cleanup: calculator closed", not calcs(), calcs())

    print(f"\n  TOTAL {sum(1 for r in results if r[1])}/{len(results)}")
    for i, ok, d in results:
        if not ok:
            print(f"    FAIL {i} :: {d}")
    return 0 if all(r[1] for r in results) else 1

sys.exit(asyncio.run(main()))
