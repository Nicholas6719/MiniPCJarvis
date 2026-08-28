"""Does semantic end-of-turn actually change anything, on the real app?

The unit gate proves the DECISION is right. This proves the decision reaches the
microphone loop: a finished sentence must be cut off after a fraction of a
second of silence, a sentence left dangling must be given far longer, and the
transcript the endpoint check already produced must be reused instead of running
Parakeet over the same audio a second time.

Asserts on the numbers JARVIS reports himself (silence waited, budget, stt_ms)
rather than wall-clock here: injected audio is paced with asyncio.sleep, and
Windows' ~16 ms timer granularity drifts hundreds of milliseconds over a clip.

Requires JARVIS_DEBUG=1. Run: python tests/endpoint_e2e.py PORT TOKEN
"""
import asyncio
import base64
import json
import os
import sys
import time

import httpx
import numpy as np
import websockets
from kokoro_onnx import Kokoro

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PORT = sys.argv[1] if len(sys.argv) > 1 else "8790"
TOKEN = sys.argv[2] if len(sys.argv) > 2 else "devtoken123"
BASE, H = f"http://127.0.0.1:{PORT}", {"X-Jarvis-Token": TOKEN}

d = os.path.expandvars(r"%APPDATA%\JARVIS\voices\kokoro")
k = Kokoro(os.path.join(d, "kokoro-v1.0.onnx"), os.path.join(d, "voices-v1.0.bin"))

FINISHED = "Hey Jarvis, what time is it"          # a whole question
DANGLING = "Hey Jarvis, what's the weather in"    # cut off mid-sentence


def phrase(text, voice="am_michael"):
    s, sr = k.create(text, voice=voice, speed=0.95)
    s = np.asarray(s, dtype=np.float32)
    idx = np.linspace(0, len(s) - 1, int(len(s) * 16000 / sr)).astype(int)
    return np.concatenate([np.zeros(3200, np.float32), s[idx] * 0.8])


async def run_one(c, events, text) -> dict:
    """Inject the phrase and return the transcript event JARVIS emitted."""
    n0 = len(events)
    await c.post(f"{BASE}/debug/inject_audio", headers=H,
                 json={"audio_b64": base64.b64encode(phrase(text).tobytes()).decode()})
    got: dict = {}
    deadline = time.time() + 25
    while time.time() < deadline and not got:
        for e in events[n0:]:
            if e.get("kind") == "transcript" and e.get("role") == "user":
                got = e
                break
        await asyncio.sleep(0.05)
    t0 = time.time()                       # let the turn finish before the next
    while time.time() - t0 < 60:
        if time.time() - t0 > 2 and \
                (await c.get(f"{BASE}/health", headers=H)).json()["state"] == "idle":
            break
        await asyncio.sleep(0.4)
    return got


async def main() -> int:
    events: list = []

    async def listen():
        async with websockets.connect(f"ws://127.0.0.1:{PORT}/ws?token={TOKEN}",
                                      max_size=None) as ws:
            async for m in ws:
                events.append(json.loads(m))

    lt = asyncio.create_task(listen())
    await asyncio.sleep(1)
    fails = []
    async with httpx.AsyncClient(timeout=120) as c:
        fin = await run_one(c, events, FINISHED)
        await asyncio.sleep(2)
        dang = await run_one(c, events, DANGLING)
    lt.cancel()

    for label, phr, ev in (("finished", FINISHED, fin), ("dangling", DANGLING, dang)):
        print(f"  {label:9} {phr!r}")
        print(f"            heard {ev.get('text')!r} - waited "
              f"{ev.get('silence_ms')} ms of silence (budget {ev.get('budget_ms')} ms), "
              f"transcribe {ev.get('stt_ms')} ms")

    def check(name, cond, detail=""):
        print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
        if not cond:
            fails.append(name)

    check("both utterances were heard", bool(fin.get("text")) and bool(dang.get("text")))
    fb, db = fin.get("budget_ms"), dang.get("budget_ms")
    check("a finished sentence gets the short budget", fb == 400, f"{fb} ms")
    check("a dangling one gets the patient budget", db == 1900, f"{db} ms")
    fs, ds = fin.get("silence_ms") or 0, dang.get("silence_ms") or 0
    check("he really did cut the finished one early", 0 < fs < 900, f"{fs} ms")
    check("and really did hold on for the dangling one", ds > 1900, f"{ds} ms")
    # The endpoint check transcribed this audio already; doing it twice is
    # ~1.5 s of dead air on every single turn.
    ms = fin.get("stt_ms")
    check("the transcript is reused, not recomputed",
          ms is not None and ms < 100, f"{ms} ms")

    print("\nENDPOINT E2E: " + ("PASS" if not fails else f"FAIL ({len(fails)})"))
    return 0 if not fails else 1


sys.exit(asyncio.run(main()))
