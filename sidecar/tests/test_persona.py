"""Answering his name: never the same word twice, and a greeting after time away.

Run: python tests/test_persona.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
