"""Checklist part 2: barge-in interrupt, in-app navigation proof, close-through-the-app,
external-window audit, RAM after vision reap.  Run: python tests/checklist2.py PORT TOKEN"""
import asyncio, base64, json, os, sys, time
import numpy as np
import httpx, psutil, websockets
sys.path.insert(0, ".")

port, tok = sys.argv[1], sys.argv[2]
H = {"X-Jarvis-Token": tok}
BASE = f"http://127.0.0.1:{port}"
results = []


def rec(item, ok, detail=""):
    results.append((item, bool(ok), str(detail)[:160]))
    print(f"  {'PASS' if ok else 'FAIL'}  {item[:52]:52} {str(detail)[:90]}")


def api(path, method="GET", **kw):
    r = httpx.request(method, BASE + path, headers=H, timeout=kw.pop("timeout", 90), **kw)
    r.raise_for_status()
    return r.json()


def external_browser_windows():
    """Visible, on-screen Brave/Edge/Chrome windows = JARVIS escaped the app."""
    import win32gui
    out = []

    def cb(h, _):
        t = win32gui.GetWindowText(h)
        if win32gui.IsWindowVisible(h) and t.endswith((" - Brave", "Microsoft Edge", " - Google Chrome")):
            l, tp, r, b = win32gui.GetWindowRect(h)
            if r > 0 and b > 0 and l > -5000:
                out.append(t[:60])
        return True
    win32gui.EnumWindows(cb, None)
    return out


def say(text, voice="am_michael"):
    from kokoro_onnx import Kokoro
    d = os.path.expandvars(r"%APPDATA%\JARVIS\voices\kokoro")
    k = Kokoro(os.path.join(d, "kokoro-v1.0.onnx"), os.path.join(d, "voices-v1.0.bin"))
    s, sr = k.create(text, voice=voice)
    idx = np.linspace(0, len(s) - 1, int(len(s) * 16000 / sr)).astype(int)
    return s[idx].astype(np.float32)


async def wait_idle(limit: float = 90) -> bool:
    t0 = time.time()
    while time.time() - t0 < limit:
        try:
            if httpx.get(BASE + "/health", timeout=5).json().get("state") == "idle":
                return True
        except Exception:
            pass
        await asyncio.sleep(1)
    return False


async def main():
    before_windows = external_browser_windows()

    async with websockets.connect(f"ws://127.0.0.1:{port}/ws?token={tok}", max_size=None) as ws:
        # ---------- in-app navigation proof ----------
        httpx.post(BASE + "/text", headers=H, json={"text": "open youtube"}, timeout=15)
        nav, t0 = None, time.time()
        while time.time() - t0 < 90:
            e = json.loads(await asyncio.wait_for(ws.recv(), timeout=90))
            if e.get("kind") == "browser":
                nav = (e.get("url"), e.get("title"), bool(e.get("shot")))
            if e.get("kind") == "turn_done":
                break
        await asyncio.sleep(4)
        from tools.windows_tools import _visible_windows as _vw
        user_br = [t for _, t in _vw() if t.endswith(" - Brave")]
        rec("'open youtube' opens in the USER's browser", any("youtube" in t.lower() for t in user_br), user_br)
        rec("...and not in JARVIS's hidden browser", nav is None, nav)

        # ---------- barge-in interrupt (name-only) ----------
        httpx.post(BASE + "/text", headers=H, json={"text": "explain how a jet engine works in detail"}, timeout=15)
        t0, spoke_at, interrupted, deltas_after = time.time(), None, False, 0
        while time.time() - t0 < 120:
            e = json.loads(await asyncio.wait_for(ws.recv(), timeout=120))
            k = e.get("kind")
            if k == "state" and e.get("state") == "speaking" and spoke_at is None:
                spoke_at = time.time()
                await asyncio.sleep(1.2)
                httpx.post(BASE + "/debug/inject_audio", headers=H, timeout=30,
                           json={"audio_b64": base64.b64encode(say("Hey Jarvis").tobytes()).decode()})
            elif k == "interrupted":
                interrupted = True
            elif k == "assistant_delta" and interrupted:
                deltas_after += 1
            elif k == "turn_done":
                break
        rec("barge-in: saying 'Jarvis' stops him mid-answer", interrupted, f"interrupted={interrupted}")
        rec("no text keeps streaming after an interrupt", interrupted and deltas_after == 0, f"{deltas_after} deltas after")

        # ---------- close an app through the app (Store app, safe) ----------
        await wait_idle()
        r = httpx.post(BASE + "/text", headers=H, json={"text": "open calculator"}, timeout=15).json()
        rec("app accepts a new turn right after an interrupt", r.get("ok"), r)
        t0 = time.time()
        while time.time() - t0 < 60:
            if json.loads(await asyncio.wait_for(ws.recv(), timeout=60)).get("kind") == "turn_done":
                break
        await asyncio.sleep(6)
        running = [p.pid for p in psutil.process_iter(["name"]) if "calc" in (p.info["name"] or "").lower()]
        await wait_idle()
        httpx.post(BASE + "/text", headers=H, json={"text": "close calculator"}, timeout=15)
        reply, t0 = "", time.time()
        while time.time() - t0 < 60:
            e = json.loads(await asyncio.wait_for(ws.recv(), timeout=60))
            if e.get("kind") == "assistant_delta":
                reply += e["text"]
            if e.get("kind") == "turn_done":
                break
        await asyncio.sleep(1.5)
        after = [p.pid for p in psutil.process_iter(["name"]) if "calc" in (p.info["name"] or "").lower()]
        rec("close a Store app by voice (was broken)", bool(running) and not after, f"before={running} after={after} | {reply.strip()}")

    # ---------- audits ----------
    new_win = [w for w in external_browser_windows() if w not in before_windows]
    rec("only the user-requested site opened externally", all("youtube" in w.lower() for w in new_win), new_win or "none")

    print("  waiting 100 s for the vision server to reap...")
    time.sleep(100)
    vis = [p.pid for p in psutil.process_iter(["name", "cmdline"])
           if (p.info["name"] or "") == "llama-server.exe" and any("gemma-3-4b" in (a or "") for a in (p.info["cmdline"] or []))]
    ram = psutil.virtual_memory()
    rec("vision server frees RAM when idle", not vis, f"vision procs={vis} | RAM now {ram.used/1e9:.1f}/{ram.total/1e9:.1f} GB ({ram.percent}%)")
    rec("idle RAM leaves headroom", ram.percent < 88, f"{ram.percent}%")

    d = api("/diagnostics")
    checks = {c["name"]: c for c in d["checks"]}
    rec("all diagnostics green after the run",
        all(c["status"] == "ok" for c in d["checks"]),
        ", ".join(f"{n}={c['status']}" for n, c in checks.items() if c["status"] != "ok") or "all ok")

    print("\n== PART 2 REPORT")
    print(f"  TOTAL {sum(1 for r in results if r[1])}/{len(results)}")
    for i, ok, det in results:
        if not ok:
            print(f"    FAIL {i} :: {det}")
    return 0 if all(r[1] for r in results) else 1

sys.exit(asyncio.run(main()))
