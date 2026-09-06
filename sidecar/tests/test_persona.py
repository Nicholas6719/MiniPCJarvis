"""Answering his name: never the same word twice, and a greeting after time away.

Run: python tests/test_persona.py
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# the ledger is persisted now: this gate must write to its own database
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "persona.db"))

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


def main() -> int:
    from brain import persona as P

    print("\n-- a bare wake word --")
    check("the first answer is the familiar one", P.wake_ack(30, 14) == "Yes?")
    seq = []
    last = None
    for _ in range(8):
        last = P.wake_ack(30, 14, last)
        seq.append(last)
    check("never the same line twice in a row",
          all(a != b for a, b in zip(seq, seq[1:])), seq)
    check("...and it cycles through all of them", set(seq) == set(P.WAKE_ACKS), seq)
    check("every line is short", all(len(s) <= 20 for s in P.WAKE_ACKS))

    print("\n-- after time away --")
    check("morning", P.wake_ack(8 * 3600, 7) == "Good morning, sir.")
    check("afternoon", P.wake_ack(8 * 3600, 13) == "Good afternoon, sir.")
    check("evening", P.wake_ack(8 * 3600, 21) == "Good evening, sir.")
    check("small hours count as evening", P.wake_ack(8 * 3600, 2) == "Good evening, sir.")
    check("the first wake of a session greets", P.wake_ack(float("inf"), 9) == "Good morning, sir.")
    check("five hours is not away", P.wake_ack(5 * 3600, 9) == "Yes?")

    print("\n-- while you were away --")
    check("nothing happened: no briefing", P.briefing([]) == "")
    one = [{"ts": 1, "outcome": "telegram", "subject": "the market brief", "text": "..."}]
    b = P.briefing(one)
    check("one message: says so, names it", b.startswith("While you were away: One thing reached you")
          and "the market brief" in b, b)
    many = one + [{"ts": 2, "outcome": "spoken", "subject": "", "text": "Your dentist is at 4 tomorrow."},
                  {"ts": 3, "outcome": "held", "subject": "cpu", "text": "..."},
                  {"ts": 4, "outcome": "held for the next brief", "subject": "", "text": "Rain later today."}]
    b = P.briefing(many)
    check("counts, then subjects", "2 things reached you" in b and "2 things I held back" in b, b)
    check("a task without a subject is named by its text",
          "Your dentist is at 4 tomorrow" in b, b)
    check("...and never the whole mail", len(b) < 220, len(b))
    check("nothing-outcomes are not reported",
          P.briefing([{"ts": 1, "outcome": "nothing", "why": "empty"}]) == "")
    secs = P.briefing_sections(many)
    check("the screen gets the same ledger as sections",
          [s["title"] for s in secs] == ["Reached you", "Held back"], secs)
    check("...a subject and its text on one line",
          secs[0]["lines"][0] == "the market brief" and "Your dentist is at 4 tomorrow" in secs[0]["lines"][1],
          secs[0]["lines"])
    check("...and nothing when nothing happened", P.briefing_sections([]) == [])
    check("the greeting carries it after time away",
          P.wake_line(8 * 3600, 9, None, one).startswith("Good morning, sir. While you were away"),
          P.wake_line(8 * 3600, 9, None, one))
    check("...but not on an ordinary wake", P.wake_line(30, 9, None, one) == "Yes?")

    print("\n-- a follow-up never repeats the last answer --")
    # Measured live 2026-09-05: "and Chile?" -> "Lima. Santiago."; "and when was
    # it published?" -> "Herman Melville, 1851, sir." A prompt rule changed
    # nothing; this is handled on the way to the speaker.
    from brain.skills import strip_repeat
    check("a first sentence that IS the last answer is flagged",
          strip_repeat("Lima.", "Lima.") == ("Lima.", True))
    check("...however it was punctuated", strip_repeat("Lima, sir.", "Lima.")[1] is True)
    check("a leading repeat with a comma is cut off",
          strip_repeat("Herman Melville, 1851, sir.", "Herman Melville.") == ("1851, sir.", False),
          strip_repeat("Herman Melville, 1851, sir.", "Herman Melville."))
    check("a real answer is left alone",
          strip_repeat("Santiago.", "Lima.") == ("Santiago.", False))
    check("an answer that merely starts with the same word is left alone",
          strip_repeat("Lima is in Peru.", "Lima.") == ("Lima is in Peru.", False))
    check("no previous answer, nothing to strip", strip_repeat("Lima.", "") == ("Lima.", False))

    print("\n-- a question about the disk is answered with the disk --")
    from brain.skills import say_stats, slots_stats
    res = {"disk_c_free_gb": 1700.0, "cpu_percent": 18.0, "ram_percent": 81.0}
    check("disk", say_stats(slots_stats("how much disk space is left"), res) == "About 1.7 terabytes free on the C drive.")
    check("memory", say_stats(slots_stats("how much ram am i using"), res) == "Memory is at 81 percent.")
    check("cpu", say_stats(slots_stats("what's the cpu at"), res) == "CPU is at 18 percent.")
    check("the whole picture when nothing is named",
          say_stats(slots_stats("how's the system doing"), res).startswith("CPU is at 18 percent, memory at 81 percent"))

    print("\n-- the ledger fills --")
    import asyncio
    import delivery as dv
    d = dv.Delivery()
    real_present = dv.is_present
    dv.is_present = lambda: True

    async def spoke(text, interrupt):
        return True
    d._speak = spoke
    try:
        asyncio.run(d.deliver("The disk is nearly full, sir.", dv.ALERT, key="disk", subject="disk space"))
        asyncio.run(d.deliver("", dv.ALERT))
    finally:
        dv.is_present = real_present
    # The ledger is persisted and loads the last day on first use, so in the
    # build's shared gate database it may already hold other gates' rows:
    # judge by what THIS gate added, never by the total.
    check("a spoken alert is in the ledger",
          d.ledger and d.ledger[-1]["outcome"] == "spoken" and d.ledger[-1]["subject"] == "disk space",
          d.ledger[-1:])
    n_before = len(d.ledger)
    asyncio.run(d.deliver("", dv.ALERT))
    check("...and an empty message is not", len(d.ledger) == n_before, (n_before, len(d.ledger)))
    # ...and it survives a restart: a fresh Delivery (what a release makes)
    # still knows what was said, because the ledger is on disk now
    fresh = dv.Delivery()
    seen = fresh.entries(time.time() - 60)
    check("a new process still knows what was said before it",
          any(e.get("subject") == "disk space" and e.get("outcome") == "spoken" for e in seen), seen)
    check("...and nothing older than asked for", fresh.entries(time.time() + 60) == [])

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
