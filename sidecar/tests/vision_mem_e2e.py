"""Vision turn + RAM peak + engine survival. Run: python tests/vision_mem_e2e.py PORT TOKEN"""
import asyncio, json, sys, time, threading
import httpx, psutil, websockets

port, tok = sys.argv[1], sys.argv[2]
H = {"X-Jarvis-Token": tok}
BASE = f"http://127.0.0.1:{port}"
samples = []
stop = False


def sampler():
    while not stop:
        samples.append(psutil.virtual_memory().used / 1e9)
        time.sleep(1)


async def turn(ws, text):
    t0 = time.time()
    httpx.post(BASE + "/text", headers=H, json={"text": text}, timeout=10)
    reply = ""; tools = []
    while time.time() - t0 < 180:
        e = json.loads(await asyncio.wait_for(ws.recv(), timeout=180)); k = e.get("kind")
        if k == "tool_call" and e.get("status") == "pending":
            tools.append(e["tool"])
        elif k == "assistant_delta":
            reply += e["text"]
        elif k == "turn_done":
            break
    print(f"  {text[:40]:40} tools={tools} {round(time.time()-t0,1)}s | {reply.strip()[:90]}")
    return reply


async def main():
    global stop
    threading.Thread(target=sampler, daemon=True).start()
    total = psutil.virtual_memory().total / 1e9
    async with websockets.connect(f"ws://127.0.0.1:{port}/ws?token={tok}", max_size=None) as ws:
        r1 = await turn(ws, "what's on my screen right now")
        r2 = await turn(ws, "what's 15 percent of 80")
        r3 = await turn(ws, "describe my screen in one sentence")
    stop = True
    d = httpx.get(BASE + "/diagnostics", headers=H, timeout=60).json()
    eng = next((c for c in d["checks"] if c["name"] == "AI Engine"), {})
    peak = max(samples) if samples else 0
    print(f"RAM peak {peak:.1f} / {total:.1f} GB ({peak/total*100:.0f}%) | engine: {eng.get('status')} {eng.get('detail')}")
    ok = eng.get("status") == "ok" and bool(r2.strip()) and ("12" in r2 or "twelve" in r2.lower()) and peak / total < 0.93
    print("VISION/MEMORY:", "PASS" if ok else "FAIL")
    return 0 if ok else 1

sys.exit(asyncio.run(main()))
