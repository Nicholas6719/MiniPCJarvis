"""Full manual-checklist replacement: drives the running app through every item the
user would test by hand, restores machine state (volume/mute/files), and prints a
PASS/FAIL report.  Run: python tests/checklist.py PORT TOKEN
"""
import asyncio, json, os, sys, time
import httpx, websockets

port, tok = sys.argv[1], sys.argv[2]
H = {"X-Jarvis-Token": tok}
BASE = f"http://127.0.0.1:{port}"
DESKTOP = r"C:\Users\nicho\OneDrive\Desktop"
results = []      # (section, item, ok, detail)
timings = []


def rec(section, item, ok, detail=""):
    results.append((section, item, bool(ok), str(detail)[:150]))
    print(f"  {'PASS' if ok else 'FAIL'}  {item[:52]:52} {str(detail)[:80]}")


def api(path, method="GET", **kw):
    r = httpx.request(method, BASE + path, headers=H, timeout=kw.pop("timeout", 60), **kw)
    r.raise_for_status()
    return r.json()


class Turn:
    """One spoken/typed turn: sends text, collects events until turn_done."""
    def __init__(self, ws):
        self.ws = ws

    async def __call__(self, text, timeout=180):
        t0 = time.time()
        httpx.post(BASE + "/text", headers=H, json={"text": text}, timeout=15)
        ev = {"reflex": None, "mode": None, "tools": [], "reply": "", "filler": None,
              "first": None, "files": None, "media": None, "browser": None, "web": None,
              "learned": [], "confirm": None}
        while time.time() - t0 < timeout:
            try:
                e = json.loads(await asyncio.wait_for(self.ws.recv(), timeout=timeout))
            except asyncio.TimeoutError:
                break
            k = e.get("kind")
            if k == "reflex":
                ev["reflex"], ev["mode"] = e.get("skill"), e.get("mode")
            elif k == "filler":
                ev["filler"] = e.get("text")
            elif k == "tool_call" and e.get("status") == "pending":
                ev["tools"].append(e["tool"])
            elif k == "confirmation_required":
                ev["confirm"] = e.get("tool")
            elif k == "brain_learned":
                ev["learned"].append(f"{e.get('text')}->{e.get('skill')}")
            elif k == "files":
                ev["files"] = (e.get("label"), e.get("count"))
            elif k == "images":
                ev["media"] = len(e.get("images") or [])
            elif k == "browser":
                ev["browser"] = e.get("title")
            elif k == "web":
                ev["web"] = e.get("stage")
            elif k == "assistant_delta":
                ev["first"] = ev["first"] or round(time.time() - t0, 2)
                ev["reply"] += e["text"]
            elif k == "turn_done":
                break
        ev["total"] = round(time.time() - t0, 1)
        ev["reply"] = ev["reply"].strip()
        timings.append((text, ev["first"], ev["total"], ev["reflex"]))
        return ev


