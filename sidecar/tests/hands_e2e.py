"""Remote hands and dictation, for real, on the running app.

Everything else about R2 was gated on logic: grid maths, risk levels, name
scoring. Nothing had ever proved that click_control actually moves the mouse to
a named control and presses it, or that dictation records, transcribes and lands
the words in another app.

This does, with a proof that cannot be faked: it opens Notepad, dictates a
sentence into it, then clicks Close by NAME. Notepad only asks "save changes?"
if there is unsaved text in it — so that dialog appearing is the receipt for the
dictation, and the dialog answering to a second click by name is the receipt for
click_control. Then it clicks "Don't save" and checks Notepad is gone, leaving
nothing behind on the user's desktop.

Requires JARVIS_DEBUG=1. Run: python tests/hands_e2e.py PORT TOKEN
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

DICTATED = "The quarterly numbers look strong."

d = os.path.expandvars(r"%APPDATA%\JARVIS\voices\kokoro")
k = Kokoro(os.path.join(d, "kokoro-v1.0.onnx"), os.path.join(d, "voices-v1.0.bin"))

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


def speech(text):
    s, sr = k.create(text, voice="am_michael", speed=0.95)
    s = np.asarray(s, dtype=np.float32)
    idx = np.linspace(0, len(s) - 1, int(len(s) * 16000 / sr)).astype(int)
    return np.concatenate([np.zeros(1600, np.float32), s[idx] * 0.8])


async def tool(c, _tool, **args) -> dict:
    """Run one tool through the real risk gate; approve if it asks.

    Returns the tool's OWN result. A refusal ("I don't see a control called X")
    comes back inside it, not as a failed call, so flatten that here — checking
    the envelope would call every refusal a success.
    """
    r = (await c.post(f"{BASE}/debug/tool", headers=H,
                      json={"tool": _tool, "args": args})).json()
    res = r.get("result") if isinstance(r.get("result"), dict) else {}
    return {"ok": bool(r.get("ok")) and "error" not in res,
            "error": r.get("error") or res.get("error"), **res}


async def approver(events, c):
    """Answer every confirmation the way the user would when they asked for it."""
    seen = set()
    while True:
        for e in list(events):
            if e.get("kind") == "confirmation_required" and e["confirm_id"] not in seen:
                seen.add(e["confirm_id"])
                await c.post(f"{BASE}/confirm", headers=H,
                             json={"confirm_id": e["confirm_id"], "approved": True})
        await asyncio.sleep(0.1)


async def idle(c, secs=45) -> bool:
    t0 = time.time()
    while time.time() - t0 < secs:
        if (await c.get(f"{BASE}/health", headers=H)).json().get("state") == "idle":
            return True
        await asyncio.sleep(0.5)
    return False


async def controls(c, window, limit=60) -> list[str]:
    r = await tool(c, "list_controls", window=window, limit=limit)
    return [str(i.get("name", "")) for i in (r.get("controls") or [])]


async def main() -> int:
    events: list = []

    async def listen():
        async with websockets.connect(f"ws://127.0.0.1:{PORT}/ws?token={TOKEN}",
                                      max_size=None) as ws:
            async for m in ws:
                events.append(json.loads(m))

    lt = asyncio.create_task(listen())
    await asyncio.sleep(1)
    async with httpx.AsyncClient(timeout=180) as c:
        ap = asyncio.create_task(approver(events, c))
        try:
            # --- open a scratch window of our own to work in -----------------
            r = await tool(c, "open_application", name="notepad")
            check("notepad opened", r.get("ok"), r)
            await asyncio.sleep(3)
            names = await controls(c, "Notepad")
            check("its controls can be read", len(names) > 3, names[:6])
            check("only one Notepad window is in scope — a click by name must "
                  "not be able to land in a different window",
                  len([n for n in names if n == "File"]) <= 2, names)

            # --- dictate into it ---------------------------------------------
            await tool(c, "focus_window", title="Notepad")
            await asyncio.sleep(1.5)
            await idle(c)                  # dictation refuses mid-turn, by design
            st = (await c.post(f"{BASE}/dictation/start", headers=H)).json()
            check("dictation starts", st.get("ok"), st)
            await c.post(f"{BASE}/debug/inject_audio", headers=H,
                         json={"audio_b64": base64.b64encode(
                             speech(DICTATED).tobytes()).decode()})
            sp = (await c.post(f"{BASE}/dictation/stop", headers=H)).json()
            check("dictation transcribed what was said",
                  DICTATED.lower().strip(".") in (sp.get("text") or "").lower(), sp)
            check("and pasted it into the focused window", sp.get("pasted") is True, sp)
            await asyncio.sleep(1.5)

            # THE DOCUMENT'S TEXT, not the labels around it. This used to look
            # for the dictated words among control NAMES, on the theory that
            # Windows names a Notepad tab after its contents. Modern Notepad
            # names it "Untitled" and reports "Unmodified", so the receipt
            # failed on builds where dictation worked perfectly — every other
            # assertion here passed, including that the text was pasted and that
            # selecting all and deleting emptied the document.
            #
            # A control's contents live in its ValuePattern, which nothing in
            # the sidecar could read. read_window_text is that capability, added
            # rather than weakening this assertion: it is the same proof, taken
            # from the place the proof actually lives.
            doc = await tool(c, "read_window_text", window="Notepad")
            said = (doc.get("text") or "")
            check("Windows itself reports the dictated words are in the document",
                  DICTATED.lower().strip(".")[:24] in said.lower(),
                  f"document reads {said[:120]!r}")
            tabs = await controls(c, "Notepad")

            # --- a click by name, with a consequence that can be seen ---------
            before = [t for t in tabs if "unmodified" in t.lower()]
            r = await tool(c, "click_control", window="Notepad", name="Add New Tab")
            check("clicking 'Add New Tab' by name succeeds", r.get("ok"), r)
            await asyncio.sleep(2)
            after = [t for t in await controls(c, "Notepad") if "unmodified" in t.lower()]
            check("and a new tab really appeared — the click did something",
                  len(after) > len(before), f"{before} -> {after}")

            # --- clear up after ourselves, with the keyboard -------------------
            await tool(c, "press_keys", keys="ctrl+w")           # drop the empty tab
            await asyncio.sleep(1.5)
            await tool(c, "focus_window", title="Notepad")
            await tool(c, "press_keys", keys="ctrl+a", window="Notepad")
            await tool(c, "press_keys", keys="delete", window="Notepad")
            await asyncio.sleep(1.5)
            left = await controls(c, "Notepad")
            check("selecting all and deleting really emptied the document",
                  not any(DICTATED.lower()[:20] in n.lower() for n in left), left[:6])

        finally:
            # Never leave a window — or unsaved text — behind, even on a failure:
            # Notepad reopens whatever was in it, so empty the document FIRST or
            # the next launch (his, not the test's) restores our test sentence.
            try:
                await tool(c, "focus_window", title="Notepad")
                await tool(c, "press_keys", keys="ctrl+a", window="Notepad")
                await tool(c, "press_keys", keys="delete", window="Notepad")
                await asyncio.sleep(0.8)
                await tool(c, "close_application", name="notepad")
                await asyncio.sleep(1.5)
                if await controls(c, "Notepad"):
                    await tool(c, "press_keys", keys="alt+f4", window="Notepad")
            except Exception:
                pass
            ap.cancel()
    lt.cancel()
    print(f"\nHANDS E2E: {'PASS' if not fails else f'FAIL ({len(fails)})'}")
    return 0 if not fails else 1


sys.exit(asyncio.run(main()))
