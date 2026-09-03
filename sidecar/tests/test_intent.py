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
import tempfile

# A BRAIN OF ITS OWN, and not the one the build shares. This suite calls
# brain.learn - proving that confirming a phrase teaches it - and a
# learned example is written to disk and stays there. Pointed at the
# gate database (build_sidecar.cmd sets JARVIS_DB for the whole run) the
# first pass taught "make me a duck" and every pass after it found the
# phrase already known, fired instead of asking, and failed. Forced
# rather than setdefault, because ignoring what the build set is the
# entire point.
os.environ["JARVIS_DB"] = os.path.join(tempfile.mkdtemp(), "intent.db")

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

    print("\n-- holding still is not cancelling the render --")
    # 2026-09-03, from his testing: the model drifts by default and there
    # was NO WAY TO SAY STOP, so "make it stop spinning" matched nothing on
    # the stage, fell through to the skill router, and hit render_stop - it
    # CANCELLED HIS RENDER. Same class as "turn it off" spinning the model:
    # a sentence whose meaning is in the whole phrase, taken by one word.
    for said in ("make it stop spinning", "stop spinning", "stop turning",
                 "hold still", "keep it still", "freeze it",
                 "stop it moving", "stop the spin"):
        check(f"{said!r} holds it steady",
              holo_angles.parse_action(said) == "still",
              str(holo_angles.parse_action(said)))
    for said in ("start it spinning", "let it spin", "turn it slowly"):
        check(f"{said!r} sets it drifting again",
              holo_angles.parse_action(said) == "spin",
              str(holo_angles.parse_action(said)))
    # And the two that must be UNTOUCHED: cancelling a render is a real
    # thing he asks for, and "spin it round" is a rotation.
    for said in ("stop the render", "cancel that render"):
        check(f"{said!r} is still not a stage control",
              holo_angles.parse_action(said) is None,
              str(holo_angles.parse_action(said)))
    for said in ("spin it round", "spin it around", "spin it"):
        check(f"{said!r} is a rotation, not the idle drift",
              holo_angles.parse_action(said) == "rotate",
              str(holo_angles.parse_action(said)))

    print("\n-- and the layer words are not the hide word --")
    # Adding `hide` and checking it FIRST claimed "hide the layers", which
    # means stop drawing the sliced toolpath - not close the hologram. The
    # build gate caught it. Specificity first: layer rules sit above hide.
    for said, want in (("hide the layers", "solid"),
                       ("stop the layers", "solid"),
                       ("back to the model", "solid"),
                       ("show me the layers", "layers"),
                       ("layer 50", "layer")):
        check(f"{said!r} is {want}, not hide",
              holo_angles.parse_action(said) == want,
              str(holo_angles.parse_action(said)))

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

    # ---------------------------------------------------------------- brain
    # These need the embedding matrix, so they are last and cost a few seconds.
    print("\n-- it asks when it nearly knows, instead of going quiet --")
    import asyncio

    async def brain_checks():
        from brain.router import brain
        from brain.skills import CONFIRM_AS, confirm_as
        from config import config
        await brain.load()
        hard = float(config.get("brain", "threshold", default=0.82))
        empty = {"stage": False, "project": False, "render": False}
        stage = {"stage": True, "project": False, "render": False}

        # "Make me a duck" ranked holo_make top at 0.68 and was BINNED, because
        # decide() answered None for "nearly knew" and "no idea" alike.
        got = await brain.decide("make me a duck", context=empty)
        u = brain.unsure
        check("a near miss is kept rather than dropped on the LLM",
              got is None and bool(u) and u["skill"] == "holo_make",
              f"decide={got and got[0].name} unsure={u and u.get('skill')}")
        check("...and it can be said out loud as English",
              confirm_as("holo_make") == "render that in 3D",
              confirm_as("holo_make"))

        # His confirmation is the lesson. The second time must not ask again.
        if u:
            await brain.learn("make me a duck", u["skill"], source="user")
        again = await brain.decide("make me a duck", context=empty)
        check("saying yes teaches it: the second time it just acts",
              bool(again) and again[0].name == "holo_make"
              and again[2] >= hard,
              f"{again and again[0].name}@{again and round(again[2], 2)}")

        # And it GENERALISES - the point of teaching a brain rather than a list.
        near = await brain.decide("make me a dragon", context=empty)
        check("...and a phrasing he never taught benefits too",
              bool(near) and near[0].name == "holo_make",
              f"{near and near[0].name}")

        print("\n-- and he remembers what was just being talked about --")
        # From his testing: "how many fingers am I holding up" -> five,
        # correct. "What about now?" two seconds later -> "did you mean the
        # TIME, sir?". _last_reflex already knew, and nothing consulted it.
        #
        # Measured: "what about now" gives time@0.85 sleep@0.81 look_at@0.80
        # date@0.79 — four skills inside 0.06, which is noise rather than a
        # match. So the pull is decided by CONTENT: strip the reference, and
        # if nothing is left the previous subject is the only one on offer.
        warm = {**empty, "last_skill": "fingers"}
        for said in ("what about now", "how about now", "and now",
                     "again", "the second one"):
            r = await brain.decide(said, context=warm)
            check(f"{said!r} stays on the last subject",
                  bool(r) and r[0].name == "fingers",
                  str(r and r[0].name))
        # ...and a sentence WITH a subject of its own is never dragged.
        # The requirement is that it is NOT dragged onto the last subject.
        # Whether it fires or asks is a separate question - asking is fine.
        for said in ("what about the weather tomorrow", "what about my calendar",
                     "how about some music"):
            r = await brain.decide(said, context=warm)
            landed = r[0].name if r else (brain.unsure or {}).get("skill")
            check(f"{said!r} keeps its own subject",
                  landed != "fingers", str(landed))
        check("a cold start has nothing to follow up",
              (await brain.decide("what about now", context=empty)) is not None
              or brain.unsure is not None,
              "no last skill means it decides on the words alone")

        print("\n-- the screen decides what an ambiguous sentence means --")
        a = await brain.decide("make it bigger", context=stage)
        b = await brain.decide("make it bigger", context=empty)
        check("'make it bigger' moves the MODEL when one is on the stage",
              bool(a) and a[0].name == "holo_move", f"{a and a[0].name}")
        check("...and the INTERFACE when the stage is empty",
              bool(b) and b[0].name == "ui", f"{b and b[0].name}")

        # The one that must never be guessed at: an edit rewrites the source and
        # re-renders a part he may be about to print.
        c = await brain.decide("make his eyes smaller", context=stage)
        check("naming a feature is an edit, not a view change",
              bool(c) and c[0].name == "holo_edit", f"{c and c[0].name}")
        check("...but a bare 'make it bigger' never becomes one",
              not (a and a[0].name == "holo_edit"),
              "an edit is not undone by looking away")

        check("every skill it can offer to guess has a spoken name",
              all(confirm_as(k) != k.replace("_", " ") for k in CONFIRM_AS),
              "'did you mean holo make, sir?' is not English")

    asyncio.run(brain_checks())

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
