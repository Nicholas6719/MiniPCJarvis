"""Fact-store e2e against a RUNNING app: a sourced answer graduates into the
brain, a paraphrase is served without the LLM, provenance answers on demand,
and a changeable question is never cached. Run: python tests/facts_e2e.py PORT TOKEN"""
import json
import sys
import time
import urllib.request

port, tok = sys.argv[1], sys.argv[2]
BASE = f"http://127.0.0.1:{port}"
fails = []


def req(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json",
                                        "X-Jarvis-Token": tok})
    with urllib.request.urlopen(r, timeout=30) as resp:
        return json.loads(resp.read())


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


def say(text):
    req("POST", "/text", {"text": text})
    t0 = time.time()
    for _ in range(90):
        time.sleep(2)
        if req("GET", "/health")["state"] in ("idle", "sleeping") and time.time() - t0 > 4:
            break
    return time.time() - t0


def last_assistant():
    rows = req("GET", "/transcript?limit=4")["transcript"]
    for r in reversed(rows):
        if r["role"] == "assistant":
            return r["content"]
    return ""


# start clean: remove any kilimanjaro fact from earlier runs
for f in req("GET", "/facts")["facts"]:
    if "kilimanjaro" in f["question"].lower():
        req("DELETE", f"/facts/{f['id']}")

# 1. a sourced timeless answer should graduate into the store (background, ~10 s)
say("look up how tall mount kilimanjaro is")
a1 = last_assistant()
check("web turn answered", any(ch.isdigit() for ch in a1), a1[:80])
stored = None
for _ in range(12):
    time.sleep(5)
    stored = next((f for f in req("GET", "/facts")["facts"]
                   if "kilimanjaro" in f["question"].lower() and f["status"] == "active"), None)
    if stored:
        break
check("fact graduated into the store", stored is not None)

# 2. a paraphrase is served from the brain — fast, no web, no LLM
if stored:
    before = req("GET", "/facts")["stats"]["served"]
    dt = say("how tall is mount kilimanjaro")
    a2 = last_assistant()
    after = req("GET", "/facts")["stats"]["served"]
    check("paraphrase served from the store", after > before, f"{before}->{after}")
    check("fact answer spoken", any(ch.isdigit() for ch in a2), a2[:80])
    check("fact turn is fast (<6 s wall incl. speech)", dt < 6, f"{dt:.1f}s")

    # 3. provenance on demand — he never volunteers it, but answers for it
    say("how do you know that")
    a3 = last_assistant().lower()
    check("provenance names the source", "verified" in a3 or "." in a3 and "from" in a3, a3[:100])

# 4. changeable facts are never cached
say("look up the latest ai chip announcements")
time.sleep(20)
cached = [f for f in req("GET", "/facts")["facts"] if "ai chip" in f["question"].lower()]
check("realm-2 question was NOT cached", not cached, cached)

print(f"\nFACTS E2E: {'PASS' if not fails else f'FAIL ({len(fails)})'}")
sys.exit(0 if not fails else 1)
