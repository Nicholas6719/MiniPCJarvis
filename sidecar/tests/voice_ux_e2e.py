"""Software end-to-end test of the wake/conversation pipeline.

Requires a sidecar started with JARVIS_DEBUG=1 (audio injection endpoint).
Tests: wake+preroll in one breath, follow-up without wake word, bare 'Jarvis'.
"""
import asyncio, base64, json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import httpx, websockets, numpy as np
from kokoro_onnx import Kokoro

PORT = sys.argv[1] if len(sys.argv) > 1 else "8790"
TOKEN = sys.argv[2] if len(sys.argv) > 2 else "devtoken123"
BASE, H = f"http://127.0.0.1:{PORT}", {"X-Jarvis-Token": TOKEN}

d = os.path.expandvars(r"%APPDATA%\JARVIS\voices\kokoro")
k = Kokoro(os.path.join(d, "kokoro-v1.0.onnx"), os.path.join(d, "voices-v1.0.bin"))


def phrase(text, voice="am_michael"):
    s, sr = k.create(text, voice=voice, speed=0.95)
    s = np.asarray(s, dtype=np.float32)
    idx = np.linspace(0, len(s) - 1, int(len(s) * 16000 / sr)).astype(int)
    return np.concatenate([np.zeros(3200, np.float32), s[idx] * 0.8])


async def inject(c, audio):
    return await c.post(f"{BASE}/debug/inject_audio", headers=H,
                        json={"audio_b64": base64.b64encode(audio.tobytes()).decode()})


async def wait_idle(c, t=90):
    t0 = time.time()
    while time.time() - t0 < t:
        st = (await c.get(f"{BASE}/health", headers=H)).json()["state"]
        if st == "idle" and time.time() - t0 > 3:
            return
        await asyncio.sleep(0.4)


async def main():
    events = []

    async def listen():
        async with websockets.connect(f"ws://127.0.0.1:{PORT}/ws?token={TOKEN}") as ws:
            async for m in ws:
                events.append(json.loads(m))

    lt = asyncio.create_task(listen())
    await asyncio.sleep(1)
    async with httpx.AsyncClient(timeout=120) as c:
        await wait_idle(c)   # never start while he is still talking from a previous test
        await inject(c, phrase("Hey Jarvis, what time is it?"))
        await wait_idle(c)
        n1 = len(events)
        wake1 = [e["score"] for e in events if e.get("kind") == "wake"]
        user1 = [e["text"] for e in events if e.get("kind") == "transcript" and e.get("role") == "user"]
        reply1 = "".join(e.get("text", "") for e in events if e.get("kind") == "assistant_delta")
        print("T1 wake:", wake1, "| heard:", user1, "| reply:", reply1[:80])
        t1 = bool(wake1) and any("time" in (u or "").lower() for u in user1)

        await asyncio.sleep(0.8)  # inside the 8 s conversation window
        await inject(c, phrase("And what day of the week is it?"))
        await wait_idle(c)
        user2 = [e["text"] for e in events[n1:] if e.get("kind") == "transcript" and e.get("role") == "user"]
        wake2 = [e for e in events[n1:] if e.get("kind") == "wake"]
        reply2 = "".join(e.get("text", "") for e in events[n1:] if e.get("kind") == "assistant_delta")
        print("T2 (no wake word) heard:", user2, "| wake events:", len(wake2), "| reply:", reply2[:80])
        t2 = bool(user2) and not wake2

        await asyncio.sleep(10)  # let the window expire
        n3 = len(events)
        await inject(c, phrase("Jarvis.", voice="bm_lewis"))
        await wait_idle(c)
        wake3 = [e["score"] for e in events[n3:] if e.get("kind") == "wake"]
        spoke3 = [e.get("text") for e in events[n3:] if e.get("kind") == "speaking"]
        print("T3 bare 'Jarvis' wake:", wake3, "| spoke:", spoke3)
        t3 = bool(wake3)
    lt.cancel()
    print("\nPREROLL:", "PASS" if t1 else "FAIL",
          "| CONVERSATION WINDOW:", "PASS" if t2 else "FAIL",
          "| BARE JARVIS:", "PASS" if t3 else "FAIL")


asyncio.run(main())
