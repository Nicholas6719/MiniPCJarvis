"""Hands on the hologram — designed around fatigue rather than ignoring it.

The research on mid-air gesturing is blunt: sustained arm-out interaction causes
measurable fatigue within a minute or two — "gorilla arm" — and the mitigations
are known. So three rules shape this file, and they change the design rather
than decorate it:

  1. HANDS ARE NEVER REQUIRED. Every gesture here has a spoken equivalent that
     already exists from phase C. Hands are the fast, coarse, enjoyable path; the
     voice is the precise one. "Rotate it exactly ninety degrees" should always
     be easier said than gestured, and it is.
  2. SUPPORTED POSTURES. The gains are set so a rotation is a few centimetres of
     hand travel with the elbow bent and the forearm resting — not an arm
     outstretched at the screen sweeping across it. ROTATE_GAIN below is the
     whole of that decision.
  3. SHORT ENGAGEMENTS. Tracking ARMS on a pinch and RELEASES on an open palm, so
     the resting state is hands down and nothing idles waiting for him to hold a
     pose. A hand that simply leaves the frame also releases.

PURE FUNCTIONS OVER LANDMARKS, deliberately. Everything here can be tested with
synthetic sequences and no camera, which is how `tests/test_hands.py` gates
finger counting and how this is gated too.

THE MIRROR. The webcam image is mirrored — he moves his hand right and the pixel
moves left. Screen-space x must therefore be flipped, or every control feels
subtly wrong rather than obviously wrong, which is much worse: obviously wrong
gets fixed in a minute, subtly wrong gets lived with.
"""
from __future__ import annotations

import math

WRIST = 0
THUMB_TIP = 4
INDEX_TIP = 8
MIDDLE_MCP = 9
MIDDLE_TIP = 12
RING_TIP = 16
PINKY_TIP = 20

# Pinch, as a fraction of the hand's own size, so it works at any distance from
# the camera. Two thresholds, not one: a single threshold flickers on and off
# around the boundary and the model jitters with it.
PINCH_ON = 0.38
PINCH_OFF = 0.55

# A rotation is a few centimetres of hand travel, not a sweep. A quarter of the
# frame's width turns the model most of the way round, which is what lets him do
# this with a bent elbow and a resting forearm. Raising this makes it faster and
# more tiring; that trade is the point.
ROTATE_GAIN = 540.0        # degrees per unit of normalised travel
DEADZONE = 0.006           # below this, it is hand tremor rather than intent
SCALE_GAIN = 1.6
MIN_SCALE_STEP = 0.02

# How long a hand may be missing before tracking releases. Two or three dropped
# frames are a detection blink; half a second is him putting his hand down.
LOST_AFTER_S = 0.5


def _xy(p) -> tuple[float, float]:
    """Landmarks arrive as objects with .x/.y or as plain tuples."""
    if hasattr(p, "x"):
        return float(p.x), float(p.y)
    return float(p[0]), float(p[1])


def _dist(a, b) -> float:
    ax, ay = _xy(a)
    bx, by = _xy(b)
    return math.hypot(ax - bx, ay - by)


def hand_size(lm) -> float:
    """Wrist to the middle knuckle — the one span that barely changes with pose.

    Fingertip spans change enormously between a fist and a spread hand, so using
    one of those to normalise a pinch would make the threshold depend on the very
    thing it is trying to measure.
    """
    if not lm or len(lm) <= MIDDLE_MCP:
        return 0.0
    return max(1e-6, _dist(lm[WRIST], lm[MIDDLE_MCP]))


def pinch_ratio(lm) -> float:
    """Thumb tip to index tip, as a fraction of hand size. Small means pinched."""
    if not lm or len(lm) <= INDEX_TIP:
        return 9.9
    return _dist(lm[THUMB_TIP], lm[INDEX_TIP]) / hand_size(lm)


def palm_open(lm) -> bool:
    """An open palm: every fingertip further from the wrist than its knuckle.

    This is the RELEASE, so it is deliberately generous — failing to notice him
    letting go is far worse than releasing a moment early, because the model then
    keeps moving after he has stopped meaning it to.
    """
    if not lm or len(lm) < 21:
        return False
    size = hand_size(lm)
    far = sum(1 for tip in (INDEX_TIP, MIDDLE_TIP, RING_TIP, PINKY_TIP)
              if _dist(lm[tip], lm[WRIST]) > size * 1.6)
    return far >= 3 and pinch_ratio(lm) > PINCH_OFF


