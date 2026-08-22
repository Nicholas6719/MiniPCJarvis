"""LLM-path timing: general questions should be answered directly (brain says
'general' -> first round runs with tool_choice=none). Run: python tests/general_e2e.py PORT TOKEN"""
import asyncio, json, sys, time
import httpx, websockets

port, tok = sys.argv[1], sys.argv[2]
H = {"X-Jarvis-Token": tok}
BASE = f"http://127.0.0.1:{port}"

CASES = [
    "tell me a one sentence fun fact about octopuses",
    "what's the capital of australia",
    "give me one tip for sleeping better",
    "what's the weather in boston right now",   # must search
    "how many legs does a spider have",
    "open example.com and tell me what the page says",   # must stay inside JARVIS
]


async def one(ws, text):
    t0 = time.time()
    httpx.post(BASE + "/text", headers=H, json={"text": text}, timeout=10)
    mode = None; first = None; reply = ""; tools = []
    while time.time() - t0 < 120:
        e = json.loads(await asyncio.wait_for(ws.recv(), timeout=120)); k = e.get("kind")
        if k == "reflex":
            mode = e.get("mode")
        elif k == "tool_call" and e.get("status") == "pending":
            tools.append(e["tool"])
        elif k == "assistant_delta":
            first = first or time.time() - t0; reply += e["text"]
        elif k == "turn_done":
            break
    print(f"  {text[:42]:42} mode={str(mode):16} tools={tools} first={round(first, 2) if first else None}s "
          f"total={round(time.time() - t0, 1)}s | {reply.strip()[:60]}")


def visible_browser_windows() -> list[str]:
    """Any on-screen Brave/Edge/Chrome window = JARVIS escaped the app. Must be empty
    (windows the user already had open before the test are reported too, so read it)."""
    import win32gui
    out = []
    def cb(h, _):
        t = win32gui.GetWindowText(h)
        if win32gui.IsWindowVisible(h) and t.endswith((" - Brave", "Microsoft Edge", " - Google Chrome")):
            l, tp, r, b = win32gui.GetWindowRect(h)
            if r > 0 and b > 0:
                out.append(t[:60])
        return True
    win32gui.EnumWindows(cb, None)
    return out


async def main():
    before = visible_browser_windows()
    async with websockets.connect(f"ws://127.0.0.1:{port}/ws?token={tok}", max_size=None) as ws:
        for t in CASES:
            await one(ws, t)
            await asyncio.sleep(1.5)
    after = [w for w in visible_browser_windows() if w not in before]
    print("NEW visible browser windows:", after or "none (good)")

asyncio.run(main())