async def main():
    vol0 = api("/system")["volume"]          # restore at the end
    async with websockets.connect(f"ws://127.0.0.1:{port}/ws?token={tok}", max_size=None) as ws:
        t = Turn(ws)

        print("\n== 3. REFLEXES (expect sub-second, no LLM)")
        r = await t("what time is it")
        rec("reflex", "what time is it", r["reflex"] == "time" and ":" in r["reply"], f"{r['first']}s {r['reply']}")
        r = await t("what's the date today")
        rec("reflex", "what's the date today", r["reflex"] == "date", f"{r['first']}s {r['reply']}")
        r = await t("what's the weather")
        rec("reflex", "weather (home = Framingham)", r["reflex"] == "weather" and "framingham" in r["reply"].lower(), r["reply"])
        r = await t("what's the weather tomorrow")
        rec("reflex", "weather tomorrow", r["reflex"] == "weather" and "tomorrow" in r["reply"].lower(), r["reply"])
        r = await t("what's the weather in boston")
        rec("reflex", "weather in Boston", r["reflex"] == "weather" and "boston" in r["reply"].lower(), r["reply"])
        r = await t("set the volume to 30 percent")
        v = api("/system")["volume"]["volume_percent"]
        rec("reflex", "set volume to 30 (real volume changed)", r["reflex"] == "volume_set" and v == 30, f"now {v}%")
        r = await t("mute")
        m1 = api("/system")["volume"]["muted"]
        r2 = await t("unmute")
        m2 = api("/system")["volume"]["muted"]
        rec("reflex", "mute then unmute", m1 is True and m2 is False, f"muted={m1} then {m2}")
        r = await t("pause the music")
        rec("reflex", "media pause key", r["reflex"] == "media_pause" and "media_control" in r["tools"], r["reply"])
        r = await t("how's the computer doing")
        rec("reflex", "system stats", r["reflex"] == "stats" and "percent" in r["reply"], r["reply"][:70])
        r = await t("what windows do i have open")
        rec("reflex", "list windows (no hidden browsers)", r["reflex"] == "windows" and "Brave Search" not in r["reply"], r["reply"][:70])
        r = await t("what's on my clipboard")
        rec("reflex", "read clipboard", r["reflex"] == "clipboard", r["reply"][:60])

        # screenshot -> desktop, verify the file then remove it
        before = set(os.listdir(DESKTOP))
        r = await t("take a screenshot and save it to my desktop")
        time.sleep(1)
        new = [f for f in set(os.listdir(DESKTOP)) - before if f.lower().endswith((".png", ".jpg"))]
        rec("reflex", "screenshot saved to Desktop", r["reflex"] == "screenshot" and bool(new), f"{r['reply']} file={new}")
        for f in new:
            try: os.remove(os.path.join(DESKTOP, f))
            except OSError: pass

        r = await t("remember that my favorite pizza place is Regina")
        rec("reflex", "remember a fact", r["reflex"] == "remember", r["reply"])
        r = await t("what's my favorite pizza place")
        rec("reflex", "recall it", "regina" in r["reply"].lower(), r["reply"][:70])
        r = await t("open my downloads folder")
        rec("reflex", "open downloads folder (FILES view)", r["reflex"] == "folder" and r["files"] and r["files"][0] == "downloads", str(r["files"]))
        r = await t("find the file called jarvis")
        rec("reflex", "find files by name", r["reflex"] == "find_file" and r["files"] and r["files"][1] > 0, str(r["files"]))
        r = await t("open notepad")
        time.sleep(2)
        wins = [w["title"] for w in api("/windows?thumbs=0")["windows"]]
        rec("reflex", "open an installed app (Notepad)", r["reflex"] == "open_app" and any("notepad" in w.lower() for w in wins), f"{r['reply']} | windows={wins[:4]}")
        r = await t("switch to notepad")
        rec("reflex", "switch to a window", r["reflex"] == "switch" and "error" not in r["reply"].lower(), r["reply"])
        r = await t("close notepad")
        rec("reflex", "close an app", r["reflex"] == "close_app", r["reply"])
        r = await t("open youtube")
        rec("reflex", "'open youtube' (name only -> site in-app)", "open_application" in r["tools"] or "open_url" in r["tools"], f"{r['reply']} tools={r['tools']}")
        r = await t("open netflix")
        rec("reflex", "'open netflix' (name only)", bool(r["tools"]), f"{r['reply']} tools={r['tools']}")

        print("\n== 4. BRAIN LEARNING")
        r = await t("when I say movie time, set the volume to 60 and open netflix.com")
        rec("brain", "teach a command", r["reflex"] == "teach" and "got it" in r["reply"].lower(), r["reply"][:90])
        r = await t("movie time")
        v = api("/system")["volume"]["volume_percent"]
        rec("brain", "run the taught routine", r["reflex"] == "command" and v == 60, f"vol={v} tools={r['tools']}")
        r = await t("no, I meant what time is it")
        cmds = api("/brain")["commands"]
        rec("brain", "correction re-runs + un-learns", r["reflex"] == "time" and not any(c["phrase"] == "movie time" for c in cmds), f"{r['reply'][:40]} | commands={[c['phrase'] for c in cmds]}")
        r = await t("tell me if the cpu goes above 95 percent for 5 minutes")
        rules = api("/config")["config"]["proactive"].get("rules", [])
        rec("brain", "standing rule created", r["reflex"] == "watch" and len(rules) == 1, f"{r['reply'][:60]} rules={rules}")
        r = await t("stop watching the cpu")
        rules = api("/config")["config"]["proactive"].get("rules", [])
        rec("brain", "standing rule removed", r["reflex"] == "unwatch" and not rules, r["reply"])

        print("\n== 5. LLM PATH")
        r = await t("why is the sky blue")
        bad = [c for c in "*#`_" if c in r["reply"]]
        rec("llm", "knowledge answer, no tools, no markdown", not r["tools"] and not bad and len(r["reply"]) > 20, f"filler={r['filler']!r}@ first={r['first']}s | {r['reply'][:60]}")
        rec("llm", "filler spoken while thinking", bool(r["filler"]), r["filler"])
        r = await t("search the web for the best mini pc under 500 dollars")
        rec("llm", "web search + spoken summary", "web_search" in r["tools"] and len(r["reply"]) > 20, f"{r['first']}s {r['reply'][:70]}")
        r = await t("show me a picture of saturn")
        rec("llm", "images -> MEDIA view", (r["media"] or 0) > 0, f"{r['media']} images")
        r = await t("open example.com and tell me what the page says")
        rec("llm", "open a page and read it", "example" in r["reply"].lower(), f"{r['reply'][:70]}")
        r = await t("write me a two line poem about coffee")
        rec("llm", "creative reply", len(r["reply"]) > 20 and not r["tools"], r["reply"][:70].replace("\n", " / "))

        print("\n== 6. VISION")
        r = await t("what's on my screen right now", timeout=240)
        rec("vision", "analyze screen (cold)", "analyze_screen" in r["tools"] and len(r["reply"]) > 30, f"{r['total']}s {r['reply'][:70]}")
        rec("vision", "does not describe JARVIS itself", "jarvis" not in r["reply"].lower()[:60], r["reply"][:60])
        r = await t("describe my screen in one sentence", timeout=240)
        rec("vision", "analyze screen (warm)", "analyze_screen" in r["tools"], f"{r['total']}s {r['reply'][:60]}")

    # restore volume
    api("/system/volume", "POST", json={"percent": vol0.get("volume_percent") or 40})
    api("/system/mute", "POST", json={"muted": bool(vol0.get("muted"))})

    print("\n== 7. OS TABS (API level)")
    lst = api("/files?path=documents")
    rec("files", "list a folder", lst.get("count", 0) > 0, f"{lst.get('label')} {lst.get('count')} items")
    tmp = os.path.join(r"C:\Users\nicho\Documents", "jarvis_checklist_tmp.txt")
    open(tmp, "w").write("hello from the checklist run")
    pv = api("/files/preview?path=documents/jarvis_checklist_tmp.txt")
    rec("files", "preview a text file", pv.get("type") == "text" and "hello" in pv.get("text", ""), pv.get("type"))
    rn = api("/files/op", "POST", json={"op": "rename", "path": tmp, "new_name": "jarvis_checklist_renamed"})
    rec("files", "rename", rn.get("to") == "jarvis_checklist_renamed.txt", rn)
    mv = api("/files/op", "POST", json={"op": "move", "path": r"C:\Users\nicho\Documents\jarvis_checklist_renamed.txt", "destination": "downloads"})
    rec("files", "move to another folder", "moved" in mv, mv)
    dl = api("/files/op", "POST", json={"op": "delete", "path": r"C:\Users\nicho\Downloads\jarvis_checklist_renamed.txt"})
    rec("files", "recycle (undoable)", "recycled" in dl, dl)
    bad = httpx.get(BASE + "/files?path=C:/Windows", headers=H, timeout=30).json()
    rec("files", "sandbox blocks outside folders", "error" in bad, bad.get("error", "")[:60])
    sr = api("/files/search?q=jarvis")
    rec("files", "find box", sr.get("count", 0) > 0, f"{sr.get('count')} hits")

    w = api("/windows")
    thumbs = sum(1 for x in w["windows"] if x.get("thumb"))
    rec("apps", "window tiles with live thumbnails", len(w["windows"]) > 0 and thumbs > 0, f"{len(w['windows'])} windows, {thumbs} thumbs")
    if w["windows"]:
        act = api("/windows/act", "POST", json={"hwnd": w["windows"][0]["hwnd"], "action": "focus"})
        rec("apps", "focus a window from the HUD", "error" not in act, act)

    sysd = api("/system")
    rec("system", "system snapshot (net/battery/gauges/procs)", sysd["stats"]["cpu_percent"] is not None and "network" in sysd and len(sysd["processes"]) > 0,
        f"cpu {sysd['stats']['cpu_percent']}% ram {sysd['stats']['ram_percent']}% net {sysd['network']['ip']}")
    api("/system/volume", "POST", json={"percent": 45})
    rec("system", "volume slider writes through", api("/system")["volume"]["volume_percent"] == 45, "45%")
    api("/system/volume", "POST", json={"percent": vol0.get("volume_percent") or 40})

    b = api("/browser/open", "POST", json={"url": "https://example.com"}, timeout=90)
    rec("browser", "open a page in-app", b.get("title") == "Example Domain", b.get("title"))
    b2 = api("/browser/click", "POST", json={"x": 0.3, "y": 0.42}, timeout=90)
    rec("browser", "click through the screenshot", "error" not in b2, b2.get("url"))
    b3 = api("/browser/scroll", "POST", json={"dy": 300}, timeout=90)
    rec("browser", "scroll", "error" not in b3, b3.get("url"))

    print("\n== 8. SETTINGS / MISC")
    voices = api("/voices")
    vlist = voices.get("voices") or voices
    rec("settings", "voice list populated", len(vlist) > 3, f"{len(vlist)} voices")
    cfg = api("/config")["config"]
    rec("settings", "brain + filler toggles present", cfg.get("brain", {}).get("enabled") is not None and cfg.get("speech", {}).get("fillers") is not None,
        f"brain={cfg['brain']['enabled']} fillers={cfg['speech']['fillers']}")
    rec("settings", "home location saved", cfg.get("weather", {}).get("home") == "Framingham, MA", cfg.get("weather", {}).get("home"))
    tr = api("/transcript")["transcript"]
    rec("misc", "transcript persisted (hidden on relaunch in UI)", len(tr) > 5, f"{len(tr)} rows")
    mem = api("/metrics")["summary"]
    rec("misc", "metrics collected", mem.get("turns", 0) > 10, json.dumps(mem))
    ds = api("/brain/export")
    rec("misc", "training dataset export", ds.get("examples", 0) > 0, f"{ds.get('examples')} examples -> {ds.get('path')}")

    print("\n== REPORT")
    bad = [r for r in results if not r[2]]
    for sect in dict.fromkeys(r[0] for r in results):
        items = [r for r in results if r[0] == sect]
        print(f"  {sect:9} {sum(1 for i in items if i[2])}/{len(items)}")
    print(f"  TOTAL     {sum(1 for r in results if r[2])}/{len(results)}")
    if bad:
        print("  failures:")
        for s, i, _, d in bad:
            print(f"    [{s}] {i} :: {d}")
    fast = [x for x in timings if x[3]]
    slow = [x for x in timings if not x[3]]
    if fast:
        print(f"  reflex turns: median first word {sorted(x[1] or 9 for x in fast)[len(fast)//2]:.2f}s")
    if slow:
        print(f"  llm turns:    median first word {sorted(x[1] or 99 for x in slow)[len(slow)//2]:.2f}s")
    return 0 if not bad else 1

sys.exit(asyncio.run(main()))
