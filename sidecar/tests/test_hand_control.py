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
    evs = t.update([hand(0.44, 0.42)], 0.2)
    check("dragging up tips it instead",
          evs and evs[0]["axis"] == "x", evs)

    check("a tremor moves nothing",
          actions(t.update([hand(0.4401, 0.4201)], 0.3)) == [],
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
    check("...and it does NOT switch the camera on itself",
          "camera" in (r.get("error") or "").lower(),
          "a camera that turns itself on is a surprise nobody wants")
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
