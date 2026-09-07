"""The draft model answers only what the sidecar lets it, and a hedge is
never spoken.

Measured 2026-09-07: gemma-3-4b on the CPU, first word ~0.6 s, ~20 tok/s;
the big model 2-4 s. In the same bench it INVENTED a weather forecast when
asked - so `eligible()` is a whitelist of shape, `deferred()` a second net,
and both are asserted here rather than trusted.

Run: python tests/test_draft.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "draft.db"))

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


def main() -> int:
    from llm import draft as D
    from config import config

    on = bool(D.configured())
    check("a draft model is configured by default", on, D.configured())

    print("\n-- what it may answer --")
    for q in ("who wrote Dune", "how far is the moon", "what's the capital of Australia",
              "how many ounces in a pound", "explain photosynthesis in one sentence",
              "why is the sky blue", "who directed Jaws", "is a tomato a fruit",
              "tell me about the Roman empire"):
        check(f"eligible: {q!r}", D.eligible(q), q)

    print("\n-- what it may not --")
    for q in ("what's the weather tomorrow", "what time is it", "what's in the news",
              "how's tesla stock doing", "open spotify", "remind me at 7 to call mom",
              "what's my favourite colour", "what did I ask you earlier",
              "search for the best 3d printer", "show me the top", "render a duck",
              "what are the systems", "turn the volume up", "make me a bracket",
              "what did you say", "how much is 15 percent of 80",
              "who is the current president", "what's the latest iphone",
              "hey jarvis are you there", "set a timer for ten minutes",
              "what's on my screen", "read me my emails"):
        check(f"not eligible: {q!r}", not D.eligible(q), q)
    long = "who wrote " + "the very long book " * 8 + "that I mentioned"
    check("a long sentence is the big model's", not D.eligible(long))
    check("a statement is not a question", not D.eligible("the moon is far away"))
    check("nothing is eligible with a question pending", not D.eligible("who wrote Dune", pending=True))
    check("...or a model on the stage", not D.eligible("who wrote Dune", stage=True))
    check("...or a mandatory search", not D.eligible("who wrote Dune", must_use_tool=True))

    print("\n-- what it may say --")
    for good in ("Frank Herbert wrote Dune, sir.", "There are 16 ounces in a pound.",
                 "The capital of Australia is Canberra, sir."):
        check(f"kept: {good[:30]!r}", not D.deferred(good))
    for bad in ("DEFER", "I don't have access to real-time data, sir.",
                "As an AI, I cannot know the current weather.", "I'm not sure about that.",
                "I would need to check the website for that.", "", "  ",
                "I can't browse the internet, sir.", "Please provide more details."):
        check(f"thrown away: {bad[:30]!r}", D.deferred(bad), bad)

    print("\n-- the prompt --")
    msgs = D.messages_for([{"role": "user", "content": "who wrote Dune"},
                           {"role": "assistant", "content": "Frank Herbert, sir."},
                           {"role": "user", "content": "x" * 2000}] * 3, "and when", "[ctx]")
    check("a paragraph of persona, not the tool block",
          msgs[0]["role"] == "system" and len(msgs[0]["content"]) < 700 and "DEFER" in msgs[0]["content"])
    check("at most four recent turns, each clipped", len(msgs) == 6
          and all(len(m["content"]) <= 600 for m in msgs[1:-1]), len(msgs))
    check("the question is last, with the context", msgs[-1]["content"].endswith("and when")
          and msgs[-1]["content"].startswith("[ctx]"))

    print("\n-- the wiring --")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    orch = open(os.path.join(root, "orchestrator.py"), encoding="utf-8").read()
    check("the draft gets one go, in round 0, only when eligible",
          "draftrules.eligible(" in orch and "draft_tried" in orch and "server=draft" in orch)
    check("a deferral is never spoken: nothing is queued before the judge",
          "draft_defer = True" in orch and "the big model takes" in orch)
    check("the draft server is started after the big one and stopped with it",
          "_draft_boot" in orch and "await draft.stop()" in orch)
    srv = open(os.path.join(root, "llm", "llama_server.py"), encoding="utf-8").read()
    check("the draft server never adopts a shared one", "draft = LlamaServer(adopt=False)" in srv
          and "not self.adopt" in srv)
    prov = open(os.path.join(root, "llm", "provider.py"), encoding="utf-8").read()
    check("the provider streams from whichever server it is given",
          "srv = server or llama" in prov and "srv.base_url" in prov)
    m = config.get("llm", "models", default={}).get("gemma-3-4b", {})
    args = m.get("args") or []
    check("the draft model runs on the GPU with few CPU threads, one slot, short context",
          "-ngl" in args and "-np" in args and int(m.get("context") or 0) <= 8192
          and "-t" in args and int(args[args.index("-t") + 1]) <= 2, m)

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
