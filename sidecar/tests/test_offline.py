"""What he is told when there is no internet.

He tried JARVIS with no connection and said "it really did not work". He was on
a half-installed build, so that is not the whole story — but looking into it
found a real gap underneath: JARVIS had no idea whether it was online.

`State.OFFLINE` exists and does NOT mean this. It is the state before boot; its
only transition is OFFLINE -> STARTING.

So with no connection every networked tool failed on its own and the model
improvised a different explanation for each. Worst of all, an empty search
result is indistinguishable from "nothing was found" — so it could answer from
memory as though it had actually looked, which is the one thing it must never
do. He could not tell an unplugged router from a broken assistant.

These gate the honest sentence, and the two properties that make it honest:
it is FAST (no waiting out timeouts to be told the obvious), and it never
claims to have looked.
"""
import asyncio
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["JARVIS_DB"] = os.path.join(tempfile.mkdtemp(), "offline.db")

fails: list[str] = []


def check(name: str, cond, detail: str = "") -> None:
    ok = bool(cond)
    if not ok:
        fails.append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   {detail}" if detail and not ok else ""))


async def main() -> int:
    import netcheck

    print("-- the check itself --")
    t0 = time.time()
    up = netcheck.online()
    first = time.time() - t0
    check("it answers quickly", first < 2.0, f"{first:.2f}s")
    t0 = time.time()
    netcheck.online()
    check("...and instantly once cached", (time.time() - t0) < 0.05)
    check("a live network produces no complaint",
          netcheck.note() == "" if up else True,
          "note should be empty while online")

    # From here on, pretend the network is gone the way it really goes: the
    # probes fail. Restored afterwards so nothing else in the run is affected.
    real = netcheck.online
    netcheck.online = lambda force=False: False
    try:
        print("\n-- a render, with no connection --")
        from tools.render_tools import make_hologram
        t0 = time.time()
        r = await make_hologram(description="a duck", name="offline-duck")
        took = time.time() - t0
        # HIS DUCK TOOK THREE MINUTES and said it was almost done the whole way.
        # Spending that to arrive at "I couldn't" would be the same lesson
        # unlearned, so this has to be immediate.
        check("it says so immediately rather than failing slowly",
              took < 5.0, f"{took:.1f}s")
        check("...and marks it as a network problem", r.get("offline") is True)
        said = (r.get("spoken") or r.get("error") or "").lower()
        check("...and says which, in his words",
              "internet" in said, said[:80])
        check("...and offers the way that still works",
              "picture" in said, said[:80])

        print("\n-- a search, with no connection --")
        import tools.builtin as B
        from tools.builtin import web_search
        orig = B._web_search

        async def empty(q, c=5):
            return {}

        B._web_search = empty
        try:
            s = await web_search("who won the game")
        finally:
            B._web_search = orig
        check("an empty result offline is reported as a network problem",
              s.get("offline") is True, str(s)[:80])
        # THE IMPORTANT ONE. "Nothing found" and "I could not look" are
        # different answers, and only one of them permits answering from
        # memory. Conflating them is how a confident wrong answer happens.
        note = (s.get("note") or "").lower()
        check("...and the model is told NOT to answer from memory",
              "memory" in note and "did not happen" in note, note[:100])
    finally:
        netcheck.online = real

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
