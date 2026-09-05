"""Software end-to-end test of the wake/conversation pipeline.

Requires a sidecar started with JARVIS_DEBUG=1 (audio injection endpoint).
Tests: wake+preroll in one breath, follow-up without wake word, and bare
'Jarvis' (reported, not gated — see the threshold note at T3).
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

    # Synthesise every phrase BEFORE the clock matters. The follow-up used to be
    # synthesised inside the conversation window - Kokoro on a busy CPU took the
    # better part of a second - and the window is FIVE seconds on his machine
    # (conversation.window_s, his call), not the eight this file once assumed.
    # T2 passed alone and failed in the suite for exactly that reason: it was
    # a race against the window, not a bug in the window.
    p_time = phrase("Hey Jarvis, what time is it?")
    p_day = phrase("And what day of the week is it?")
    p_bare = phrase("Jarvis.", voice="bm_lewis")

    async def wait_armed(n_from: int, t: float = 60) -> bool:
        t0 = time.time()
        while time.time() - t0 < t:
            if any(e.get("kind") == "conversation" and e.get("armed") for e in events[n_from:]):
                return True
            await asyncio.sleep(0.05)
        return False

    lt = asyncio.create_task(listen())
    await asyncio.sleep(1)
    async with httpx.AsyncClient(timeout=120) as c:
        await wait_idle(c)   # never start while he is still talking from a previous test
        n0 = len(events)
        await inject(c, p_time)
        armed = await wait_armed(n0)   # the window opens the moment the turn is over
        n1 = len(events)
        wake1 = [e["score"] for e in events if e.get("kind") == "wake"]
        user1 = [e["text"] for e in events if e.get("kind") == "transcript" and e.get("role") == "user"]
        reply1 = "".join(e.get("text", "") for e in events if e.get("kind") == "assistant_delta")
        print("T1 wake:", wake1, "| heard:", user1, "| reply:", reply1[:80])
        t1 = bool(wake1) and any("time" in (u or "").lower() for u in user1)

        win = httpx.get(BASE + "/config", headers=H, timeout=10).json()["config"].get(
            "conversation", {}).get("window_s", 15)
        print(f"T1 window armed: {armed} (window_s {win})")
        # straight in, while the window is open - he does not pause to think
        # between "what time is it" and "and what day is it" either
        await inject(c, p_day)
        await wait_idle(c)
        user2 = [e["text"] for e in events[n1:] if e.get("kind") == "transcript" and e.get("role") == "user"]
        wake2 = [e for e in events[n1:] if e.get("kind") == "wake"]
        reply2 = "".join(e.get("text", "") for e in events[n1:] if e.get("kind") == "assistant_delta")
        print("T2 (no wake word) heard:", user2, "| wake events:", len(wake2), "| reply:", reply2[:80])
        t2 = bool(user2) and not wake2

        await asyncio.sleep(float(win) + 3)  # let the window expire
        n3 = len(events)
        await inject(c, p_bare)
        await wait_idle(c)
        wake3 = [e["score"] for e in events[n3:] if e.get("kind") == "wake"]
        spoke3 = [e.get("text") for e in events[n3:] if e.get("kind") == "speaking"]
        # Bare "Jarvis" is BEST EFFORT, not a promise. The wake threshold was raised
        # to 0.60 on 2026-08-27 (user's call) after ambient room audio woke him at
        # 0.94 while he was alone; the full "hey jarvis" still scores ~0.99, but a
        # bare "Jarvis" scores 0.46-0.98 depending on delivery, so it may not clear
        # the bar. Report the score, don't fail the release on it.
        thr = httpx.get(BASE + "/config", headers=H, timeout=10).json()["config"]["wake"]["threshold"]
        print(f"T3 bare 'Jarvis' wake: {wake3} | spoke: {spoke3} "
              f"| threshold {thr} — best effort, not a gate")
        t3 = True
    lt.cancel()
    print("\nPREROLL:", "PASS" if t1 else "FAIL",
          "| CONVERSATION WINDOW:", "PASS" if t2 else "FAIL",
          "| BARE JARVIS:", "woke" if wake3 else "below threshold (expected at 0.60)")


asyncio.run(main())
