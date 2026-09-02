"""The remote path, end to end, against the real bot.

Only the INBOUND half is simulated — there is no way for the bot to receive a
message from him without him sending one — but everything the bridge sends back
is a real message to the real chat. That is also why this is opt-in: it puts
notifications on his phone, and a test suite has no business doing that
unasked. Set JARVIS_TELEGRAM_E2E=1 to run it; without it, it skips loudly.

What it proves:
  * a question asked from the phone is asked ON the phone, as buttons
  * tapping an option answers it, from the branch already fetched
  * a question asked remotely does NOT open the microphone at the PC, where
    anything said near it would be read as his reply to a question he asked
    from somewhere else

Run: JARVIS_TELEGRAM_E2E=1 python tests/telegram_e2e.py PORT TOKEN
"""
import asyncio
import json
import os
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


async def inbound(c, ev, settle=3.0, **body) -> dict:
    """Hand the bridge an update as if it came from his phone; watch what happens."""
    n0 = len(ev)
    t0 = time.time()
    r = await c.post(f"{BASE}/debug/telegram", headers=H, json=body)
    if r.status_code != 200:
        return {"error": f"{r.status_code} {r.text[:120]}"}
    got = {"tools": [], "asked": None, "reply": "", "armed": False, "done": None}
    while time.time() - t0 < 90 and got["done"] is None:
        for e in ev[n0:]:
            k = e.get("kind")
            if k == "tool_call" and e.get("status") == "pending":
                got["tools"].append(e.get("tool"))
            elif k == "clarify":
                got["asked"] = e.get("question")
            elif k == "conversation" and e.get("armed"):
                got["armed"] = True
            elif k == "turn_done":
                got["reply"] = e.get("text") or ""
                got["done"] = time.time() - t0
        n0 = len(ev)
        await asyncio.sleep(0.05)
    # the branches are spawned around the same moment the turn ends: give their
    # tool_call events a beat to arrive, or this reads as "nothing was fetched"
    end = time.time() + settle
    while time.time() < end:
        for e in ev[n0:]:
            if e.get("kind") == "tool_call" and e.get("status") == "pending":
                got["tools"].append(e.get("tool"))
            elif e.get("kind") == "conversation" and e.get("armed"):
                got["armed"] = True
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
    if os.environ.get("JARVIS_TELEGRAM_E2E") != "1":
        print("  SKIPPED - this sends real messages to his phone.\n"
              "  Set JARVIS_TELEGRAM_E2E=1 to run it.\n\nTELEGRAM E2E: SKIPPED")
        return 0

    # NEVER AT NIGHT, whatever the env var says.
    #
    # On 2026-09-01 a scheduled task I created and failed to delete fired at
    # 23:59 and ran the whole suite. This file duly sent a stock quote, a button
    # prompt, a test voice clip and a weather answer to his phone at 12:09 AM.
    # His words: "I know for a fact we weren't running any tests at midnight."
    #
    # The orphaned task was the fault and it is gone. This is the guard that
    # makes the class of mistake harmless: an opt-in flag can be set by a script,
    # inherited by a scheduler, or left behind in an environment — none of which
    # is consent to wake him. The clock is not so easily fooled. Anything that
    # can put a notification on his phone refuses to do it inside the hours he
    # set aside for sleeping, exactly as delivery.py already refuses.
    try:
        import sys as _s
        _s.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from proactive import proactive
        if proactive.in_quiet_hours():
            print("  SKIPPED - it is inside his quiet hours, and this test messages\n"
                  "  his phone. Run it during the day.\n\nTELEGRAM E2E: SKIPPED")
            return 0
    except Exception as e:
        # Cannot tell what time it is for him? Then do not risk it.
        print(f"  SKIPPED - could not confirm this is a reasonable hour ({e}).\n"
              "\nTELEGRAM E2E: SKIPPED")
        return 0
    ev: list = []

    async def listen():
        async with websockets.connect(f"ws://127.0.0.1:{PORT}/ws?token={TOKEN}",
                                      max_size=None) as ws:
            async for m in ws:
                ev.append(json.loads(m))

    lt = asyncio.create_task(listen())
    await asyncio.sleep(1)
    async with httpx.AsyncClient(timeout=180) as c:
        # --- an ordinary question, from the phone -----------------------------
        plain = await inbound(c, ev, text="what time is it")
        check("a message from the phone runs a turn", not plain.get("error"),
              plain.get("error"))
        check("and answers it", "am" in plain["reply"].lower()
              or "pm" in plain["reply"].lower(), plain["reply"][:80])

        # --- a vague one: asked as buttons, both readings fetching -------------
        ask = await inbound(c, ev, text="any news on tesla")
        check("a vague question is put back to him", bool(ask["asked"]), ask)
        check("both readings are fetched while it sits on his phone",
              set(ask["tools"]) == {"get_news", "get_stock_quote"}, ask["tools"])
        check("asking from the phone does NOT open the microphone here",
              not ask["armed"], "the conversation window was armed")

        # --- tapping an option ------------------------------------------------
        tap = await inbound(c, ev, callback="clarify:the stock")
        check("tapping an option answers the question", "dollars" in tap["reply"].lower(),
              tap["reply"][:120])
        check("and it needed no second fetch", tap["tools"] == [], tap["tools"])
        check("the answer comes back promptly",
              tap["done"] is not None and tap["done"] < 8, f"{tap['done']}s")

        # --- and the markets work from the phone too --------------------------
        mkt = await inbound(c, ev, text="how are the markets doing")
        check("markets answer from the phone",
              "percent" in mkt["reply"].lower(), mkt["reply"][:120])

        # --- a VOICE NOTE, the whole way round --------------------------------
        # The bot sends the clip to the chat to get a real file_id, then it is
        # handed back as though he had recorded it. Everything after that is the
        # real path: Telegram's own download, the decoder, the recogniser.
        fixture = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "fixtures", "voice_note.oga")
        sent = await c.post(f"{BASE}/debug/telegram_send_voice", headers=H,
                            json={"path": fixture})
        fid = sent.json().get("file_id") if sent.status_code == 200 else None
        check("a voice note can be put into the chat to test with", bool(fid),
              sent.text[:160])
        if fid:
            v = await inbound(c, ev, voice_file_id=fid)
            check("a voice note is transcribed and acted on",
                  "boston" in v["reply"].lower() or "weather" in v["reply"].lower(),
                  v["reply"][:160])
    lt.cancel()
    print(f"\nTELEGRAM E2E: {'PASS' if not fails else f'FAIL ({len(fails)})'}")
    return 0 if not fails else 1


sys.exit(asyncio.run(main()))
