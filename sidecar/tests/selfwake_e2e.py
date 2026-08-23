"""He must not wake/interrupt himself when his own speech contains 'Jarvis'.
Run: python tests/selfwake_e2e.py PORT TOKEN"""
import asyncio, json, sys, time
import httpx, websockets

port, tok = sys.argv[1], sys.argv[2]
H = {"X-Jarvis-Token": tok}
BASE = f"http://127.0.0.1:{port}"


async def main():
    # unsilence so the speakers really play (that is the whole point of this test)
    httpx.post(BASE + "/debug/silence", headers=H, json={"seconds": 0}, timeout=10)
    async with websockets.connect(f"ws://127.0.0.1:{port}/ws?token={tok}", max_size=None) as ws:
        # a fact he will read back containing his own name, twice
        httpx.post(BASE + "/text", headers=H, json={"text": "remember that my assistant is called Jarvis and I say Jarvis a lot"}, timeout=15)
        t0 = time.time()
        while time.time() - t0 < 60:
            if json.loads(await asyncio.wait_for(ws.recv(), timeout=60)).get("kind") == "turn_done":
                break
        await asyncio.sleep(1)
        httpx.post(BASE + "/text", headers=H, json={"text": "say this sentence out loud exactly: Jarvis is my name, and Jarvis never interrupts Jarvis."}, timeout=15)
        t0, interrupted, wakes, reply = time.time(), False, 0, ""
        while time.time() - t0 < 120:
            e = json.loads(await asyncio.wait_for(ws.recv(), timeout=120))
            k = e.get("kind")
            if k == "interrupted":
                interrupted = True
            elif k == "wake":
                wakes += 1
            elif k == "assistant_delta":
                reply += e["text"]
            elif k == "turn_done":
                break
        await asyncio.sleep(4)          # idle listening right after speech: echo must not wake him
        t1 = time.time()
        while time.time() - t1 < 3:
            try:
                e = json.loads(await asyncio.wait_for(ws.recv(), timeout=1))
                if e.get("kind") == "wake":
                    wakes += 1
            except asyncio.TimeoutError:
                pass
    said_name = "jarvis" in reply.lower()
    ok = said_name and not interrupted and wakes == 0
    print(f"  reply: {reply.strip()[:90]!r}")
    print(f"  said own name={said_name} interrupted={interrupted} wake events={wakes}")
    print("SELF-WAKE:", "PASS" if ok else "FAIL")
    return 0 if ok else 1

sys.exit(asyncio.run(main()))
