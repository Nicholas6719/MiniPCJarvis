"""Sleep mode against the running app, both directions.

The direction that matters is coming BACK. Sleep mode shipped once as a one-way door:
the wake-word loop skipped every audio block unless the state was IDLE, so once he was
asleep nothing could wake him and every later turn failed with "busy". Entering sleep is
the easy half; this test exists for the other half.

Run: python tests/sleep_e2e.py PORT TOKEN
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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

port, tok = sys.argv[1], sys.argv[2]
H = {"X-Jarvis-Token": tok}
BASE = f"http://127.0.0.1:{port}"

fails = []


def check(name, cond):
    print(("  PASS  " if cond else "  FAIL  ") + name)
    if not cond:
        fails.append(name)


def state():
    return httpx.get(BASE + "/health", headers=H, timeout=10).json()["state"]


def minimized() -> bool | None:
    """True if every JARVIS window is minimised. None if we can't tell (no window)."""
    try:
        import win32gui
        from tools.windows_tools import _our_windows
        wins = _our_windows()
        if not wins:
            return None
        return all(win32gui.IsIconic(h) for h in wins)
    except Exception:
        return None


def phrase(text):
    from kokoro_onnx import Kokoro
    d = os.path.expandvars(r"%APPDATA%\JARVIS\voices\kokoro")
    k = Kokoro(os.path.join(d, "kokoro-v1.0.onnx"), os.path.join(d, "voices-v1.0.bin"))
    s, sr = k.create(text, voice="am_michael", speed=0.95)
    s = np.asarray(s, dtype=np.float32)
    idx = np.linspace(0, len(s) - 1, int(len(s) * 16000 / sr)).astype(int)
    return np.concatenate([np.zeros(3200, np.float32), s[idx] * 0.8])


async def wait_state(want, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if state() == want:
            return True
        await asyncio.sleep(0.4)
    return False


async def main() -> int:
    events = []

    async def listen():
        async with websockets.connect(f"ws://127.0.0.1:{port}/ws?token={tok}") as ws:
            async for m in ws:
                events.append(json.loads(m))

    lt = asyncio.create_task(listen())
    await asyncio.sleep(1)

    # --- into sleep, by voice command text ---
    # state before AND the reply to the post: when this fails in a full suite run
    # (and passes alone) the question is always "did the request even land?"
    was = httpx.get(BASE + "/health", headers=H, timeout=10).json().get("state")
    r = httpx.post(BASE + "/text", headers=H, json={"text": "that's all for now"}, timeout=15)
    ok = await wait_state("sleeping", 60)
    now = httpx.get(BASE + "/health", headers=H, timeout=10).json().get("state")
    check("'that's all for now' puts him to sleep", ok,
          f"was {was!r}, post {r.status_code} {r.text[:80]}, ended {now!r}")
    reflex = [e.get("skill") for e in events if e.get("kind") == "reflex"]
    check("it went through the sleep reflex, not the LLM", "sleep" in reflex)
    await asyncio.sleep(1.5)
    check("his window is minimised", minimized() in (True, None))

    # --- the half that was broken: getting back ---
    n = len(events)
    audio = phrase("Hey Jarvis, what time is it?")
    httpx.post(BASE + "/debug/inject_audio", headers=H,
               json={"audio_b64": base64.b64encode(audio.tobytes()).decode()}, timeout=30)
    woke = False
    t0 = time.time()
    while time.time() - t0 < 60:
        if any(e.get("kind") == "wake" for e in events[n:]):
            woke = True
            break
        await asyncio.sleep(0.3)
    check("the wake word still fires while he is asleep", woke)
    check("he leaves the sleeping state", await wait_state("idle", 90) or state() != "sleeping")
    await asyncio.sleep(1.0)
    check("his window is restored", minimized() in (False, None))
    said = "".join(e.get("text", "") for e in events[n:] if e.get("kind") == "assistant_delta")
    check(f"and he answers the question ({said.strip()[:40]!r})", bool(said.strip()))

    # --- typing to him while asleep must work too, not answer "busy" ---
    httpx.post(BASE + "/text", headers=H, json={"text": "go to sleep"}, timeout=15)
    await wait_state("sleeping", 60)
    r = httpx.post(BASE + "/text", headers=H, json={"text": "what time is it"}, timeout=15).json()
    check("typing while asleep is accepted, not refused as busy", r.get("ok") is True)
    await wait_state("idle", 90)

    lt.cancel()
    print("\n" + ("SLEEP E2E: PASS" if not fails else f"SLEEP E2E: FAIL {fails}"))
    return 1 if fails else 0

sys.exit(asyncio.run(main()))
