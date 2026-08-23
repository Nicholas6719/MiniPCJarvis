"""Barge-in: saying 'Jarvis' while he speaks must stop him. Run: python tests/bargein_e2e.py PORT TOKEN [rounds]"""
import asyncio, base64, json, os, sys, time
import numpy as np, httpx, websockets

port, tok = sys.argv[1], sys.argv[2]
rounds = int(sys.argv[3]) if len(sys.argv) > 3 else 2
H = {"X-Jarvis-Token": tok}
BASE = f"http://127.0.0.1:{port}"


def say(text, voice="am_michael"):
    from kokoro_onnx import Kokoro
    d = os.path.expandvars(r"%APPDATA%\JARVIS\voices\kokoro")
    k = Kokoro(os.path.join(d, "kokoro-v1.0.onnx"), os.path.join(d, "voices-v1.0.bin"))
    s, sr = k.create(text, voice=voice)
    idx = np.linspace(0, len(s) - 1, int(len(s) * 16000 / sr)).astype(int)
    return s[idx].astype(np.float32)


async def wait_idle():
    for _ in range(120):
        try:
            if httpx.get(BASE + "/health", timeout=5).json().get("state") == "idle":
                return
        except Exception:
            pass
        await asyncio.sleep(1)


async def main():
    clip = say("Hey Jarvis")
    ok_all = True
    async with websockets.connect(f"ws://127.0.0.1:{port}/ws?token={tok}", max_size=None) as ws:
        for i in range(rounds):
            await wait_idle()
            httpx.post(BASE + "/text", headers=H, json={"text": "explain how a jet engine works in detail, at least eight sentences"}, timeout=15)
            t0, spoke_at, injected_at, interrupted, deltas_after, total_deltas = time.time(), None, None, None, 0, 0
            while time.time() - t0 < 150:
                e = json.loads(await asyncio.wait_for(ws.recv(), timeout=150))
                k = e.get("kind")
                if k == "state" and e.get("state") == "speaking" and spoke_at is None:
                    spoke_at = time.time()
                    await asyncio.sleep(2.0)
                    httpx.post(BASE + "/debug/inject_audio", headers=H, timeout=30,
                               json={"audio_b64": base64.b64encode(clip.tobytes()).decode()})
                    injected_at = time.time()
                elif k == "interrupted":
                    interrupted = round(time.time() - injected_at, 2) if injected_at else -1
                elif k == "assistant_delta":
                    total_deltas += 1
                    if interrupted is not None:
                        deltas_after += 1
                elif k == "turn_done":
                    break
            ok = interrupted is not None and deltas_after == 0
            ok_all &= ok
            print(f"  round {i+1}: {'PASS' if ok else 'FAIL'} spoke_after={spoke_at and round(spoke_at - t0, 1)}s "
                  f"interrupted_after_inject={interrupted}s deltas_after={deltas_after} total_deltas={total_deltas}")
    print("BARGE-IN:", "PASS" if ok_all else "FAIL")
    return 0 if ok_all else 1

sys.exit(asyncio.run(main()))
