"""Teach-by-voice, routines and corrections against the running app.
Run: python tests/teach_e2e.py PORT TOKEN"""
import asyncio, json, sys, time
import httpx, websockets

port, tok = sys.argv[1], sys.argv[2]
H = {"X-Jarvis-Token": tok}
BASE = f"http://127.0.0.1:{port}"


async def turn(ws, text):
    t0 = time.time()
    httpx.post(BASE + "/text", headers=H, json={"text": text}, timeout=10)
    reflex = None; mode = None; reply = ""; tools = []; learned = []
    while time.time() - t0 < 120:
        e = json.loads(await asyncio.wait_for(ws.recv(), timeout=120)); k = e.get("kind")
        if k == "reflex":
            reflex, mode = e.get("skill"), e.get("mode")
        elif k == "tool_call" and e.get("status") == "pending":
            tools.append(e["tool"])
        elif k == "brain_learned":
            learned.append(f"{e.get('text')}->{e.get('skill')}")
        elif k == "assistant_delta":
            reply += e["text"]
        elif k == "turn_done":
            break
    print(f"  {text[:46]:46} reflex={reflex}/{mode} tools={tools} learned={learned} {round(time.time()-t0,1)}s | {reply.strip()[:70]}")
    return reflex, tools, reply


async def main():
    ok = True
    async with websockets.connect(f"ws://127.0.0.1:{port}/ws?token={tok}", max_size=None) as ws:
        r = await turn(ws, "when I say test mode, set the volume to 35 and take a screenshot")
        ok &= r[0] == "teach" and "Got it" in r[2]
        await asyncio.sleep(1)
        r = await turn(ws, "test mode")
        ok &= r[0] == "command" and r[1] == ["set_volume", "take_screenshot"]
        await asyncio.sleep(1)
        r = await turn(ws, "no, I meant what time is it")
        ok &= r[0] == "correction" and ("AM" in r[2] or "PM" in r[2])
        await asyncio.sleep(1)
        r = await turn(ws, "test mode")   # forgotten by the correction
        ok &= r[0] != "command"
    b = httpx.get(BASE + "/brain", headers=H).json()
    print("commands now:", b.get("commands"))
    print("TEACH/ROUTINE/CORRECTION:", "PASS" if ok else "FAIL")
    return 0 if ok else 1

sys.exit(asyncio.run(main()))
