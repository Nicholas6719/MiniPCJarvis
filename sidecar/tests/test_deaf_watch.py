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

    # --- a muted microphone is said, once per episode --------------------------
    # The endpoint keeps delivering frames - of zeros - so "capturing" and "frames
    # arriving" both read healthy while the wake word never fires. The endpoint's
    # mute flag is read on its own COM thread; here it is a stub, and the line
    # is judged by where it LANDS: delivery.deliver, the normal spoken path.
    print("\n-- a muted microphone is said once --")
    import time as _time
    import delivery as _dmod
    from audio import capture_mute
    from audio.io import mic, speaker

    said = []
    real_deliver = _dmod.delivery.deliver
    real_present = _dmod.is_present
    real_read = capture_mute.capture_muted
    real_stream, real_frame, real_sound = mic._stream, mic.last_frame_at, mic.last_sound_at
    real_silent = speaker.silent_until

    async def fake_deliver(text, tier="notable", **kw):
        said.append((text, tier, kw.get("key")))
        return {"delivered": "spoken", "tier": tier}

    reads = []

    def read_from(answer):
        def stub(device_name=None, timeout=0):
            reads.append(device_name)
            return answer
        capture_mute.capture_muted = stub

    async def tick(answer, *, silent_for=0.0, test_mute=False):
        read_from(answer)
        now = _time.time()
        mic._stream = object()                       # "a stream is open"
        mic.last_frame_at = now                      # frames keep arriving
        mic.last_sound_at = now - silent_for
        speaker.silent_until = now + 60 if test_mute else 0.0
        orc._mute_last_read = 0.0                    # the poll is due
        return await orc._mute_check()

    _dmod.delivery.deliver = fake_deliver
    _dmod.is_present = lambda: True
    orc._mute_said = False
    try:
        spoke = asyncio.run(tick(True))
        check("the endpoint says muted: he is told", spoke and len(said) == 1, said)
        check("...in the persona's words",
              said and said[0][0] == "Your microphone is muted at the system level, sir.", said)
        check("...as an ALERT through delivery, keyed for dedup",
              said and said[0][1] == _dmod.ALERT and said[0][2] == "mic-muted", said)
        check("the read asked about the device we listen on", reads and reads[-1] == mic.device_name, reads)

        spoke = asyncio.run(tick(True))
        check("still muted a minute later: not said again", not spoke and len(said) == 1, said)

        spoke = asyncio.run(tick(False))
        check("unmuted: nothing said, the episode is over", not spoke and len(said) == 1, said)

        spoke = asyncio.run(tick(True))
        check("muted again: a new episode, said once more", spoke and len(said) == 2, said)

        n = len(said)
        spoke = asyncio.run(tick(True, test_mute=True))
        check("under the test mute (/debug/silence) it never fires", not spoke and len(said) == n, said)
        check("...and the endpoint was not even asked", len(reads) == 4, reads)

        # The read cannot tell (no pycaw, a device mid re-enumeration): a minute
        # of exact digital silence with frames flowing is the same verdict.
        orc._mute_said = False
        n = len(said)
        spoke = asyncio.run(tick(None, silent_for=10))
        check("unreadable and sound was recent: nothing", not spoke and len(said) == n, said)
        spoke = asyncio.run(tick(None, silent_for=90))
        check("unreadable and 90 s of zeros: said", spoke and len(said) == n + 1, said)
        spoke = asyncio.run(tick(None, silent_for=120))
        check("...once", not spoke and len(said) == n + 1, said)

        # Nobody at the machine: the line waits for him rather than going to his phone.
        orc._mute_said = False
        _dmod.is_present = lambda: False
        n = len(said)
        spoke = asyncio.run(tick(True))
        check("not present: held for when he is here", not spoke and len(said) == n, said)
        _dmod.is_present = lambda: True
        spoke = asyncio.run(tick(True))
        check("...and said the moment he is", spoke and len(said) == n + 1, said)
    finally:
        _dmod.delivery.deliver = real_deliver
        _dmod.is_present = real_present
        capture_mute.capture_muted = real_read
        mic._stream, mic.last_frame_at, mic.last_sound_at = real_stream, real_frame, real_sound
        speaker.silent_until = real_silent
        orc._mute_said = False

    # the frame path keeps the clock honest: zeros do not count as sound
    import numpy as np
    t0 = mic.last_sound_at = 1.0
    mic._put(np.zeros(1024, dtype=np.float32))
    check("a block of zeros is not sound", mic.last_sound_at == t0)
    mic._put(np.full(1024, 0.01, dtype=np.float32))
    check("a block with signal is", mic.last_sound_at > t0)

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
