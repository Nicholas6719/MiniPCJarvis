"""Speak-before-thinking + open_site direct check. Run: python tests/filler_e2e.py PORT TOKEN"""
import asyncio, json, sys, time
import httpx, websockets

port, tok = sys.argv[1], sys.argv[2]
H = {"X-Jarvis-Token": tok}
BASE = f"http://127.0.0.1:{port}"


async def one(ws, text):
    t0 = time.time()
    httpx.post(BASE + "/text", headers=H, json={"text": text}, timeout=10)
    filler = None; filler_at = None; first = None; reflex = None; mode = None; reply = ""; tools = []
    while time.time() - t0 < 120:
        e = json.loads(await asyncio.wait_for(ws.recv(), timeout=120)); k = e.get("kind")
        if k == "filler":
            filler, filler_at = e["text"], round(time.time() - t0, 2)
        elif k == "reflex":
            reflex, mode = e.get("skill"), e.get("mode")
        elif k == "tool_call" and e.get("status") == "pending":
            tools.append(e["tool"])
        elif k == "assistant_delta":
            first = first or round(time.time() - t0, 2); reply += e["text"]
        elif k == "turn_done":
            break
    print(f"  {text[:40]:40} filler={filler!r}@{filler_at}s reflex={reflex}/{mode} tools={tools} first={first}s total={round(time.time()-t0,1)}s | {reply.strip()[:50]}")


async def main():
    async with websockets.connect(f"ws://127.0.0.1:{port}/ws?token={tok}", max_size=None) as ws:
        for t in ["why is the sky blue", "search the web for the tallest building in the world",
                  "open youtube.com", "what time is it"]:
            await one(ws, t)
            await asyncio.sleep(1.5)

asyncio.run(main())
