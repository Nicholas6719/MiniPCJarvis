"""Persona + reflex-speed end-to-end against the running app.

Sends a run of reflex turns, collects what JARVIS actually said, and reports how
often "sir" appeared and where it sat. Also reads /metrics for first-audio latency,
which the TTS phrase cache is meant to have cut.
Run: python tests/persona_e2e.py PORT TOKEN
"""
import asyncio, json, re, sys, time
import httpx, websockets

port, tok = sys.argv[1], sys.argv[2]
H = {"X-Jarvis-Token": tok}
BASE = f"http://127.0.0.1:{port}"

TURNS = [
    "what time is it", "mute the speakers", "turn the audio back on",
    "set the volume to 40 percent", "what's the date today",
    "how's the computer doing", "what windows do i have open",
    "volume 55 please", "mute the speakers", "turn the audio back on",
    "what time is it now", "what's today's date",
]


async def one(ws, text):
    t0 = time.time()
    for _ in range(60):
        r = httpx.post(BASE + "/text", headers=H, json={"text": text}, timeout=10).json()
        if r.get("ok"):
            break
        await asyncio.sleep(1)
    reply = ""; first = None
    while time.time() - t0 < 90:
        try:
            e = json.loads(await asyncio.wait_for(ws.recv(), timeout=90))
        except asyncio.TimeoutError:
            break
        if e.get("kind") == "assistant_delta":
            if first is None:
                first = time.time() - t0
            reply += e["text"]
        elif e.get("kind") == "turn_done":
            break
    return reply.strip(), first


async def main():
    said = []
    async with websockets.connect(f"ws://127.0.0.1:{port}/ws?token={tok}", max_size=None) as ws:
        for text in TURNS:
            reply, first = await one(ws, text)
            said.append(reply)
            print(f"  {text[:30]:30} -> {reply[:60]:60} ({first and round(first, 2)}s)")
            await asyncio.sleep(1.2)

    hits = [s for s in said if re.search(r"\bsir\b", s, re.I)]
    print(f"\nhonorific: {len(hits)}/{len(said)} = {100 * len(hits) / len(said):.0f}%  (films: 37%)")
    bad_place = [s for s in hits if not (re.search(r",\s+sir[.?!]?$", s, re.I) or re.match(r"^Sir,", s))]
    print(f"misplaced: {bad_place if bad_place else 'none'}")
    doubled = [s for s in said if len(re.findall(r"\bsir\b", s, re.I)) > 1]
    print(f"doubled  : {doubled if doubled else 'none'}")
    runs = [i for i in range(len(said) - 1)
            if re.search(r"\bsir\b", said[i], re.I) and re.search(r"\bsir\b", said[i + 1], re.I)]
    print(f"back-to-back: {'none' if not runs else runs}")

    m = httpx.get(BASE + "/metrics", headers=H).json()
    print("\nmetrics:", json.dumps(m["summary"]))

    ok = (0.15 <= len(hits) / len(said) <= 0.60) and not bad_place and not doubled and not runs
    print("\n" + ("PERSONA E2E: PASS" if ok else "PERSONA E2E: FAIL"))
    return 0 if ok else 1

sys.exit(asyncio.run(main()))
