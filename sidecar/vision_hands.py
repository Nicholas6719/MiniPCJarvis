"""His hands, as twenty-one landmarks each. The first brick of the evolution.

He asked JARVIS how many fingers he was holding up; YOLOX knows eighty object
nouns and cannot count fingers under any tuning at all. This can: MediaPipe's
HandLandmarker, 21 3-D points per hand, up to two hands, measured at 8 ms a
frame on this CPU. It is also, deliberately, the foundation for what he says is
coming — *"holographic images with hand controls... it needs to be able to see
me and my hands and everything I'm doing"* — because gestures are made of
exactly these landmarks.

PACKAGING, because this nearly could not ship: mediapipe's wheel declares a
dependency on opencv-contrib-python, which would fight the opencv-python the
whole vision stack runs on. Installed with --no-deps, plain cv2 satisfies every
import it actually makes (proven in a scratch venv before touching the real
one). Its drawing helper also imports matplotlib at module level purely for
debug overlays nobody calls — the stub below keeps 40 MB of plotting library
out of a bundle that would never use it.

COUNTING. A finger is extended when its TIP is farther from the wrist than its
middle joint — a rule that survives any rotation of the hand, where "tip above
knuckle" breaks the moment he turns his wrist. The thumb is judged against the
pinky side of the palm for the same reason. And one frame is never the answer:
the count is the majority across several frames, because a mid-gesture blur
should not become "seven".
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time

log = logging.getLogger("jarvis.hands")

MODEL = "hand_landmarker.task"
HAND_FRAMES = 6           # majority vote across these
# landmark indices
WRIST = 0
FINGERS = ((8, 6), (12, 10), (16, 14), (20, 18))   # (tip, pip) index..pinky
THUMB_TIP, THUMB_MCP, PINKY_MCP = 4, 2, 17


def _stub_matplotlib() -> None:
    import types
    if "matplotlib" not in sys.modules:
        fake = types.ModuleType("matplotlib")
        fake.pyplot = types.ModuleType("matplotlib.pyplot")
        sys.modules["matplotlib"] = fake
        sys.modules["matplotlib.pyplot"] = fake.pyplot


def _model_path() -> str | None:
    here = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    for cand in (os.path.join(here, "models", MODEL),
                 os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", MODEL)):
        if os.path.exists(cand):
            return cand
    return None


def _d2(a, b) -> float:
    return (a.x - b.x) ** 2 + (a.y - b.y) ** 2


def count_extended(landmarks) -> int:
    """How many digits of ONE hand are up. Pure geometry, testable offline."""
    if not landmarks or len(landmarks) < 21:
        return 0
    wrist = landmarks[WRIST]
    n = 0
    for tip, pip in FINGERS:
        if _d2(landmarks[tip], wrist) > _d2(landmarks[pip], wrist):
            n += 1
    # The thumb cannot be judged against the wrist (it sits beside it); judge it
    # against the pinky side of the palm instead: an extended thumb is far from
    # it, a tucked one crosses toward it.
    if _d2(landmarks[THUMB_TIP], landmarks[PINKY_MCP]) > _d2(
            landmarks[THUMB_MCP], landmarks[PINKY_MCP]):
        n += 1
    return n


class Hands:
    def __init__(self) -> None:
        self._lm = None
        self._unavailable = False
        self._lock = threading.Lock()

    def _landmarker(self):
        if self._lm is not None or self._unavailable:
            return self._lm
        path = _model_path()
        if not path:
            self._unavailable = True
            log.error("hands: %s is missing", MODEL)
            return None
        try:
            _stub_matplotlib()
            from mediapipe.tasks.python import BaseOptions, vision
            opts = vision.HandLandmarkerOptions(
                base_options=BaseOptions(model_asset_path=path), num_hands=2)
            self._lm = vision.HandLandmarker.create_from_options(opts)
            log.info("hands: landmarker ready")
        except Exception:
            self._unavailable = True
            log.exception("hands: landmarker would not load")
        return self._lm

    def read(self, frame_bgr) -> dict:
        """One frame -> hands and finger counts. Never raises."""
        lm = self._landmarker()
        if lm is None:
            return {"error": "the hand model is unavailable"}
        try:
            import cv2
            import mediapipe as mp
            rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
            img = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            t0 = time.time()
            with self._lock:
                res = lm.detect(img)
            hands = []
            for i, marks in enumerate(res.hand_landmarks):
                side = ""
                try:
                    side = res.handedness[i][0].category_name.lower()
                except Exception:
                    pass
                hands.append({"hand": side, "fingers": count_extended(marks)})
            return {"hands": hands,
                    "fingers": sum(h["fingers"] for h in hands),
                    "detect_ms": round((time.time() - t0) * 1000, 1)}
        except Exception:
            log.exception("hand read failed")
            return {"error": "the hand read failed"}

    def read_pose(self, frame_bgr) -> dict:
        """One frame -> the twenty-one LANDMARKS per hand, kept rather than counted.

        `read` computes exactly these and throws them away, because counting
        fingers only needs the total. Tracking a hand needs where it is, so this
        is the same detection with nothing discarded.

        Deliberately NOT `read_many`: that takes a majority vote across six
        frames, which is right for answering "how many fingers" and completely
        wrong for following a hand — a vote over half a second is half a second
        of lag, and lag is the whole difference between a control that feels
        attached to his hand and one that does not.
        """
        lm = self._landmarker()
        if lm is None:
            return {"error": "the hand model is unavailable"}
        try:
            import cv2
            import mediapipe as mp
            rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
            img = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            t0 = time.time()
            with self._lock:
                res = lm.detect(img)
            hands = []
            for i, marks in enumerate(res.hand_landmarks):
                side = ""
                try:
                    side = res.handedness[i][0].category_name.lower()
                except Exception:
                    pass
                hands.append({
                    "hand": side,
                    "fingers": count_extended(marks),
                    "landmarks": [(round(float(p.x), 4), round(float(p.y), 4),
                                   round(float(p.z), 4)) for p in marks],
                })
            return {"hands": hands, "detect_ms": round((time.time() - t0) * 1000, 1)}
        except Exception:
            log.exception("hand pose read failed")
            return {"error": "the hand read failed"}

    def read_many(self, frames: list) -> dict:
        """Several frames; the answer is the majority, not one blurry moment."""
        if not frames:
            return {"error": "no frames to look at"}
        counts: dict[int, int] = {}
        per_hand_best: list = []
        looked = 0
        for f in frames:
            r = self.read(f)
            if r.get("error"):
                continue
            looked += 1
            total = r["fingers"] if r["hands"] else -1   # -1 = no hand at all
            counts[total] = counts.get(total, 0) + 1
            if r["hands"]:
                per_hand_best = r["hands"]
        if not looked:
            return {"error": "every frame failed"}
        # majority; ties resolved toward the more frequent LATER? No: highest
        # vote, ties toward seeing a hand rather than not.
        winner = max(counts.items(), key=lambda kv: (kv[1], kv[0] >= 0))[0]
        if winner < 0:
            return {"hands": [], "fingers": 0, "no_hands": True, "frames": looked}
        return {"hands": per_hand_best, "fingers": winner, "frames": looked}


hands = Hands()
