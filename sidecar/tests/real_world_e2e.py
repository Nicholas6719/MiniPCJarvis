"""How good is JARVIS, really — the conversations Nicholas actually has.

Not micro-checks: whole exchanges, judged the way a person judges them.
  ACCURACY     the answer contains what a correct answer must contain
  INTELLIGENCE it took the right ROUTE (brain / fact / web / model) and did not
               fabricate, shrug, or parrot
  SPEED        time to first word, and to the end of the turn

Every case carries its own expectation, so this is a scorecard, not a vibe. It
runs against the RUNNING install and reports per-category and per-path numbers.

Run: python tests/real_world_e2e.py PORT TOKEN [--quick]
"""
import asyncio
import json
import re
import statistics
import sys
import time

# The console is cp1252 and the model writes narrow no-break spaces; the
# suite crashed mid-run on one (release 27), which is not a JARVIS failure.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import httpx
import websockets

port, tok = sys.argv[1], sys.argv[2]
QUICK = "--quick" in sys.argv
H = {"X-Jarvis-Token": tok}
BASE = f"http://127.0.0.1:{port}"

# A reply that is really a failure, however politely phrased.
_AP = r"['’]"
SHRUG = re.compile(
    rf"\b(i (?:couldn{_AP}?t|could not|cannot|can{_AP}?t)|no results|nothing (?:came back|found)|"
    rf"unable to|i don{_AP}?t have|not able to|failed to)", re.I)


def has(*words):
    """The answer must contain all of these (case-insensitive)."""
    return lambda r: all(w.lower() in r.lower() for w in words)


def any_of(*words):
    return lambda r: any(w.lower() in r.lower() for w in words)


def num_between(lo, hi):
    """Some number in the reply falls in range — for live readings."""
    def check(r):
        return any(lo <= float(n) <= hi for n in re.findall(r"\d+(?:\.\d+)?", r))
    return check


# (utterance, category, expected_route|None, check, note)
#   route: reflex skill name, "fact", or None = don't care / model path
CASES = [
    # --- the fast lane: things he must never think about ---------------------
    ("what time is it", "instant", "time", lambda r: re.search(r"\d", r), ""),
    ("how's my pc doing", "instant", "stats", num_between(0, 100), "live CPU/RAM"),
    ("what windows do i have open", "instant", "windows", lambda r: len(r) > 10, ""),
    ("what's on my clipboard", "instant", "clipboard", lambda r: len(r) > 5, ""),
    # --- knowledge he should own (fact store or straight answer) -------------
    ("how many legs does a spider have", "knowledge", None, has("eight"), "learned overnight"),
    ("what's the capital of Australia", "knowledge", None, has("canberra"), ""),
    ("how many milliliters in a US cup", "knowledge", None, any_of("236", "237", "240"), ""),
    ("who wrote the Lord of the Rings", "knowledge", None, has("tolkien"), ""),
    ("what year did the Berlin Wall fall", "knowledge", None, has("1989"), ""),
    # --- live web: must go get it, must not answer from memory ---------------
    ("what's the weather in Framingham", "live", "weather", num_between(-20, 120), ""),
    ("look up who won the last super bowl", "live", None,
     lambda r: not SHRUG.search(r) and len(r) > 25, "needs the web"),
    ("research the best mini pc of 2026", "live", None,
     lambda r: not SHRUG.search(r) and len(r) > 40, "the task he failed in July"),
    # --- his machine, his files ---------------------------------------------
    ("show me my documents folder", "files", "folder", any_of("documents", "items"), ""),
    ("what files are in the recycle bin", "files", "recycle_bin",
     lambda r: not SHRUG.search(r), "could not do this yesterday"),
    ("find the file called jarvis", "files", "find_file", any_of("found", "jarvis"), ""),
    # --- memory + reminders --------------------------------------------------
    ("remember that my desk lamp is on the left", "memory", "remember", lambda r: len(r) > 3, ""),
    ("what do you remember about my desk lamp", "memory", None, has("left"), "recall it"),
    ("remind me in 30 minutes to check the oven", "memory", "reminder", any_of("reminder", "30"), ""),
    ("what reminders do i have", "memory", "reminders", has("oven"), "new capability"),
    ("don't remind me about the oven anymore", "memory", "unremind", any_of("won't", "cancel", "done"),
     "the phrase that broke yesterday"),
    # --- conversation and manners -------------------------------------------
    ("thank you jarvis", "manners", "thanks",
     lambda r: "thank you jarvis" not in r.lower() and len(r) < 40, "must not parrot"),
    ("how do you know that", "manners", "provenance", lambda r: len(r) > 8, ""),
    # --- judgement: things that LOOK like commands but are not ---------------
    ("what time should i go to bed", "judgement", None,
     lambda r: not re.fullmatch(r"\s*it'?s \d.*", r.strip(), re.I), "not a clock reading"),
    ("how many hours should i sleep", "judgement", None, lambda r: len(r) > 20, "not sleep mode"),
    # These two must DO the thing, not merely avoid the wrong thing. The first
    # version of this suite only checked "did he fall asleep", so it passed
    # "minimize everything" -> "I've set a CPU alert" with a green tick. A check
    # that can't see a wrong action isn't a check.
    ("be quieter", "judgement", "volume_rel",
     lambda r: "standing by" not in r.lower() and re.search(r"\d", r), "must lower the volume"),
    ("minimize everything", "judgement", "show_desktop",
     lambda r: "standing by" not in r.lower() and any_of("desktop", "minimi")(r),
     "windows, not him, and not a CPU alert"),
    ("bring my windows back", "judgement", "restore_windows",
     any_of("back", "restored"), "undo it again"),
]

