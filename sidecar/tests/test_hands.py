"""Counting fingers is geometry plus a majority — both have to be right.

He held up all ten fingers and was told he was holding up none. YOLOX could
never count fingers; the HandLandmarker can (21 landmarks a hand, ~8 ms a frame
on this CPU). What is gated here is everything that does not need the model:

  * the extended-finger rule itself, on hand-built landmark sets — an open hand
    is five, a fist is none, and the rule survives the hand being upside down,
    because "tip above the knuckle" would not;
  * the majority vote: one blurred mid-gesture frame must not become the answer;
  * the spoken line NEVER names left or right. The webcam is a mirror, so the
    model's "left" is HIS right — a side claim would be confidently wrong half
    the time, which is the exact failure he keeps catching;
  * every failure is a sentence, never a raise.

Offline: no camera, no mediapipe. Run: python tests/test_hands.py
"""
import os
import sys
import tempfile
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "hands.db"))

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


def P(x, y):
    return types.SimpleNamespace(x=x, y=y)


def hand(extended: set, upside_down=False):
    """21 synthetic landmarks. Fingers in `extended` reach far from the wrist."""
    s = -1.0 if upside_down else 1.0
    pts = [P(0.5, 0.9 * s)] * 21           # wrist-ish default everywhere
    pts = list(pts)
    # thumb (1..4): an extended thumb points AWAY from the pinky side (x=0.7),
    # so its chain drifts toward LOW x; a tucked one crosses toward the palm.
    cols = {1: 0.44, 2: 0.40, 3: 0.34, 4: 0.26}
    for i, x in cols.items():
        pts[i] = P(x if 0 in extended else 0.58, 0.8 * s)
    if 0 not in extended:
        pts[4] = P(0.64, 0.78 * s)          # tucked: tip closer to pinky side
    # four fingers: chains at x=.45,.5,.55,.6 — pip mid, tip far when extended
    for f, (tip, pip) in enumerate(((8, 6), (12, 10), (16, 14), (20, 18)), start=1):
        x = 0.40 + f * 0.06
        pts[pip] = P(x, 0.55 * s)
        pts[tip] = P(x, (0.25 if f in extended else 0.70) * s)
    pts[17] = P(0.70, 0.60 * s)             # pinky mcp
    return pts


def main() -> int:
    from vision_hands import Hands, count_extended

    check("an open hand is five", count_extended(hand({0, 1, 2, 3, 4})) == 5,
          count_extended(hand({0, 1, 2, 3, 4})))
    check("a fist is none", count_extended(hand(set())) == 0,
          count_extended(hand(set())))
    check("two fingers are two", count_extended(hand({1, 2})) == 2,
          count_extended(hand({1, 2})))
    check("four without the thumb", count_extended(hand({1, 2, 3, 4})) == 4)
    check("...and the rule survives the hand being upside down",
          count_extended(hand({0, 1, 2, 3, 4}, upside_down=True)) == 5,
          count_extended(hand({0, 1, 2, 3, 4}, upside_down=True)))
    check("garbage landmarks are zero, not a crash",
          count_extended(None) == 0 and count_extended([P(0, 0)] * 3) == 0)

    # --- majority across frames ---------------------------------------------
    class Replay(Hands):
        def __init__(self, seq):
            self.seq, self.i = list(seq), 0

        def read(self, frame):
            r = self.seq[self.i]
            self.i += 1
            return r

    ten = {"hands": [{"hand": "", "fingers": 5}, {"hand": "", "fingers": 5}],
           "fingers": 10, "detect_ms": 8}
    blur = {"hands": [{"hand": "", "fingers": 3}], "fingers": 3, "detect_ms": 8}
    res = Replay([ten, ten, blur, ten, ten, ten]).read_many([None] * 6)
    check("one blurred frame does not change the answer", res["fingers"] == 10, res)

    none = {"hands": [], "fingers": 0, "detect_ms": 8}
    res2 = Replay([none] * 6).read_many([None] * 6)
    check("no hands at all is said as such", res2.get("no_hands") is True, res2)

    err = {"error": "boom"}
    res3 = Replay([err, ten, ten, err, ten, ten]).read_many([None] * 6)
    check("failed frames are dropped, not counted as zero",
          res3["fingers"] == 10, res3)
    check("all frames failing is an error",
          Replay([err] * 4).read_many([None] * 4).get("error") is not None)
    check("no frames is an error",
          Hands().read_many([]).get("error") is not None)

    # a missing model reports rather than raising
    broken = Hands()
    broken._unavailable = True
    check("a missing model is a sentence, not a crash",
          broken.read(object()).get("error") is not None)

    # --- what he hears -------------------------------------------------------
    from brain.skills import say_fingers
    check("ten is ten, five on each",
          say_fingers({}, ten) == "Ten, sir — five on each hand.",
          say_fingers({}, ten))
    check("no hands seen is honest",
          say_fingers({}, {"hands": [], "no_hands": True, "fingers": 0})
          == "I don't see your hands, sir.")
    fist = {"hands": [{"hand": "left", "fingers": 0}], "fingers": 0}
    check("a fist is 'closed', not 'I see nothing'",
          say_fingers({}, fist) == "None, sir — your hands are closed.",
          say_fingers({}, fist))
    mixed = {"hands": [{"hand": "left", "fingers": 5},
                       {"hand": "right", "fingers": 2}], "fingers": 7}
    said = say_fingers({}, mixed)
    check("uneven hands are described", said ==
          "Seven, sir — five on one hand and two on the other.", said)
    check("...and NO left/right claim is ever made — the webcam is a mirror",
          "left" not in said.lower() and "right" not in said.lower(), said)
    check("an error says why",
          "couldn't read" in say_fingers({}, {"error": "the camera would not open"}))

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
