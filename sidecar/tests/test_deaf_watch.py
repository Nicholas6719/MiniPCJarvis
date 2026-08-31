"""He must never be left unable to hear his own name.

On 2026-08-31 Nicholas woke his sleeping monitor with the wake word - the display
came on, JARVIS came back to the front - and then could not talk to him at all.
He was parked on the "NEEDS YOU" confirmation screen, holding a `press_keys`
question raised by one of MY e2e runs hours earlier, which nobody ever answered.

The wake word is only fed in IDLE, WAITING, STARTING and SLEEPING. Nine other
states exist, and anything that parks him in one of them leaves him awake, lit,
on screen and deaf.

This watchdog does not care WHY - the causes will keep differing. It cares that
"cannot hear" is never a place he stays.

Offline: no app, no audio, no network.
Run: python tests/test_deaf_watch.py
"""
import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "deaf.db"))

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


def main() -> int:
    from state_machine import State
    from orchestrator import orchestrator as orc

    HEARS = (State.IDLE, State.WAITING, State.STARTING, State.SLEEPING)

    # --- the states that cannot hear him are exactly the ones we think --------
    deaf = [s for s in State if s not in HEARS]
    check("there really are states in which he is deaf", len(deaf) >= 5,
          [s.name for s in deaf])
    check("...and EXECUTING is one of them", State.EXECUTING in deaf)
    check("...while WAITING is not - a question is answerable out loud",
          State.WAITING in HEARS)

    async def stuck_in(state, *, pending=False, patience=0.2):
        """Park him in `state` and let the watchdog run."""
        from tools.registry import registry
        real_pending = type(registry).has_pending
        if pending:
            type(registry).has_pending = property(lambda self: True)
        resolved = []
        real_resolve = registry.resolve_latest
        registry.resolve_latest = lambda approved: resolved.append(approved) or True
        orc.STUCK_AFTER_S = patience
        await orc.sm.to(state, force=True)
        task = asyncio.create_task(orc._deaf_watch())
        try:
            # the loop wakes every 15s; give it one tick plus the patience window
            await asyncio.sleep(16.5)
            return orc.sm.state, resolved
        finally:
            task.cancel()
            registry.resolve_latest = real_resolve
            if pending:
                type(registry).has_pending = real_pending

    # --- parked in a deaf state, he is brought back ---------------------------
    state, _ = asyncio.run(stuck_in(State.EXECUTING))
    check("stuck in EXECUTING, he is returned to IDLE", state == State.IDLE, state)

    state, _ = asyncio.run(stuck_in(State.PROCESSING))
    check("...and from PROCESSING too", state == State.IDLE, state)

    # --- an unanswered question is refused, not left hanging ------------------
    # This is the actual 2026-08-31 case: a question from a test that nobody was
    # left to answer. Silence means no, because refusing is always the safe read.
    state, resolved = asyncio.run(stuck_in(State.EXECUTING, pending=True))
    check("a forgotten confirmation is cleared", resolved == [False], resolved)
    check("...and he can hear again", state == State.IDLE, state)

    # --- and a state he CAN hear from is left completely alone ---------------
    async def left_alone(state):
        orc.STUCK_AFTER_S = 0.2
        await orc.sm.to(state, force=True)
        task = asyncio.create_task(orc._deaf_watch())
        try:
            await asyncio.sleep(16.5)
            return orc.sm.state
        finally:
            task.cancel()

    check("SLEEPING is his shift, not a fault", asyncio.run(left_alone(State.SLEEPING))
          == State.SLEEPING)
    check("WAITING for an answer is not being stuck",
          asyncio.run(left_alone(State.WAITING)) == State.WAITING)

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
