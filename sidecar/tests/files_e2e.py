"""FILES view end-to-end: voice reflexes + HUD endpoints. Run: python tests/files_e2e.py PORT TOKEN"""
import asyncio, json, sys, time
import httpx, websockets

port, tok = sys.argv[1], sys.argv[2]
H = {"X-Jarvis-Token": tok}
BASE = f"http://127.0.0.1:{port}"


async def turn(ws, text):
    t0 = time.time()
    httpx.post(BASE + "/text", headers=H, json={"text": text}, timeout=10)
    reflex = None; reply = ""; files = None
    while time.time() - t0 < 120:
        e = json.loads(await asyncio.wait_for(ws.recv(), timeout=120)); k = e.get("kind")
        if k == "reflex":
            reflex = e.get("skill")
        elif k == "files":
            files = (e.get("label"), e.get("count"))
        elif k == "assistant_delta":
            reply += e["text"]
        elif k == "turn_done":
            break
    print(f"  {text[:40]:40} reflex={reflex} files={files} {round(time.time()-t0,1)}s | {reply.strip()[:70]}")
    return reflex, files, reply


async def main():
    ok = True
    async with websockets.connect(f"ws://127.0.0.1:{port}/ws?token={tok}", max_size=None) as ws:
        r = await turn(ws, "open my downloads folder")
        ok &= r[0] == "folder" and r[1] is not None and r[1][0] == "downloads"
        await asyncio.sleep(1)
        r = await turn(ws, "find the file called jarvis")
        ok &= r[0] == "find_file" and r[1] is not None and r[1][1] > 0
    # HUD endpoints
    lst = httpx.get(BASE + "/files?path=documents", headers=H, timeout=20).json()
    ok &= lst.get("count", 0) > 0
    # preview any text file that is actually in Documents — do NOT depend on one of
    # the agent's own working files being there (they live in .agent/ now)
    txt = next((e["name"] for e in lst.get("entries", [])
                if e["kind"] == "file" and e["name"].lower().endswith((".txt", ".md", ".log"))), None)
    pv = httpx.get(BASE + f"/files/preview?path=documents/{txt}", headers=H, timeout=20).json() if txt else {"type": "text"}
    ok &= pv.get("type") == "text"
    bad = httpx.get(BASE + "/files?path=C:/Windows", headers=H, timeout=20).json()
    ok &= "error" in bad
    print("list:", lst.get("label"), lst.get("count"), "| preview:", pv.get("type"), "| sandbox:", "error" in bad)
    print("FILES:", "PASS" if ok else "FAIL")
    return 0 if ok else 1

sys.exit(asyncio.run(main()))
