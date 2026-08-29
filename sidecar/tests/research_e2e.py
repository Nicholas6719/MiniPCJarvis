"""Does research actually WORK — the thing he asks for, judged the way he judges it.

Every micro-suite was green while JARVIS failed three real research tasks in a row. That
is the definition of testing the wrong thing, so this suite tests the headline capability
end to end and reports what actually came back: did it search, did it read anything, how
long did it take, and is the answer substantive or a shrug.

Run: python tests/research_e2e.py PORT TOKEN
"""
import asyncio
import json
import re
import sys
import time

import httpx
import websockets

port, tok = sys.argv[1], sys.argv[2]
H = {"X-Jarvis-Token": tok}
BASE = f"http://127.0.0.1:{port}"

# (task, needs_live_web). The flag is the whole point: when live search is blocked, a
# substantive answer to a needs-live question CANNOT have come from anywhere but stale
# model memory, so it is fabrication by construction — no keyword matching required.
TASKS = [
    ("research the best mini PC of 2026 and tell me which one to buy", True),
    ("look up what the current price of an RTX 5090 is", True),
    ("search the web for the latest news about AMD Strix Halo", True),
    ("research whether the Ryzen 7 8845HS supports ECC memory", False),
    ("find out when the next total solar eclipse is visible from Boston", False),
]

# A reply that is really a shrug, however politely phrased.
# NOTE the apostrophe class: the model writes U+2019 ("couldn’t"), and matching only the
# ASCII apostrophe made this suite report PASS on three replies that were plainly
# failures. A test that cannot see the failure is worse than no test.
_AP = r"['’]"
SHRUG = re.compile(
    rf"\b(i (?:couldn{_AP}?t|could not|cannot|can{_AP}?t)|no results|"
    rf"nothing (?:came back|found)|unable to|i don{_AP}?t have|i do not have|"
    rf"not able to|failed to|couldn{_AP}?t find|no (?:recent|reliable) )", re.I)


async def run(ws, text):
    t0 = time.time()
    r = httpx.post(BASE + "/text", headers=H, json={"text": text}, timeout=20).json()
    if not r.get("ok"):
        return {"text": text, "error": f"refused: {r}", "reply": "", "tools": [],
                "errors": [], "secs": 0, "first": None}
    reply, tools, errors, first = "", [], [], None
    sources = []          # everything the tools actually put in front of the model
    while time.time() - t0 < 300:
        try:
            e = json.loads(await asyncio.wait_for(ws.recv(), timeout=300))
        except asyncio.TimeoutError:
            break
        k = e.get("kind")
        if k == "assistant_delta":
            if first is None:
                first = time.time() - t0
            reply += e["text"]
        elif k == "tool_call" and e.get("status") in (None, "pending"):
            tools.append(e.get("tool"))
        elif k == "tool_call" and e.get("status") == "error":
            errors.append(f"{e.get('tool')}: {str(e.get('result'))[:90]}")
        elif k == "error":
            errors.append(str(e.get("summary"))[:120])
        elif k == "web" and e.get("results"):
            for it in e["results"]:
                sources.append(f"{it.get('title','')} {it.get('snippet','')}")
        elif k == "browser" and (e.get("text") or e.get("title")):
            sources.append(f"{e.get('title','')} {(e.get('text') or '')[:4000]}")
        elif k == "turn_done":
            break
    return {"text": text, "reply": reply.strip(), "tools": tools, "errors": errors,
            "sources": " ".join(sources).lower(),
            "secs": round(time.time() - t0, 1), "first": round(first, 1) if first else None}


# Says plainly that it cannot search, rather than inventing an answer.
HONEST = re.compile(
    rf"(search (?:is |was )?(?:un)?available|can{_AP}?t (?:search|look that up|look it up)|"
    rf"web search (?:is )?(?:un)?available|no (?:live )?web search|api key|"
    rf"can{_AP}?t (?:provide|retrieve|give you) (?:current|a current|the current))", re.I)

# The tell-tale of fabrication: a confident specific claim about live data with no source.
LIVE_CLAIM = re.compile(r"\$\s?\d|\b\d{3,5}\s?(?:dollars|usd)\b|\bcosts?\s+(?:about\s+)?\$?\d", re.I)


