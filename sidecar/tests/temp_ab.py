"""A/B sampling through the REAL turn pipeline, under realistic conversation conditions.

Methodology note, learned the hard way: asking the same question several times in a row
proves nothing. The first correct answer lands in the conversation history and the model
simply repeats it, so every setting scores 100%. The failure being chased ("how many bones
in the human body" -> "fifty-two vertebrae") only appeared for a question asked ONCE,
partway through a long run of unrelated questions — i.e. with a full, noisy history in
context. So: each pass asks every question exactly once, in a varied sequence, and the
whole sequence is repeated. Interleaved A/B/A/B on one warm session.

Run: python tests/temp_ab.py PORT TOKEN [PASSES]
"""
import asyncio
import json
import re
import sys
import time

import httpx
import websockets

port, tok = sys.argv[1], sys.argv[2]
PASSES = int(sys.argv[3]) if len(sys.argv) > 3 else 2
H = {"X-Jarvis-Token": tok}
BASE = f"http://127.0.0.1:{port}"

# Deliberately varied and interleaved with non-factual turns, so the history is as noisy
# as a real conversation's by the time the checked questions come round.
CASES = [
    ("what is the capital of france", r"paris"),
    ("how tall is mount everest", r"8,?848|29,0|8\.8|twenty.nine thousand"),
    ("what year did the berlin wall fall", r"1989|eighty.nine"),
    ("who directed the film jaws", r"spielberg"),
    ("what is a solid state drive", r"flash|memory|no moving"),
    ("should i use dark mode at night", None),
    ("tell me a fun fact about octopuses", None),
    ("what is the largest ocean", r"pacific"),
    ("why is the sky blue", r"scatter|rayleigh"),
    ("how many bones in the human body", r"206|two hundred"),
    ("name a good sci fi film", None),
    ("is coffee bad for you", None),
    ("what is a good bedtime", None),
    ("what does cpu stand for", r"central processing"),
    ("how far is the moon", r"384|385|238,?9|quarter million"),
    ("should i defragment an ssd", r"\bno\b|unnecessary|don'?t|not needed|never"),
    ("what is a vpn", r"virtual private|encrypt"),
    ("how many minutes are in a day", r"1,?440|fourteen hundred|one thousand four hundred"),
    ("how many players are on a soccer team on the field", r"\b11\b|eleven"),
    ("what is the freezing point of water in fahrenheit", r"\b32\b|thirty.two"),
]


async def one(ws, text):
    httpx.post(BASE + "/text", headers=H, json={"text": text}, timeout=15)
    r = ""
    t0 = time.time()
    while time.time() - t0 < 150:
        try:
            e = json.loads(await asyncio.wait_for(ws.recv(), timeout=150))
        except asyncio.TimeoutError:
            break
        if e.get("kind") == "assistant_delta":
            r += e["text"]
        elif e.get("kind") == "turn_done":
            break
    return r.strip()


async def sweep(ws, temp):
    httpx.patch(BASE + "/config", headers=H,
                json={"llm": {"sampling": {"temperature": temp}}}, timeout=15)
    good = tot = 0
    bad = []
    for _ in range(PASSES):
        for q, pat in CASES:
            r = await one(ws, q)
            if pat is None:
                continue                      # asked only to make the history realistic
            ok = bool(re.search(pat, r, re.I))
            good += ok
            tot += 1
            if not ok:
                bad.append(f"{q} -> {r[:64] or '<empty reply>'}")
            await asyncio.sleep(0.3)
    print(f"  temp {temp:<5} {good}/{tot} correct", flush=True)
    for b in bad:
        print(f"      WRONG  {b}", flush=True)
    return good, tot


async def main():
    totals: dict[float, tuple[int, int]] = {}
    async with websockets.connect(f"ws://127.0.0.1:{port}/ws?token={tok}", max_size=None) as ws:
        for t in (0.8, 0.15, 0.8, 0.15):
            g, n = await sweep(ws, t)
            a, b = totals.get(t, (0, 0))
            totals[t] = (a + g, b + n)
    print("\n== totals, one-shot questions in a noisy conversation")
    for t, (g, n) in sorted(totals.items()):
        print(f"   temp {t:<5} {g}/{n} = {100 * g / n:.0f}%")
    httpx.patch(BASE + "/config", headers=H,
                json={"llm": {"sampling": {"temperature": 0.15}}}, timeout=15)

asyncio.run(main())
