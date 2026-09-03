"""Phase E: hands on the hologram, gated against synthetic landmark sequences.

No camera, no model — the same approach `test_hands.py` takes to finger counting,
because a gesture recogniser is a pure function of landmark positions and testing
it any other way makes it untestable.

WHAT MATTERS MOST HERE IS NOT THAT GESTURES WORK. It is the three fatigue rules,
because they are the ones that will be quietly eroded by a later change:

  * HANDS ARE NEVER REQUIRED — every gesture emits exactly the payload a spoken
    command emits, so `holo_control` remains the single control surface. If these
    ever diverge, hands become a second way to do things that words cannot.
  * SUPPORTED POSTURES — a rotation is a few centimetres of travel with the elbow
    bent, not an arm outstretched sweeping the screen. That is ROTATE_GAIN, and
    it is asserted rather than left to drift.
  * SHORT ENGAGEMENTS — tracking arms on a pinch and releases on an open palm or
    a hand leaving frame. The resting state is hands down, and the tracker never
    idles waiting for him to hold a pose.

And the mirror, which is the classic way to get this subtly wrong: he moves his
hand right, the raw pixel moves left, and without the flip the model turns the
wrong way — which reads as broken rather than reversed.

Run: python tests/test_hand_control.py
"""
import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "hands.db"))

import hand_gestures as G  # noqa: E402

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


def hand(px=0.5, py=0.5, pinch=True, spread=False, side="right"):
    """A synthetic hand: 21 landmarks, only the meaningful ones placed.

    Wrist to middle knuckle is 0.2, so the pinch threshold — a fraction of hand
    size — has something real to divide by.
    """
    lm = [(0.5, 0.9, 0.0)] * 21
    lm[G.WRIST] = (0.5, 0.9, 0.0)
    lm[G.MIDDLE_MCP] = (0.5, 0.7, 0.0)
    gap = 0.02 if pinch else 0.18
    lm[G.THUMB_TIP] = (px - gap / 2, py, 0.0)
    lm[G.INDEX_TIP] = (px + gap / 2, py, 0.0)
    reach = 0.5 if spread else 0.05
    for tip in (G.MIDDLE_TIP, G.RING_TIP, G.PINKY_TIP):
        lm[tip] = (0.5, 0.9 - reach, 0.0)
    if spread:
        lm[G.INDEX_TIP] = (0.55, 0.9 - reach, 0.0)
        lm[G.THUMB_TIP] = (0.35, 0.85, 0.0)
    return {"hand": side, "landmarks": lm}


def actions(evs):
    return [e["action"] for e in evs]


