"""A typed "Do it!" is a yes, and it must not deadlock behind its own question.

From his phone, 2026-09-04:

    JARVIS: Before I do this — type text (medium risk). Proceed?  [DO IT] [NO]
    him:    Do it!
    him:    Did you do it
    JARVIS: I didn't get a yes, so I left it alone.
    JARVIS: Done, sir.
    JARVIS: Done, sir.

The buttons carried the confirm_id and were the only thing that could answer.
Typing the word instead queued a NEW turn, which waits for the state machine to
go idle — and it cannot, because a tool is blocked on the very confirmation he
just answered. So the question timed out at 120 s, he was told he had not
answered a minute after answering, and the two stray "Done, sir."s were the
turns his replies had started.

Tested here without touching Telegram: his phone is not a test fixture, and the
fault was never in the transport. It is in whether a typed yes reaches the
pending future at all.
"""
import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}{'' if ok else '  — ' + str(detail)[:120]}")
    if not ok:
        FAILURES.append(name)


async def run() -> None:
    from tools.registry import registry

    print("\nnothing pending, nothing to answer")
    check("it knows when no question is open", not registry.awaiting_confirmation())
    check("and answering nothing is harmless",
          registry.answer_pending_confirmation(True) is False)

    print("\na pending question is answered by a typed yes")
    fut: asyncio.Future = asyncio.get_running_loop().create_future()
    registry._pending["probe1"] = fut
    check("it reports a question is open", registry.awaiting_confirmation())
    check("the answer lands", registry.answer_pending_confirmation(True) is True)
    check("...as an approval", fut.done() and fut.result() is True)
    check("and the question is no longer open", not registry.awaiting_confirmation())

    print("\n...and by a typed no")
    fut2: asyncio.Future = asyncio.get_running_loop().create_future()
    registry._pending["probe2"] = fut2
    registry.answer_pending_confirmation(False)
    check("the refusal lands", fut2.done() and fut2.result() is False)
    registry._pending.clear()


def main() -> int:
    asyncio.run(run())

    print("\nthe words he actually typed count as yes")
    from orchestrator import NO_WORDS, YES_WORDS
    for text in ("Do it!", "do it", "yes", "Yes please", "go ahead", "confirm", "okay"):
        check(f"{text!r} is a yes", bool(YES_WORDS.match(text)))
    for text in ("no", "No thanks", "cancel", "stop", "never mind"):
        check(f"{text!r} is a no", bool(NO_WORDS.match(text)))
    for text in ("what time is it", "open notepad", "did you do it"):
        check(f"{text!r} is neither, and runs as its own turn",
              not YES_WORDS.match(text) and not NO_WORDS.match(text))

    print("\nTHE CHECK HAPPENS BEFORE THE TURN LOCK")
    # This is the whole bug. Taking the turn lock first is what made him wait
    # behind the deadlock, so the ordering is what is gated — not the wording.
    src = Path(__file__).resolve().parents[1].joinpath("remote_telegram.py") \
        .read_text(encoding="utf-8")
    guard = src.find("awaiting_confirmation()")
    lock = src.find("async with self._turn_lock")
    check("the confirmation guard is reached first",
          0 < guard < lock, f"guard at {guard}, lock at {lock}")
    check("and a yes resolves it rather than starting a turn",
          re.search(r"awaiting_confirmation\(\)[\s\S]{0,400}?"
                    r"answer_pending_confirmation\(True\)", src) is not None)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: " + ", ".join(FAILURES))
        return 1
    print("typed confirmations: all good")
    return 0


sys.exit(main())
