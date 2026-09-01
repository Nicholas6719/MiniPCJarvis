"""A reminder should sound like JARVIS, not like a parrot.

Nicholas set a standing 9 p.m. reminder to wear his retainers, and every night
got back `A reminder: wear my retainers` — his own words, read at him. What he
asked for on 2026-09-01: *"sir, it's about time to put in your retainers"* or
*"I believe you have to wear your retainers now, sir"* — his assistant's voice,
and a different sentence most nights.

So it goes through the LLM. Which means this file mostly tests what happens when
the LLM is unhelpful, because that is where a feature like this hurts: a
reminder that arrives four minutes late, or not at all, is worse than a dull one.

Offline: the model is stubbed throughout. No app, no network, no LLM.
Run: python tests/test_reminder_voice.py
"""
import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "voice.db"))

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


def main() -> int:
    import reminder_voice as rv

    # keep the phrasing history in a scratch file, never his real one
    rv._STATE = __import__("pathlib").Path(tempfile.mkdtemp()) / "phrasings.json"

    def with_model(reply, *, delay=0.0, boom=False):
        async def fake(errand, avoid):
            if delay:
                await asyncio.sleep(delay)
            if boom:
                raise RuntimeError("llama-server is down")
            return reply(errand, avoid) if callable(reply) else reply
        rv._think = fake

    say = lambda t: asyncio.run(rv.phrase(t))  # noqa: E731

    # --- the good case -------------------------------------------------------
    with_model("Sir, it's about time to put in your retainers.")
    out = say("wear my retainers")
    check("it speaks in his voice", out == "Sir, it's about time to put in your retainers.", out)

    # --- it must still be ABOUT the thing ------------------------------------
    with_model("A pleasant evening to you, sir.")
    out = say("wear my retainers")
    check("a phrasing that lost the subject is rejected",
          "retainer" in out.lower(), out)

    # --- the model misbehaving in the usual ways -----------------------------
    for label, reply in (
            ("empty", ""),
            ("just whitespace", "   "),
            ("a whole essay", "Sir. " + "Retainers are important for dental alignment. " * 12),
            ("markdown", "**Sir**, your `retainers` await."),
            ("quoted", '"Sir, your retainers, if you would."'),
            ("scaffolded", "Reminder: Sir, your retainers await."),
    ):
        with_model(reply)
        out = say("wear my retainers")
        ok = (out and "retainer" in out.lower() and len(out) <= 160
              and "*" not in out and "`" not in out
              and not out.lower().startswith("reminder:")
              and not out.startswith('"'))
        check(f"handles {label}", bool(ok), repr(out))

    # --- the model is slow or dead: he still gets reminded, promptly ---------
    rv.BUDGET_S = 0.3
    with_model("far too late to be useful", delay=5.0)
    import time as _t
    t0 = _t.time()
    out = say("wear my retainers")
    took = _t.time() - t0
    check("a slow model does not delay the reminder", took < 2.0, f"{took:.1f}s")
    check("...and he is still reminded", "retainer" in out.lower(), out)

    with_model("", boom=True)
    out = say("wear my retainers")
    check("a dead model still produces a reminder", "retainer" in out.lower(), out)
    check("...and it still sounds like JARVIS", "sir" in out.lower(), out)

    # --- it does not repeat itself night after night -------------------------
    # With the model down, the fallbacks must still rotate rather than sending
    # the identical sentence every night.
    seen = {say("wear my retainers") for _ in range(5)}
    check("even the fallback varies night to night", len(seen) >= 3,
          f"only {len(seen)} distinct in 5 nights: {seen}")

    # --- a bare noun still reads as English ----------------------------------
    with_model("", boom=True)
    out = say("retainers")
    check("a one-word reminder is not 'time to retainers'",
          "to retainers" not in out.lower(), out)

    # --- and it never, ever raises -------------------------------------------
    for weird in ("", "   ", None, "remind me to " * 40):
        try:
            r = asyncio.run(rv.phrase(weird))
            ok = isinstance(r, str) and len(r) > 0
        except Exception as e:
            ok = False
            r = f"RAISED {e}"
        check(f"never raises on {weird!r:.30}", ok, r)

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
