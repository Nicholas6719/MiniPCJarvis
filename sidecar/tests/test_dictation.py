"""Dictation is text, not conversation. Offline: no mic, no clipboard, no typing.
Run: python tests/test_dictation.py"""
import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "gate.db"))
from dictation import Dictation, clean_for_text  # noqa: E402
from state_machine import State  # noqa: E402

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


class FakeSM:
    def __init__(self, state):
        self.state = state


class FakeOrch:
    def __init__(self, state):
        self.sm = FakeSM(state)


def main() -> int:
    # --- spoken words become written text -----------------------------------
    check("spoken punctuation lands",
          clean_for_text("send it to bob comma then call me period")
          == "send it to bob, then call me.")
    check("new paragraph is a real break",
          clean_for_text("one new paragraph two") == "one\n\ntwo")
    check("no stray spaces around a break",
          "\n " not in clean_for_text("one new line two"))
    check("fillers are dropped", clean_for_text("um so it works") == "so it works")
    check("no space before punctuation",
          clean_for_text("hello   world  period") == "hello world.")
    check("empty stays empty", clean_for_text("   ") == "")
    check("plain speech is left alone",
          clean_for_text("the quarterly numbers look strong")
          == "the quarterly numbers look strong")

    # --- it must never fight a real turn for the microphone ------------------
    d = Dictation()
    for state in (State.LISTENING, State.SPEAKING, State.THINKING, State.EXECUTING):
        d.orchestrator = FakeOrch(state)
        r = asyncio.run(d.start())
        check(f"refuses while {state.value}", r.get("ok") is False and not d.active, r)

    # --- stop without start is an error, not a crash -------------------------
    d2 = Dictation()
    d2.orchestrator = FakeOrch(State.IDLE)
    check("stop before start is refused", asyncio.run(d2.stop()).get("ok") is False)
    check("nothing is left recording", d2.active is False)

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