def main() -> int:
    # ---------------------------------------------------------- the primitives
    check("a pinched hand reads as pinched",
          G.pinch_ratio(hand()["landmarks"]) < G.PINCH_ON)
    check("an open hand does not",
          G.pinch_ratio(hand(pinch=False)["landmarks"]) > G.PINCH_OFF)
    check("the thresholds have hysteresis", G.PINCH_OFF > G.PINCH_ON,
          "one threshold chatters at the boundary and the model jitters with it")
    check("a spread hand is an open palm", G.palm_open(hand(pinch=False, spread=True)["landmarks"]))
    check("a pinched hand is not", not G.palm_open(hand()["landmarks"]))
    check("hand size is measured wrist to knuckle",
          abs(G.hand_size(hand()["landmarks"]) - 0.2) < 1e-6,
          "fingertip spans change hugely with pose, so normalising a pinch by "
          "one would make the threshold depend on what it is measuring")
    check("a hand with no landmarks does not raise",
          G.pinch_ratio([]) > 1 and G.palm_open([]) is False)

    # ------------------------------------------------------------- the mirror
    left_raw = G.grip_point(hand(px=0.3)["landmarks"], mirrored=True)
    right_raw = G.grip_point(hand(px=0.7)["landmarks"], mirrored=True)
    check("the mirror is undone: a bigger raw x is further LEFT on screen",
          left_raw[0] > right_raw[0],
          "without the flip the model turns the wrong way, which reads as "
          "broken rather than reversed")
    check("...and unmirrored is the plain coordinate",
          abs(G.grip_point(hand(px=0.3)["landmarks"], mirrored=False)[0] - 0.3) < 0.01)

    # ------------------------------------------------------- arming and letting go
    t = G.GestureTracker()
    check("an open hand does nothing at all", actions(t.update([hand(pinch=False)], 0.0)) == [],
          "nothing idles waiting for him to hold a pose")
    check("a pinch takes hold", actions(t.update([hand(0.5, 0.5)], 0.1)) == ["grab"])
    check("...and it is engaged", t.engaged is True)
    check("an open palm lets go",
          actions(t.update([hand(0.5, 0.5, pinch=False, spread=True)], 0.2)) == ["release"])
    check("...and it is not engaged", t.engaged is False)

    # A hand LEAVING is a release too, but not instantly: two dropped frames are
    # a detection blink, half a second is him putting his hand down.
    t = G.GestureTracker()
    t.update([hand(0.5, 0.5)], 0.0)
    check("a blink in detection does not let go", actions(t.update([], 0.1)) == [])
    check("...but a hand put down does",
          actions(t.update([], 0.1 + G.LOST_AFTER_S + 0.05)) == ["release"])

    # ------------------------------------------------------------- rotating
    t = G.GestureTracker()
    t.update([hand(0.5, 0.5)], 0.0)
    evs = t.update([hand(0.44, 0.5)], 0.1)          # raw left = his right
    check("dragging turns it about the vertical axis",
          len(evs) == 1 and evs[0]["action"] == "rotate" and evs[0]["axis"] == "z", evs)
    check("...his right turns it positively", evs[0]["degrees"] > 0, evs)
    # BOTH AXES AT ONCE on a diagonal. This asserted `evs[0]["axis"] == "x"` —
    # one axis per frame, whichever moved more — and that is most of why he
    # found it hard to control: a diagonal drag flipped between spinning and
    # tipping frame by frame, so the model lurched between two motions instead
    # of doing the one his hand was making. A trackball turns about both.
    evs = t.update([hand(0.44, 0.42)], 0.2)
    axes = {e["axis"] for e in evs if e["action"] == "rotate"}
    check("a diagonal drag turns AND tips, in the same frame",
          axes == {"x", "z"}, evs)
    # ONE update per check. Calling it again to build the failure message moved
    # the hand twelve centimetres before the tremor case below, which then
    # measured a drag and called it a tremor.
    vert = t.update([hand(0.44, 0.30)], 0.3)
    check("...and a purely vertical drag only tips",
          {e["axis"] for e in vert if e["action"] == "rotate"} == {"x"}, vert)

    check("a tremor moves nothing",
          actions(t.update([hand(0.4401, 0.3001)], 0.4)) == [],
          "hand tremor is not intent")

    # THE POSTURE RULE, as a number. A quarter of the frame must turn the model
    # most of the way round, so this is done with a bent elbow and a resting
    # forearm rather than an arm outstretched sweeping the screen.
    quarter = 0.25 * G.ROTATE_GAIN
    check("a quarter of the frame turns it most of the way round",
          100 <= quarter <= 200, f"{quarter:.0f} degrees")

    # ------------------------------------------------------------- two hands
    t = G.GestureTracker()
    evs = t.update([hand(0.4, 0.5, side="left"), hand(0.6, 0.5, side="right")], 0.0)
    check("two pinches take hold", actions(evs) == ["grab"], evs)
    check("...saying it is two", evs[0].get("hands") == 2, evs)
    evs = t.update([hand(0.3, 0.5, side="left"), hand(0.7, 0.5, side="right")], 0.1)
    check("hands apart zooms in",
          evs and evs[0]["action"] == "scale" and evs[0]["factor"] > 1, evs)
    evs = t.update([hand(0.45, 0.5, side="left"), hand(0.55, 0.5, side="right")], 0.2)
    check("hands together zooms out",
          evs and evs[0]["action"] == "scale" and evs[0]["factor"] < 1, evs)
    check("the zoom is clamped to something sane",
          all(0.5 <= e.get("factor", 1) <= 2.0 for e in evs), evs)

    # --------------------------------------- ONE control surface, not two
    # Every gesture must emit exactly what a spoken command emits. If these ever
    # diverge, hands become a second way to do things words cannot.
    from tools.holo_tools import _ACTIONS
    t = G.GestureTracker()
    emitted = set()
    t.update([hand(0.5, 0.5)], 0.0)
    for ev in t.update([hand(0.42, 0.5)], 0.1):
        emitted.add(ev["action"])
    t2 = G.GestureTracker()
    t2.update([hand(0.4, 0.5, side="left"), hand(0.6, 0.5, side="right")], 0.0)
    for ev in t2.update([hand(0.3, 0.5, side="left"), hand(0.7, 0.5, side="right")], 0.1):
        emitted.add(ev["action"])
    check("every gesture speaks the same language as the voice commands",
          emitted <= set(_ACTIONS), sorted(emitted - set(_ACTIONS)))
    check("...and it actually emitted some", emitted, emitted)

    # ------------------------------------------------------ arming, for real
    import hand_control
    from tools import holo_tools

    holo_tools._current.clear()
    r = hand_control.control.arm()
    check("nothing on the stage means nothing to arm", bool(r.get("error")), r)
    check("...and it says why", "stage" in (r.get("error") or ""), r.get("error"))

    holo_tools._current.update({"name": "x", "path": "x.stl"})
    r = hand_control.control.arm()
    check("a hologram alone is not enough — the camera must be on",
          bool(r.get("error")), r)
    check("...and the PRIMITIVE does not switch the camera on itself",
          "camera" in (r.get("error") or "").lower(),
          "arm() is not a request from him; it must never start a camera")

    # ---- but the TOOL does, because that one IS a request from him ----------
    # This reverses an earlier decision and the reasoning is worth keeping. The
    # old behaviour refused and told him to say "turn the camera on" as a second
    # sentence, so that the choice stayed his. It was not consent, it was
    # friction — "control it with my hands" is already an explicit request for a
    # camera-driven feature — and it was BROKEN: `set_camera` gives the camera
    # panel the stage, so the model he was about to grab disappeared behind a
    # webcam feed. A screenshot of the armed state found it; every functional
    # check passed, because the gestures worked on a hologram nobody could see.
    #
    # The line drawn is between layers: the primitive above still refuses, the
    # tool below starts it, says "Camera on", and the HUD shows WATCHING YOUR
    # HANDS the whole time.
    import camera as camera_mod
    real = camera_mod.camera
    started = {"n": 0, "stopped": 0}

    class FakeCamera:
        is_on = False

        def start(self):
            started["n"] += 1
            FakeCamera.is_on = True
            return {"ok": True, "on": True, "backend": "fake"}

        def stop(self):
            started["stopped"] += 1
            FakeCamera.is_on = False
            return {"ok": True, "on": False}

    camera_mod.camera = FakeCamera()
    try:
        r = asyncio.run(holo_tools.hand_control(on=True))
        check("asking for hand control turns the camera on rather than refusing",
              not r.get("error") and r.get("armed") is True, r)
        check("...it actually started it", started["n"] == 1, started)
        check("...and says so, so a live camera is never silent",
              "camera on" in (r.get("spoken") or "").lower(), r.get("spoken"))
        hand_control.control.disarm("test")

        # A camera we switched on for a grab that then fails must not be left
        # running. Nothing is on the stage, so arm() refuses.
        FakeCamera.is_on = False
        started["n"] = started["stopped"] = 0
        holo_tools._current.clear()
        r = asyncio.run(holo_tools.hand_control(on=True))
        check("a camera opened for an arm that fails is closed again",
              bool(r.get("error")) and started["stopped"] == 1,
              (r.get("error"), started))
        holo_tools._current.update({"name": "x", "path": "x.stl"})

        # And it does NOT restart a camera that was already on — he may have it
        # up for something else, and stopping it later would be a surprise.
        FakeCamera.is_on = True
        started["n"] = started["stopped"] = 0
        r = asyncio.run(holo_tools.hand_control(on=True))
        check("a camera already on is left alone", started["n"] == 0, started)
        check("...and the reply does not claim to have started it",
              "camera on" not in (r.get("spoken") or "").lower(), r.get("spoken"))
        hand_control.control.disarm("test")

        # ---- and it can be ASKED whether it is watching --------------------
        # `armed` on its own is a flag, and a flag is exactly what survives a
        # tracking loop that has died: the badge stays lit, his hands stop
        # working, and nothing is logged anywhere. The frame counter is the only
        # honest witness, so it is exposed alongside the flag.
        st = asyncio.run(holo_tools.hand_status())
        check("it can say it is not watching", st.get("armed") is False, st)
        check("...in a sentence", "not watching" in (st.get("spoken") or ""), st)
        FakeCamera.is_on = True
        asyncio.run(holo_tools.hand_control(on=True))
        st = asyncio.run(holo_tools.hand_status())
        check("...and that it is, when it is", st.get("armed") is True, st)
        check("...reporting the frames it has read, not just the flag",
              "frames" in st,
              "a dead loop leaves armed=True forever; only the counter differs")
        hand_control.control.disarm("test")
    finally:
        camera_mod.camera = real
        FakeCamera.is_on = False

    # The tier IS the security boundary, so it is asserted rather than assumed.
    # hand_status reads a counter: it opens nothing and turns nothing on.
    from tools.registry import registry
    import tools.holo_tools as _ht
    _ht.register_all() if not registry.get("hand_status") else None
    t = registry.get("hand_status")
    check("hand_status is registered", t is not None)
    if t:
        check("...at SAFE, because it only reads a counter", t.risk.value == "safe",
              f"is {t.risk.value}")
    ht = registry.get("hand_control")
    if ht:
        check("...while hand_control stays LOW, because it reads the webcam",
              ht.risk.value == "low", f"is {ht.risk.value}")
    check("disarming when it was never armed is not an error",
          hand_control.control.disarm().get("armed") is False)
    check("the resting state is off", hand_control.control.armed is False)
    check("it gives up on an empty room", hand_control.IDLE_OFF_S <= 120,
          f"{hand_control.IDLE_OFF_S}s")
    check("...and reads at a modest rate", hand_control.TARGET_FPS <= 20,
          f"{hand_control.TARGET_FPS} fps")
    holo_tools._current.clear()

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
