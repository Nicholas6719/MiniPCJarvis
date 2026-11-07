"""A reminder is his own words, plainly - never a model's rephrasing.

From 2026-09-01 his reminders went through the LLM "so they sound like JARVIS".
Ten days of his phone (10-27..11-06) show what that became: "ingest your daily
supplements", "lace up your retainers", "don your retainers", "strap on your
retainers", and once, cut off mid-sentence, "Sir, now is the appropriate time
to put on your." Since 2026-11-07 a reminder is said plainly, the same way
every night, from the words he set - no model, no wit, ever.

Offline, and nothing to stub: there is no model to call any more.
Run: python tests/test_reminder_voice.py
"""
import inspect
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

    # --- his two, exactly ------------------------------------------------------
    check("supplements, plainly", rv.plain("take my supplements") == "Time for your supplements, sir.",
          rv.plain("take my supplements"))
    check("retainers, plainly", rv.plain("wear my retainers") == "Time for your retainers, sir.",
          rv.plain("wear my retainers"))
    check("the same sentence every night",
          len({rv.plain("wear my retainers") for _ in range(7)}) == 1)
    check("a bare noun is the noun", rv.plain("supplements") == "Supplements, sir.", rv.plain("supplements"))
    check("anything else is 'time to' and his words",
          rv.plain("call the dentist") == "Time to call the dentist, sir.", rv.plain("call the dentist"))

    # --- the absurdities can no longer happen ------------------------------------
    for absurd in ("ingest", "lace up", "don your", "strap on", "slip on", "fasten", "pop your"):
        for text in ("take my supplements", "wear my retainers"):
            check(f"never {absurd!r}", absurd not in rv.plain(text).lower(), rv.plain(text))
    src = inspect.getsource(rv)
    check("no model is anywhere near a reminder",
          "local_llm" not in src and "llm.provider" not in src and "stream(" not in src
          and "random" not in src)
    check("...and the old LLM entry point is gone", not hasattr(rv, "phrase"))

    # --- every word he set is in it, with the pronouns turned round ---------------
    # He got "Time to wear MY retainers, sir." on 2026-09-02: JARVIS owning a
    # set of retainers. The sentence changes speaker, so the pronouns turn.
    for said, must_have, must_not in [
        ("wear my retainers", "your retainers", " my "),
        ("take my medication", "your medication", " my "),
        ("call my mother", "your mother", " my "),
        ("send myself the invoice", "yourself", "myself"),
        ("check on our order", "your order", " our "),
        ("remind me to take the bins out", "take the bins out", "remind me"),
    ]:
        out = rv.plain(said)
        check(f"{said!r} is said back with {must_have!r}",
              must_have in out.lower() and must_not not in f" {out.lower()} ", out)

    # --- never truncated, never empty, never raises -------------------------------
    for weird in ("", "   ", None, "remind me to " * 40, "wear my"):
        try:
            r = rv.plain(weird)
            ok = isinstance(r, str) and r.strip().endswith((".", "!")) and len(r) > 5
        except Exception as e:
            ok, r = False, f"RAISED {e}"
        check(f"a whole sentence for {weird!r:.30}", ok, r)
    check("'put on your.' cannot be produced",
          not rv.plain("wear my retainers").rstrip(".").endswith("your"))

    # --- the one nudge is his words too -----------------------------------------------
    check("the nudge says what is still to do",
          rv.nudge("take my supplements") == "Still to do, sir: take your supplements.",
          rv.nudge("take my supplements"))

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
