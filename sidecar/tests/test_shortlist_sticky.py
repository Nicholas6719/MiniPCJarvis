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
    # The block's VERSION is what the orchestrator re-warms slot 0 against: it
    # moves only when the block changes shape, never on an ordinary turn.
    v = s.block_version
    s.stable_order({"recall"})
    check("a turn that adds nothing leaves the version alone", s.block_version == v, (v, s.block_version))
    s.stable_order({"recall", "get_weather"})
    check("...and a new tool bumps it", s.block_version == v + 1, (v, s.block_version))
    from tools.registry import registry
    check("the current block is the sticky order", [t["function"]["name"] for t in s.current_block(registry)]
          == [n for n in s._sticky if n in registry._tools])

    print("\n-- the default never evicts --")
    # Release 18 trimmed once a minute at 72/48 and every trim was a 15-20 s
    # re-read. The whole registry is ~8.5k tokens once; no cut is worth it.
    s = ToolShortlist()
    check("the default cap is beyond any registry", s.MAX_STICKY >= 1000, s.MAX_STICKY)
    for i in range(1, 200):
        block = s.stable_order({f"tool{i}", f"tool{i + 1}"})
    check("two hundred distinct picks and nothing was dropped", len(block) == 200, len(block))

    print("\n-- the cap (kept as a mechanism) --")
    s = ToolShortlist()
    s.MAX_STICKY = 6
    s.LOW_WATER = 6
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

    print("\n-- hysteresis: one cut, then quiet --")
    # Without a low-water mark the block sat AT the cap and every new question
    # evicted again: the prefix broke on nearly every turn (measured: 4,506
    # tokens re-read on the turn after two cache hits).
    s = ToolShortlist()
    s.MAX_STICKY = 10
    s.LOW_WATER = 6
    for i in range(1, 11):
        s.stable_order({f"t{i}"})
    cut = s.stable_order({"t11"})            # 11 > 10: one cut, down to 6 (+ wanted)
    check("cut down to the low-water mark", len(cut) <= 7, cut)
    check("...keeping what this turn asked for", "t11" in cut, cut)
    n = len(cut)
    steady = [s.stable_order({f"u{k}"}) for k in range(1, 4)]
    check("the next few turns only append", all(len(b) == n + k for k, b in enumerate(steady, 1))
          and all(b[:n] == cut for b in steady), [len(b) for b in steady])

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