def grip_point(lm, mirrored: bool = True) -> tuple[float, float]:
    """Where the pinch is, in SCREEN space.

    x is flipped for the mirror: he moves his hand right, the pixel moves left,
    and without this the model turns the wrong way — which reads as the control
    being broken rather than reversed.
    """
    if not lm or len(lm) <= INDEX_TIP:
        return 0.0, 0.0
    tx, ty = _xy(lm[THUMB_TIP])
    ix, iy = _xy(lm[INDEX_TIP])
    x, y = (tx + ix) / 2.0, (ty + iy) / 2.0
    return (1.0 - x if mirrored else x), y


class GestureTracker:
    """Turns a stream of hand landmarks into hologram controls.

    Emits the same payloads `holo_control` does, so hands and voice drive one
    control surface rather than two that can disagree.
    """

    def __init__(self, mirrored: bool = True) -> None:
        self.mirrored = mirrored
        self.engaged = False
        self._pinching: dict[str, bool] = {}
        self._last: tuple[float, float] | None = None
        self._two: float | None = None
        self._seen_at = 0.0

    def reset(self) -> None:
        self.engaged = False
        self._pinching.clear()
        self._last = None
        self._two = None

    def _pinch_state(self, key: str, lm) -> bool:
        """Pinched, with hysteresis so the boundary does not chatter."""
        was = self._pinching.get(key, False)
        r = pinch_ratio(lm)
        now = r < (PINCH_OFF if was else PINCH_ON)
        self._pinching[key] = now
        return now

    def update(self, hands: list, now: float) -> list[dict]:
        """One frame of hands -> zero or more control payloads.

        `hands` is a list of {"hand": "left"/"right", "landmarks": [...]}.
        """
        out: list[dict] = []
        live = [h for h in (hands or []) if h.get("landmarks")]

        if not live:
            # A blink in detection is not a release; a hand put down is.
            if self.engaged and now - self._seen_at > LOST_AFTER_S:
                self.reset()
                out.append({"action": "release", "why": "hand left the frame"})
            return out
        self._seen_at = now

        pinched = []
        for h in live:
            key = h.get("hand") or "one"
            if self._pinch_state(key, h["landmarks"]):
                pinched.append(h)

        # AN OPEN PALM RELEASES. Checked before anything else, so letting go
        # always wins over a movement in the same frame.
        if self.engaged and any(palm_open(h["landmarks"]) for h in live) and not pinched:
            self.reset()
            out.append({"action": "release", "why": "open palm"})
            return out

        if len(pinched) >= 2:
            # TWO PINCHES: the distance between them is the zoom, the way it is
            # on every touchscreen he has ever used.
            a = grip_point(pinched[0]["landmarks"], self.mirrored)
            b = grip_point(pinched[1]["landmarks"], self.mirrored)
            span = math.hypot(a[0] - b[0], a[1] - b[1])
            if self._two is None:
                self._two = span
                self.engaged = True
                out.append({"action": "grab", "hands": 2})
            elif span > 1e-6:
                ratio = span / self._two
                if abs(ratio - 1.0) > MIN_SCALE_STEP:
                    factor = 1.0 + (ratio - 1.0) * SCALE_GAIN
                    self._two = span
                    out.append({"action": "scale",
                                "factor": round(max(0.5, min(2.0, factor)), 3)})
            self._last = None
            return out
        self._two = None

        if len(pinched) == 1:
            p = grip_point(pinched[0]["landmarks"], self.mirrored)
            if self._last is None:
                self.engaged = True
                self._last = p
                out.append({"action": "grab", "hands": 1})
                return out
            dx, dy = p[0] - self._last[0], p[1] - self._last[1]
            if abs(dx) > DEADZONE or abs(dy) > DEADZONE:
                self._last = p
                # Horizontal travel spins it about the vertical axis; vertical
                # travel tips it. The same two axes the spoken controls use, so
                # "turn it" and a drag mean the same thing.
                if abs(dx) >= abs(dy):
                    out.append({"action": "rotate", "axis": "z",
                                "degrees": round(dx * ROTATE_GAIN, 1)})
                else:
                    out.append({"action": "rotate", "axis": "x",
                                "degrees": round(dy * ROTATE_GAIN, 1)})
            return out

        # Nothing pinched: whatever was held is let go.
        if self.engaged:
            self.reset()
            out.append({"action": "release", "why": "unpinched"})
        self._last = None
        return out
