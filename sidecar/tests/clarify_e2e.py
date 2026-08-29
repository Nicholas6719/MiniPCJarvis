"""Speculative clarification, on the running app.

The unit gate proves he asks the right question. This proves the point of asking
it: that both readings are already being fetched while the question is spoken,
so the answer costs nothing when it comes. If the chosen branch had to fetch
after being chosen, the tool call would show up here — that it does not IS the
feature.

Run: python tests/clarify_e2e.py PORT TOKEN
"""
import asyncio
import json
import sys
import time

import httpx
import websockets

PORT = sys.argv[1] if len(sys.argv) > 1 else "8790"
TOKEN = sys.argv[2] if len(sys.argv) > 2 else "devtoken123"
BASE, H = f"http://127.0.0.1:{PORT}", {"X-Jarvis-Token": TOKEN}

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


async def turn(c, ev, text) -> dict:
    """Say one thing; return what he did about it."""
    n0 = len(ev)
    t0 = time.time()
    await c.post(f"{BASE}/text", headers=H, json={"text": text})
    got = {"reply": "", "tools": [], "first": None, "asked": False}
    done = False
    while time.time() - t0 < 90 and not done:
        for e in ev[n0:]:
            k = e.get("kind")
            if k == "tool_call" and e.get("status") == "pending":
                got["tools"].append(e.get("tool"))
            elif k == "clarify":
                got["asked"] = True
            elif k == "assistant_delta":
                got["first"] = got["first"] if got["first"] is not None else time.time() - t0
                got["reply"] += e.get("text", "")
            elif k == "turn_done":
                done = True
        n0 = len(ev)
        await asyncio.sleep(0.05)
    t = time.time()
    while time.time() - t < 60:
        if time.time() - t > 1 and \
                (await c.get(f"{BASE}/health", headers=H)).json()["state"] == "idle":
            break
        await asyncio.sleep(0.4)
    return got


async def main() -> int:
    ev: list = []

    async def listen():
        async with websockets.connect(f"ws://127.0.0.1:{PORT}/ws?token={TOKEN}",
                                      max_size=None) as ws:
            async for m in ws:
                ev.append(json.loads(m))

    lt = asyncio.create_task(listen())
    await asyncio.sleep(1)
    async with httpx.AsyncClient(timeout=180) as c:
        # --- the vague one: he asks, and starts both answers -------------------
        ask = await turn(c, ev, "any news on tesla")
        check("he asks instead of guessing", ask["asked"], ask["reply"][:80])
        check("the question is one short line",
              "company" in ask["reply"].lower() and "stock" in ask["reply"].lower(),
              ask["reply"])
        check("asking is instant — nothing is fetched to ask it",
              ask["first"] is not None and ask["first"] < 1.0, f"{ask['first']}s")
        check("BOTH readings are already being fetched",
              set(ask["tools"]) == {"get_news", "get_stock_quote"}, ask["tools"])

        # --- the answer: warm, so no second fetch -----------------------------
        got = await turn(c, ev, "the stock")
        check("the answer needs no tool call at all — it was already fetched",
              got["tools"] == [], got["tools"])
        check("and it is the STOCK he gets", "dollars" in got["reply"].lower(),
              got["reply"][:120])
        check("it starts speaking straight away",
              got["first"] is not None and got["first"] < 1.0, f"{got['first']}s")

        # --- a question he does not answer is a new request, not an answer -----
        await turn(c, ev, "anything on apple")
        other = await turn(c, ev, "what time is it")
        check("changing the subject drops the question, and is answered normally",
              not other["asked"] and ("am" in other["reply"].lower()
                                      or "pm" in other["reply"].lower()),
              other["reply"][:80])

        # --- and one that says which half it wants is never interrupted --------
        direct = await turn(c, ev, "what's tesla trading at")
        check("a question that is already specific is answered, not queried",
              not direct["asked"] and "dollars" in direct["reply"].lower(),
              direct["reply"][:120])
    lt.cancel()
    print(f"\nCLARIFY E2E: {'PASS' if not fails else f'FAIL ({len(fails)})'}")
    return 0 if not fails else 1


sys.exit(asyncio.run(main()))
