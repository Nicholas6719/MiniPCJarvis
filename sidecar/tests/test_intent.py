"""What he SAYS, against what JARVIS understands. Not seed phrases.

His complaint, 2026-09-03, and it is the whole reason this file exists:

    "he's not understanding my logic ... if I don't say exactly what's in the
    script that we're writing then he can't do it ... he won't even ask for
    clarification, he'll just do the wrong thing ... it feels like I'm working
    harder than he is."

Testing a parser with its own seed phrases proves nothing - it proves the seeds
are spelled correctly. Every sentence below is one a person would really say,
deliberately NOT copied from the vocabulary, and several are his own words.

Measured before the fix: 17 of 47 understood, 28 not understood at all, and 2
that did something else instead - "turn it off" SPUN THE MODEL and "close the
hologram" switched colour mode. There was no `hide` action at all, so "remove
render" could never have matched anything.

A wrong action is worse than an admitted miss and this file scores it that way:
a miss is a failure, a wrong action is a failure that also has to be undone
while he watches.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

fails: list[str] = []


def check(name: str, cond, detail: str = "") -> None:
    ok = bool(cond)
    if not ok:
        fails.append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   {detail}" if detail and not ok else ""))


# (what he says, what he plainly means) - none of these are seed phrases.
STAGE = [
    # putting it away: his own example was "remove render"
    ("remove render", "hide"), ("remove the render", "hide"),
    ("get rid of it", "hide"), ("close it", "hide"),
    ("close the hologram", "hide"), ("take it away", "hide"),
    ("hide it", "hide"), ("put it away", "hide"),
    ("i'm done with it", "hide"), ("clear the stage", "hide"),
    ("turn it off", "hide"), ("dismiss it", "hide"),
    ("get that off my screen", "hide"),
    # back to the start: his other example was "return home"
    ("return home", "reset"), ("go home", "reset"), ("home", "reset"),
    ("back to the start", "reset"), ("how it was at the beginning", "reset"),
    ("original position", "reset"), ("default view", "reset"),
    ("put it back", "reset"), ("undo that", "reset"),
    # turning it
    ("turn it around", "rotate"), ("show me the back", "rotate"),
    ("other side", "rotate"), ("let me see the back of it", "rotate"),
    ("spin it round", "rotate"), ("rotate ninety degrees", "rotate"),
    ("turn it a bit to the left", "rotate"),
    # size
    ("make it bigger", "scale"), ("zoom in", "scale"), ("closer", "scale"),
    ("too small", "scale"), ("blow it up", "scale"), ("shrink it", "scale"),
    ("too big", "scale"),
    # colour
    ("show it in colour", "colour"), ("what colour is it really", "colour"),
    ("make it look real", "colour"), ("back to the hologram", "hologram"),
    ("no colour please", "hologram"),
    # cutting it open
    ("cut it in half", "section"), ("show me inside", "section"),
    ("what's it like inside", "section"), ("slice it down the middle", "section"),
    # taking it apart
    ("take it apart", "explode"), ("show me the pieces", "explode"),
    ("separate it", "explode"),
    # framing
    ("i can't see all of it", "fit"), ("fit it on screen", "fit"),
    ("centre it", "fit"),
]


def main() -> int:
    import holo_angles

    print("-- the sentences he would really use --")
    misses, wrong = [], []
    for said, meant in STAGE:
        got = holo_angles.parse_action(said)
        if got == meant:
            continue
        (misses if got is None else wrong).append((said, meant, got))

    understood = len(STAGE) - len(misses) - len(wrong)
    print(f"  understood {understood}/{len(STAGE)}")

    # A WRONG ACTION IS THE UNFORGIVABLE ONE. He watches it do the thing he did
    # not ask for and then has to undo it.
    check("nothing does the WRONG thing",
          not wrong,
          "; ".join(f"{s!r} meant {m} -> {g}" for s, m, g in wrong))
    check("every sentence he would really say is understood",
          not misses,
          "; ".join(f"{s!r} ({m})" for s, m, _ in misses))

    print("\n-- the two that used to do something else entirely --")
    check("'turn it off' puts it away rather than SPINNING it",
          holo_angles.parse_action("turn it off") == "hide",
          str(holo_angles.parse_action("turn it off")))
    check("'close the hologram' closes it rather than switching colour",
          holo_angles.parse_action("close the hologram") == "hide",
          str(holo_angles.parse_action("close the hologram")))

    print("\n-- and 'too small' is a complaint, not a direction --")
    # Reading it as "smaller" gives him the exact opposite of what he asked for,
    # which is the worst possible answer to a complaint.
    check("'too small' makes it BIGGER",
          (holo_angles.parse_scale("too small") or 0) > 1,
          str(holo_angles.parse_scale("too small")))
    check("'too big' makes it smaller",
          0 < (holo_angles.parse_scale("too big") or 9) < 1,
          str(holo_angles.parse_scale("too big")))

    print("\n-- the one word that must STAY ambiguous --")
    # "Slice it" is a cross-section or the G-code slicer that prepares a real
    # print. Guessing either way is wrong; it belongs in the clarify flow.
    check("bare 'slice it' is not claimed, so it can be asked about",
          holo_angles.parse_action("slice it") is None,
          str(holo_angles.parse_action("slice it")))
    check("...but 'slice it down the middle' is not ambiguous at all",
          holo_angles.parse_action("slice it down the middle") == "section")

    print("\n-- and the control surface ACCEPTS what the parser returns --")
    # parse_action returning "hide" was useless while holo_control refused it:
    # the sentence was understood and then answered "I'm not sure what to do
    # with that, sir".
    import tools.holo_tools as ht
    produced = {holo_angles.parse_action(s) for s, _ in STAGE}
    produced.discard(None)
    unreachable = sorted(a for a in produced if a not in ht._ACTIONS)
    check("every action the parser can produce is one the stage accepts",
          not unreachable, f"unreachable: {unreachable}")

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
