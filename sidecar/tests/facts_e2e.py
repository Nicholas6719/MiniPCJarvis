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


def _rows():
    return req("GET", "/transcript?limit=60")["transcript"]


_answer = ""


def say(text):
    """Speak a turn and wait for ITS answer.

    Anchors on the transcript row ID of our own user row — content matching reads
    a previous run's answer (these suites repeat phrases), and row COUNTING breaks
    silently once the window is full: an old copy scrolls off exactly as the new
    one arrives. Waits for QUIET first: /text mid-speech drops the turn."""
    global _answer
    _answer = ""
    for _ in range(30):
        if req("GET", "/health")["state"] in ("idle", "sleeping"):
            break
        time.sleep(2)
    max_id_before = max([r["id"] for r in _rows()] or [0])
    req("POST", "/text", {"text": text})
    t0 = time.time()
    for _ in range(360):
        time.sleep(0.5)      # fine-grained: a fact turn is ~0.3 s + speech
        rows = _rows()
        mine = [r for r in rows if r["id"] > max_id_before
                and r["role"] == "user" and r["content"] == text]
        if not mine:
            continue
        after = [r for r in rows if r["id"] > mine[-1]["id"] and r["role"] == "assistant"]
        if after:
            _answer = after[0]["content"]
            break
    return time.time() - t0


def last_assistant():
    return _answer


def last_first_token_ms():
    """How long until he STARTED answering. Wall-clock to the transcript row is
    dominated by TTS playback (a spoken sentence is ~4 s of audio no matter where
    the answer came from) — first token is what the fact store actually changes."""
    recent = req("GET", "/metrics").get("recent") or []
    return (recent[-1] or {}).get("first_token_ms") if recent else None


# start clean: remove leftovers from earlier runs
for f in req("GET", "/facts")["facts"]:
    if "eiffel" in f["question"].lower():
        req("DELETE", f"/facts/{f['id']}")

# 1. a sourced timeless answer should graduate into the store (background, ~15 s).
# Completed history — unambiguously timeless. (A mountain's height is NOT: surveys
# revise it, and the classifier correctly rejects it. Learned the hard way.)
say("look up what year the eiffel tower was completed")
a1 = last_assistant()
web_ms = last_first_token_ms()
check("web turn answered", any(ch.isdigit() for ch in a1), a1[:80])
stored = None
for _ in range(12):
    time.sleep(5)
    stored = next((f for f in req("GET", "/facts")["facts"]
                   if "eiffel" in f["question"].lower() and f["status"] == "active"), None)
    if stored:
        break
check("fact graduated into the store", stored is not None)

# 2. a paraphrase is served from the brain — fast, no web, no LLM
if stored:
    before = req("GET", "/facts")["stats"]["served"]
    say("when was the eiffel tower finished")
    fact_ms = last_first_token_ms()
    a2 = last_assistant()
    after = req("GET", "/facts")["stats"]["served"]
    check("paraphrase served from the store", after > before, f"{before}->{after}")
    check("fact answer spoken", any(ch.isdigit() for ch in a2), a2[:80])
    # the POINT of the fact store: he STARTS answering the same question almost
    # instantly instead of searching the web for it again
    check("fact answer starts within a second", fact_ms is not None and fact_ms < 1000,
          f"{fact_ms} ms")
    check("fact start far beats the web start",
          fact_ms is not None and web_ms is not None and fact_ms < web_ms / 3,
          f"fact {fact_ms} ms vs web {web_ms} ms")

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