async def main() -> int:
    """Two legitimate outcomes, and one that is never acceptable.

    If web search works, a research task should come back with a substantive answer.
    If it is blocked, the ONLY acceptable answer is saying so. Answering anyway from
    stale model memory is the failure this exists to catch — it is how "the top-rated
    2026 mini PC is the Intel NUC 13 Extreme" (a 2022 machine) reached the user, and it
    is worse than a refusal because it looks like research.
    """
    results = []
    async with websockets.connect(f"ws://127.0.0.1:{port}/ws?token={tok}", max_size=None) as ws:
        for t, needs_live in TASKS:
            r = await run(ws, t)
            r["needs_live"] = needs_live
            results.append(r)
            r["searched"] = any(x in ("web_search", "research", "fetch_page", "open_url")
                                for x in r["tools"])
            # A correct answer can be SHORT. "The RTX 5090 is listed at about
            # $6,810." is 39 characters and was failing the length floor — this
            # suite was calling a sourced, correct, perfectly phrased answer a
            # shrug for being one character under, which is exactly backwards:
            # brevity is the house style. A reply carrying a concrete figure has
            # answered the question whatever its length.
            r["substantive"] = (not SHRUG.search(r["reply"])
                                and not HONEST.search(r["reply"])
                                and (len(r["reply"]) >= 40
                                     or bool(LIVE_CLAIM.search(r["reply"]))))
            r["honest"] = bool(HONEST.search(r["reply"]))
            r["fabricated"] = False   # decided below, once search status is known
            verdict = ("answered" if r["substantive"] else
                       "honest-refusal" if r["honest"] else "shrug")
            print(f"\n  [{verdict}] {t}")
            print(f"        {r['secs']}s  first={r['first']}s  tools={r['tools']}")
            for e in r.get("errors", []):
                print(f"        TOOL-ERROR {e}")
            print(f"        {r['reply'][:220] or '<empty>'}")
            await asyncio.sleep(1)

    st = httpx.get(BASE + "/diagnostics", headers=H, timeout=60).json()
    search = next((c for c in st.get("checks", []) if "Search" in c.get("name", "")), {})
    live = search.get("status") == "ok"
    if not live:
        for r in results:
            # Not "did it answer a live question" — that unfairly condemned a correct,
            # sourced answer ("Strix Halo debuted January 2025 with RDNA 3.5", which was
            # straight off the headline Hacker News returned). The real question is
            # whether the claim is GROUNDED in what the tools actually returned. Take the
            # distinctive tokens of the reply — model numbers, product names, years — and
            # require at least one to appear in the sources.
            if not (r["needs_live"] and r["substantive"]):
                continue
            claims = set(re.findall(r"\b(?:[A-Z][a-zA-Z]{2,}|\d{4}|[A-Za-z]+\s?\d{3,4}[A-Za-z+]*)\b",
                                    r["reply"]))
            claims = {c.lower() for c in claims
                      if c.lower() not in {"the", "afraid", "live", "search", "web", "sir",
                                           "next", "total", "solar", "boston", "amd", "intel"}}
            grounded = any(c in r["sources"] for c in claims) if claims else False
            r["fabricated"] = not grounded
            r["grounded"] = grounded
    answered = sum(1 for r in results if r["substantive"])
    honest = sum(1 for r in results if r["honest"])
    fabricated = [r["text"] for r in results if r["fabricated"]]
    shrugs = [r["text"] for r in results if not r["substantive"] and not r["honest"]]
    slow = sorted(r["secs"] for r in results)

    print(f"\n  web search: {search.get('status')} — {search.get('detail')}")
    print(f"  answered {answered}/{len(results)} · honest refusals {honest} · "
          f"fabricated {len(fabricated)} · bare shrugs {len(shrugs)}")
    print(f"  median {slow[len(slow) // 2]}s   worst {slow[-1]}s")
    for t in fabricated:
        print(f"      FABRICATED {t}")
    for t in shrugs:
        print(f"      BARE SHRUG {t}")

    # Never acceptable: making it up, or failing without saying why.
    ok = not fabricated and not shrugs
    if live:
        ok = ok and answered == len(results)   # search works: it must actually answer
    print("RESEARCH: " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1

sys.exit(asyncio.run(main()))
