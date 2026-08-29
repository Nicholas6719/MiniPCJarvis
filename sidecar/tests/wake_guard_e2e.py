"""A television must not be able to talk to him — on the real app.

After a turn there is a short window in which plain speech is enough, so you
don't have to say his name twice in one conversation. Film dialogue walked
through that window and he ran a web search on what he heard.

So while another application is making noise the window closes. What must NOT
happen is that this costs him anything else: his name must still work, and in a
quiet room the shortcut must still work. All three are checked here.

Uses /debug/audio_playing to say "something is playing" rather than actually
filling the room with sound — the detector itself is tested against real audio
in tests/test_output_watch.py and by hand. This tests the WIRING: that the
microphone loop consults it at all.

Requires JARVIS_DEBUG=1. Run: python tests/wake_guard_e2e.py PORT TOKEN
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

PORT = sys.argv[1] if len(sys.argv) > 1 else "8790"
TOKEN = sys.argv[2] if len(sys.argv) > 2 else "devtoken123"
BASE, H = f"http://127.0.0.1:{PORT}", {"X-Jarvis-Token": TOKEN}

d = os.path.expandvars(r"%APPDATA%\JARVIS\voices\kokoro")
k = Kokoro(os.path.join(d, "kokoro-v1.0.onnx"), os.path.join(d, "voices-v1.0.bin"))

WAKE = "Hey Jarvis, what time is it"
PLAIN = "And what day of the week is it"

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


def phrase(text):
    s, sr = k.create(text, voice="am_michael", speed=0.95)
    s = np.asarray(s, dtype=np.float32)
    idx = np.linspace(0, len(s) - 1, int(len(s) * 16000 / sr)).astype(int)
    return np.concatenate([np.zeros(3200, np.float32), s[idx] * 0.8])


async def idle(c, secs=60):
    t0 = time.time()
    while time.time() - t0 < secs:
        if time.time() - t0 > 1 and \
                (await c.get(f"{BASE}/health", headers=H)).json()["state"] == "idle":
            return True
        await asyncio.sleep(0.4)
    return False


async def say(c, ev, text, wait=14.0) -> str:
    """Speak into the microphone; return what he heard, or "" if he ignored it."""
    n0 = len(ev)
    await c.post(f"{BASE}/debug/inject_audio", headers=H,
                 json={"audio_b64": base64.b64encode(phrase(text).tobytes()).decode()})
    deadline = time.time() + wait
    heard = ""
    while time.time() < deadline and not heard:
        for e in ev[n0:]:
            if e.get("kind") == "transcript" and e.get("role") == "user":
                heard = e.get("text", "")
        await asyncio.sleep(0.05)
    if heard:
        await idle(c)
    return heard


async def main() -> int:
    ev: list = []

    async def listen():
        async with websockets.connect(f"ws://127.0.0.1:{PORT}/ws?token={TOKEN}",
                                      max_size=None) as ws:
            async for m in ws:
                ev.append(json.loads(m))

    lt = asyncio.create_task(listen())
    await asyncio.sleep(1)
    async with httpx.AsyncClient(timeout=120) as c:
        async def pretend(on: bool):
            await c.post(f"{BASE}/debug/audio_playing", headers=H, json={"on": on})

        try:
            # --- a quiet room: the shortcut works, which is the whole point ----
            await pretend(False)
            await idle(c)
            check("his name opens a turn", bool(await say(c, ev, WAKE)))
            plain = await say(c, ev, PLAIN)
            check("and in a quiet room a follow-up needs no name at all",
                  bool(plain), "the conversation window did not take it")

            # --- something else is making noise -------------------------------
            await pretend(True)
            check("his name still works while something is playing",
                  bool(await say(c, ev, WAKE)))
            n0 = len(ev)
            ignored = await say(c, ev, PLAIN, wait=12.0)
            check("a follow-up with no name is IGNORED while something plays",
                  ignored == "", f"he answered {ignored!r}")
            # and it was ignored FOR THAT REASON, not because the window happened
            # to expire — an absent log line proves nothing
            check("...and he says why he ignored it",
                  any(e.get("kind") == "wake_suppressed" and e.get("reason") == "audio"
                      for e in ev[n0:]), "no wake_suppressed event")

            # --- and the room goes quiet again ---------------------------------
            await pretend(False)
            check("his name still works afterwards", bool(await say(c, ev, WAKE)))
            back = await say(c, ev, PLAIN)
            check("the shortcut comes back when the room does", bool(back),
                  "the window stayed shut after the noise stopped")
        finally:
            await pretend(False)
    lt.cancel()
    print(f"\nWAKE GUARD E2E: {'PASS' if not fails else f'FAIL ({len(fails)})'}")
    return 0 if not fails else 1


sys.exit(asyncio.run(main()))
