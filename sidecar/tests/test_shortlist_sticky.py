"""The tool shortlist must be a PREFIX of itself from one turn to the next.

llama.cpp caches the prompt as a prefix. The tools block sits before the
history, so any change in it — one tool swapped for another, or the same tools
in a different order — throws away the cache from that point and re-processes
history and all: measured on the real log, ~800 tokens and ~3.3 s before the
first token on an ordinary turn, against ~100 tokens when the prefix holds.

So the order is by first appearance in the session, and a tool once offered
stays offered: the previous block is a literal prefix of the next, and the
model only ever reads the tools that are NEW. A cap keeps it from growing
without bound; past the cap the least recently wanted go, and that one turn
pays the re-read.

Offline; pure ordering logic, no embeddings. Run: python tests/test_shortlist_sticky.py
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
    from tools.shortlist import ToolShortlist

    s = ToolShortlist()
    a = s.stable_order({"web_search", "recall", "lock_pc"})
    check("first turn: some order", sorted(a) == ["lock_pc", "recall", "web_search"], a)
    b = s.stable_order({"recall", "web_search", "open_application"})
    check("second turn: the first block is a prefix", b[:len(a)] == a, (a, b))
    check("...and the new tool is appended", b[-1] == "open_application", b)
    check("...nothing wanted is missing", "open_application" in b and "recall" in b)
    c = s.stable_order({"recall"})
    check("a tool not wanted this turn STAYS (that is the point)", c == b, (b, c))
    d = s.stable_order(set(b) | {"take_screenshot"})
    check("many turns later the prefix still holds", d[:len(c)] == c and d[-1] == "take_screenshot")

    print("\n-- the cap --")
    s = ToolShortlist()
    s.MAX_STICKY = 6
    first = s.stable_order({"t1", "t2", "t3", "t4"})
    s.stable_order({"t5", "t6"})            # t1-t4 now older than t5-t6
    over = s.stable_order({"t7", "t8"})     # 8 > 6: the least recently wanted go
    check("never more than the cap", len(over) <= 6, over)
    check("the newest are kept", "t7" in over and "t8" in over, over)
    evicted = {"t1", "t2", "t3", "t4", "t5", "t6", "t7", "t8"} - set(over)
    check("only the least recently wanted went", evicted <= {"t1", "t2", "t3", "t4"}, over)
    check("...from the END of the block among equals, so the prefix survives",
          over[:2] == ["t1", "t2"], over)
    check("...and the survivors keep their order",
          [t for t in over if t in first] == [t for t in first if t in over], over)
    again = s.stable_order({"t8"})
    check("after an eviction the block is stable again", again == over, (over, again))

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
