"""Voice yes/no confirmation via audio injection, using the SAFE debug no-op tool
(never touches power state). Run: python tests/voiceconfirm_e2e.py PORT TOKEN"""
import asyncio, base64, json, os, sys, time
import numpy as np, httpx, websockets

port, tok = sys.argv[1], sys.argv[2]
H = {"X-Jarvis-Token": tok}
BASE = f"http://127.0.0.1:{port}"
results = []


def rec(item, ok, detail=""):
    results.append((item, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'}  {item[:46]:46} {str(detail)[:74]}")


def clip(text):
    from kokoro_onnx import Kokoro
    d = os.path.expandvars(r"%APPDATA%\JARVIS\voices\kokoro")
    k = Kokoro(os.path.join(d, "kokoro-v1.0.onnx"), os.path.join(d, "voices-v1.0.bin"))
    s, sr = k.create(text, voice="am_michael")
    idx = np.linspace(0, len(s) - 1, int(len(s) * 16000 / sr)).astype(int)
    return s[idx].astype(np.float32)


def inject(c):
    httpx.post(BASE + "/debug/inject_audio", headers=H, timeout=30,
               json={"audio_b64": base64.b64encode(c.tobytes()).decode()})


async def wait_idle(limit=120):
    for _ in range(limit):
        try:
            if httpx.get(BASE + "/health", timeout=5).json().get("state") == "idle":
                return
        except Exception:
            pass
        await asyncio.sleep(1)


async def trial(ws, answer_clip):
    await wait_idle()
    while True:                     # drain stale events
        try:
            await asyncio.wait_for(ws.recv(), timeout=0.3)
        except asyncio.TimeoutError:
            break
    httpx.post(BASE + "/debug/confirm_test", headers=H, timeout=15)
    approved = None; did = None; injects = 0; t0 = time.time()
    while time.time() - t0 < 60:
        try:
            e = json.loads(await asyncio.wait_for(ws.recv(), timeout=60))
        except asyncio.TimeoutError:
            break
        k = e.get("kind")
        if k == "confirmation_required":
            asyncio.get_event_loop().call_later(4.0, inject, answer_clip)      # after the question
            asyncio.get_event_loop().call_later(15.0, inject, answer_clip)     # and again for attempt 2
        elif k == "confirmation_answered":
            approved = e.get("approved")
        elif k == "tool_call" and e.get("tool") == "_debug_confirm" and e.get("status") == "success":
            did = True
        elif k == "turn_done":
            break
    return approved, did


async def main():
    httpx.post(BASE + "/debug/silence", headers=H, json={"seconds": 500}, timeout=10)
    yes, no = clip("Yes."), clip("No.")
    async with websockets.connect(f"ws://127.0.0.1:{port}/ws?token={tok}", max_size=None) as ws:
        approved, did = await trial(ws, no)
        rec("spoken 'no' declines by voice", approved is False and not did, f"approved={approved} did={did}")
        approved, did = await trial(ws, yes)
        rec("spoken 'yes' approves by voice + tool runs", approved is True and did, f"approved={approved} did={did}")
    print(f"\n  {sum(1 for _, ok in results if ok)}/{len(results)} passed")
    return 0 if all(ok for _, ok in results) else 1

sys.exit(asyncio.run(main()))
