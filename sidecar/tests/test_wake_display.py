"""Saying his name to a dark screen should turn the screen on.

His setup: the monitor blanks after 60 seconds on mains power (his own
power plan, read from Windows - he believed it was five minutes), the PC never
sleeps. So the
wake-word detector really is listening the whole time - it is explicitly fed
while SLEEPING, which is the point of sleep mode - and he can say the name to a
dark room and be heard. What he could not do was SEE the answer: JARVIS restored
his window to the front of a monitor that was still off.

What is checked here is the wiring, not the pixels: that coming back from sleep
asks for the display, that it can be turned off, and that a failure to wake the
screen never costs him the window. Whether the panel actually lights is a
hardware question and he is the one who can answer it.

Run: python tests/test_wake_display.py
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
    import tools.windows_tools as wt

    # --- it uses both mechanisms, because neither is reliable alone ----------
    got = wt.wake_display()
    check("the display wake reports what it did", isinstance(got, dict), got)
    check("...it asks Windows for the display",
          "display-required" in (got.get("how") or []), got)
    check("...and sends real input, which is what lights a blank panel",
          "input-nudge" in (got.get("how") or []), got)

    # --- ONLY when the screen is actually dark -------------------------------
    # His instruction, and the right one: a synthetic input event sent to a
    # monitor that is already on is a liberty taken with a machine he may be
    # using. Windows has no plain "is the panel lit" call, so this is inferred
    # from his own power plan plus how long since he last touched the machine.
    blank_after = wt.monitor_blank_after()
    check("it reads the real power plan, not a guessed number",
          isinstance(blank_after, int) and blank_after >= 0, blank_after)

    import delivery as _d
    real_idle = _d.user_idle_seconds

    def with_idle(seconds, timeout=60):
        _d.user_idle_seconds = lambda: seconds
        real_blank = wt.monitor_blank_after
        wt.monitor_blank_after = lambda: timeout
        try:
            return wt.display_is_off()
        finally:
            wt.monitor_blank_after = real_blank

    try:
        check("idle past the blank timeout means the screen is off",
              with_idle(75, timeout=60))
        check("...just short of it means it is still on",
              not with_idle(45, timeout=60))
        check("...and freshly touched is certainly on", not with_idle(1, timeout=60))
        check("a plan set to never blank has nothing to wake",
              not with_idle(99999, timeout=0))
    finally:
        _d.user_idle_seconds = real_idle

    # --- coming back from sleep asks for the screen --------------------------
    called = []
    real_wake, real_windows = wt.wake_display, wt._our_windows
    wt.wake_display = lambda: called.append("woke") or {"woke_display": True}
    wt._our_windows = lambda: []          # no window work in a test
    real_off = wt.display_is_off
    wt.display_is_off = lambda: True      # pretend the panel is dark
    try:
        wt.exit_sleep_mode()
        check("waking from sleep wakes the screen", called == ["woke"], called)

        # --- and NOT when the screen is already on ---------------------------
        called.clear()
        wt.display_is_off = lambda: False
        wt.exit_sleep_mode()
        check("a screen that is already on is left alone", called == [], called)
        wt.display_is_off = lambda: True

        # --- and he can turn it off ------------------------------------------
        called.clear()
        real_cfg = wt.config.get
        wt.config.get = lambda *a, **k: (False if a[:2] == ("presence", "wake_display")
                                         else real_cfg(*a, **k))
        wt.exit_sleep_mode()
        check("...unless he has switched it off", called == [], called)
        wt.config.get = real_cfg

        # --- a screen that will not wake must not cost him the window --------
        called.clear()

        def explode():
            raise OSError("no display driver")
        wt.wake_display = explode
        try:
            out = wt.exit_sleep_mode()
            check("a failed screen wake still restores the window",
                  isinstance(out, dict) and "restored" in out, out)
        except Exception as e:
            check("a failed screen wake still restores the window", False,
                  f"{type(e).__name__}: {e}")
    finally:
        wt.wake_display, wt._our_windows = real_wake, real_windows
        wt.display_is_off = real_off

    # --- but a message from his PHONE must not light up the room ------------
    # He found this one: messaged JARVIS on Telegram at night with the monitor
    # off, and the PC's screen came on. Waking the STATE MACHINE and waking the
    # MACHINE are two different things, and the remote path only needs the first
    # — the turn path runs only from IDLE, so it must leave SLEEPING, but it has
    # no business raising a window in a room he is not in.
    import asyncio
    import inspect

    import orchestrator as orch_mod
    from state_machine import State

    src = inspect.getsource(orch_mod.Orchestrator.wake_if_sleeping)
    check("waking can be asked NOT to surface", "surface" in src, src[:120])

    import remote_telegram
    tg = inspect.getsource(remote_telegram.TelegramBridge._remote_turn)
    check("...and the Telegram path asks for exactly that",
          "surface=False" in tg,
          "a message from his phone would otherwise wake the monitor")

    # And the wiring, not just the spelling: a non-surfacing wake must reach
    # IDLE without calling the thing that touches the screen.
    o = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
    touched = []

    class FakeSM:
        state = State.SLEEPING

        async def to(self, s, force=False):
            FakeSM.state = s

    o.sm = FakeSM()

    async def boom():
        touched.append("surfaced")

    o._surface = boom
    o._wake_from_sleep = boom
    woke = asyncio.run(o.wake_if_sleeping(surface=False))
    check("a remote wake still leaves the sleeping state", woke is True)
    check("...reaching IDLE, so the turn can actually run",
          FakeSM.state == State.IDLE, FakeSM.state)
    check("...and NOTHING touched the screen or the window", not touched, touched)

    FakeSM.state = State.SLEEPING
    asyncio.run(o.wake_if_sleeping())
    check("while a wake at the machine still does come to the front",
          touched == ["surfaced"], touched)

    # --- being spoken to opens a window to answer in ------------------------
    # He asked for a plate, it was made, JARVIS said so — and then he said
    # "thank you" and "rotate it" and got nothing, because the follow-up window
    # is only armed at the end of a turn HE started. A render finishing, a
    # reminder, an alert: those are all JARVIS talking to HIM, and being spoken
    # to and then having to say the name again is not how being spoken to works.
    # He reasonably concluded it had stopped listening.
    ann = inspect.getsource(orch_mod.Orchestrator.announce)
    check("speaking unprompted arms the follow-up window",
          "_arm_conversation" in ann, "he must be able to just answer")
    check("...and counts him as present, so it does not sleep mid-conversation",
          "_last_active" in ann)

    # --- and sleep clears the stage ----------------------------------------
    # A hologram deliberately HOLDS the frame while he is working, but sleep is
    # the resting state; he watched a finished part sit projected for half an
    # hour with no way to talk to it.
    # --- speaking into a dead speaker must not open the microphone ----------
    # The arming above shipped unconditional and cost him within the hour: the
    # output device stalled (his monitor's speakers, asleep), JARVIS spoke into
    # nothing, and the window opened regardless. He heard silence, said something
    # that was not addressed to JARVIS, and "Two video." became a YouTube video
    # playing. If he cannot hear it there is nothing to reply to, and an open
    # microphone is worse than a missed follow-up.
    check("the window only opens if the speech was actually heard",
          "if heard" in ann and "heard = True" in ann, ann[-900:])

    # --- and not everything the microphone hears is a command ---------------
    # A wake word fired at 0.82 on him talking to someone else; the model put
    # JARVIS to sleep; self-training learned that sentence AS the sleep command.
    from orchestrator import _teachable
    for bad in ("I was like this is the challenge? Wait, I need to go on easier.",
                "um yeah I think so maybe we should try that later on",
                "so anyway. what were we saying.",
                "never ever leave gaps issues or bugs, and always tell me",
                "show me", "what about now", "show me that again"):
        check(f"refuses to learn {bad[:34]!r}", not _teachable(bad))
    # ...and a two-word command with an OBJECT in it still teaches. A minimum
    # word count was the first attempt and rejected "open spotify".
    for good in ("go to sleep", "open spotify", "show me the layers",
                 "make me a 20 mm cube", "rotate it ninety degrees",
                 "what is the cpu at"):
        check(f"still learns {good!r}", _teachable(good))

    # --- a barge-in must survive the turn it interrupted --------------------
    # He cut in, JARVIS stopped talking, and then nothing happened: he had to
    # wait about five seconds and say the wake word again. Barge-in moves
    # straight to LISTENING and arms the capture — and then the interrupted turn
    # carried on unwinding and called `to(IDLE, force=True)`, wiping it
    # milliseconds later. A finished turn may not put the state back when a
    # newer one has already taken it.
    from orchestrator import _NEXT_TURN_STATES
    check("listening counts as a newer turn in progress",
          State.LISTENING in _NEXT_TURN_STATES)
    check("...and so does processing", State.PROCESSING in _NEXT_TURN_STATES)
    check("...but idle does not, or a turn could never end",
          State.IDLE not in _NEXT_TURN_STATES)
    for meth in ("_finish_reflex", "_ask_clarification"):
        src_m = inspect.getsource(getattr(orch_mod.Orchestrator, meth))
        check(f"{meth} checks before forcing idle",
              "_NEXT_TURN_STATES" in src_m,
              "the interrupted turn stomps the barge-in that replaced it")

    check("going to sleep takes the hologram down",
          "hide_hologram" in inspect.getsource(orch_mod),
          "a part left projected through sleep is just stuck")

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