if QUICK:
    CASES = [c for c in CASES if c[1] in ("instant", "knowledge", "manners")]


async def turn(ws, text):
    """Run one turn, capture route + reply + timings."""
    t0 = time.time()
    for _ in range(90):                    # wait out a busy app rather than dropping the turn
        try:
            r = httpx.post(BASE + "/text", headers=H, json={"text": text}, timeout=10).json()
            if r.get("ok"):
                break
        except Exception:
            pass
        await asyncio.sleep(1)
    route, first, reply, total, tools = None, None, "", None, []
    while time.time() - t0 < 180:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=180)
        except asyncio.TimeoutError:
            break
        e = json.loads(raw)
        k = e.get("kind")
        if k == "reflex":
            route = e.get("skill")
        elif k == "tool_call" and e.get("status") == "pending":
            tools.append(e.get("tool"))
        elif k == "assistant_delta":
            if first is None:
                first = time.time() - t0
            reply += e.get("text", "")
        elif k == "turn_done":
            total = time.time() - t0
            if e.get("text"):
                reply = e["text"]
            break
    return {"route": route, "reply": reply.strip(), "first": first,
            "total": total or (time.time() - t0), "tools": tools}


async def main() -> int:
    rows = []
    async with websockets.connect(f"ws://127.0.0.1:{port}/ws?token={tok}", max_size=None) as ws:
        for text, cat, want_route, check, note in CASES:
            # settle: a turn fired while he is still speaking is dropped
            for _ in range(40):
                if httpx.get(BASE + "/health", timeout=5).json()["state"] in ("idle", "sleeping"):
                    break
                await asyncio.sleep(1)
            r = await turn(ws, text)
            try:
                accurate = bool(check(r["reply"]))
            except Exception:
                accurate = False
            routed = want_route is None or r["route"] == want_route
            r.update({"text": text, "cat": cat, "accurate": accurate, "routed": routed,
                      "want_route": want_route, "note": note})
            rows.append(r)
            mark = "PASS" if accurate and routed else ("ROUTE" if accurate else "FAIL")
            first_s = f"{r['first']:.1f}s" if r["first"] else "  -  "
            print(f"  {mark:5} [{cat:9}] {text[:44]:44} {first_s:>6} / {r['total']:5.1f}s  "
                  f"route={str(r['route']):12} {r['reply'][:60]}")
            await asyncio.sleep(1)

    # ---------------------------------------------------------------- scorecard
    def pct(sel):
        got = [x for x in rows if sel(x)]
        return f"{len(got)}/{len(rows)}" if got else f"0/{len(rows)}"

    print("\n" + "=" * 78)
    acc = sum(1 for r in rows if r["accurate"])
    rt = sum(1 for r in rows if r["routed"])
    print(f"ACCURACY     {acc}/{len(rows)}  ({100 * acc / len(rows):.0f}%)")
    print(f"ROUTING      {rt}/{len(rows)}  (took the intended path)")
    firsts = [r["first"] for r in rows if r["first"]]
    totals = [r["total"] for r in rows]
    print(f"SPEED        first word: median {statistics.median(firsts):.2f}s  "
          f"p90 {sorted(firsts)[int(len(firsts) * .9) - 1]:.2f}s   "
          f"turn: median {statistics.median(totals):.1f}s  worst {max(totals):.1f}s")
    print("\nBY CATEGORY")
    for cat in dict.fromkeys(c[1] for c in CASES):
        sub = [r for r in rows if r["cat"] == cat]
        if not sub:
            continue
        a = sum(1 for r in sub if r["accurate"])
        f = [r["first"] for r in sub if r["first"]]
        speed = f"   first word median {statistics.median(f):.2f}s" if f else ""
        print(f"  {cat:10} accuracy {a}/{len(sub)}{speed}")
    print("\nBY PATH (how much work each answer cost)")
    for p in ("reflex", "fact", "model"):
        sub = [r for r in rows if (p == "fact" and r["route"] == "fact")
               or (p == "reflex" and r["route"] not in (None, "fact", "general"))
               or (p == "model" and r["route"] in (None, "general"))]
        if not sub:
            continue
        f = [r["first"] for r in sub if r["first"]]
        print(f"  {p:7} {len(sub):2} turns   first word median "
              f"{statistics.median(f):.2f}s   turn median "
              f"{statistics.median([r['total'] for r in sub]):.1f}s")
    bad = [r for r in rows if not r["accurate"]]
    if bad:
        print("\nWRONG ANSWERS")
        for r in bad:
            print(f"  [{r['cat']}] {r['text']}")
            print(f"        wanted: {r['note'] or 'see check'}")
            print(f"        got:    {r['reply'][:150] or '<empty>'}")
    mis = [r for r in rows if not r["routed"] and r["accurate"]]
    if mis:
        print("\nRIGHT ANSWER, SLOWER PATH THAN IT DESERVED")
        for r in mis:
            print(f"  {r['text']:46} wanted={r['want_route']:12} got={r['route']}")
    print("=" * 78)
    return 0 if acc == len(rows) else 1


sys.exit(asyncio.run(main()))
