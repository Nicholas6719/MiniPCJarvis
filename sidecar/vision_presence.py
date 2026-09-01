"""Is he actually there? Face detection on the webcam, once a second.

Phase 2 of the camera work. *"That's how we will create like a security layer,
so if he doesn't see me, he doesn't do certain things."*

YuNet (OpenCV's own DNN face detector) at 320x240, once a second. Measured on
the reference part it is ~0.7 ms a frame at 160x120 and a few ms at 320x240 — at
1 fps that is a fraction of one percent of one core, which is the entire point:
JARVIS is already running a 20B model, speech recognition and speech synthesis
on the same sixteen threads, and presence is not allowed to compete with any of
them.

WHAT THIS IS NOT
================
It is not authentication. It answers "is there a face in front of the camera",
not "is that Nicholas", and even the identity version would be defeated by a
photograph held up to the lens. So this is a SOFT signal — good for deciding
whether to speak aloud or send to his phone, and not something to hang a HIGH
risk tool on. Anything that would be dangerous if a stranger triggered it stays
behind the confirmation gate it already has.

HYSTERESIS, because the failure modes are asymmetric. Declaring him ABSENT when
he is there is the expensive mistake — it would route his own answers to his
phone while he sits watching the screen. So: one sighting is enough to say he is
present, and a run of consecutive empty frames is required to say he is not. He
looks away, he leans out of frame, the light changes; none of that is leaving.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time

log = logging.getLogger("jarvis.presence")

# The detector is fed a small frame on purpose: face detection cost scales with
# pixels, and a face at arm's length is enormous at this size.
DETECT_W, DETECT_H = 320, 240
DETECT_EVERY_S = 1.0        # once a second is plenty to know if a man is sitting there
SCORE_THRESHOLD = 0.85      # YuNet's own confidence, not a distance
# One sighting says present; this many consecutive empty checks say absent.
# Twelve seconds of nothing, not one blink.
ABSENT_AFTER_MISSES = 12

MODEL = "face_detection_yunet_2023mar.onnx"


def _model_path() -> str | None:
    """Where the ONNX file lives, frozen or not."""
    here = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    for cand in (os.path.join(here, "models", MODEL),
                 os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", MODEL)):
        if os.path.exists(cand):
            return cand
    return None


class Presence:
    """Whether a face is in front of the camera. Never raises, never blocks."""

    def __init__(self) -> None:
        self._det = None
        self._lock = threading.Lock()
        self._last_check = 0.0
        self._misses = 0
        self._seen = False
        self._faces = 0
        self._last_seen_at = 0.0
        self._checks = 0
        self._ms = 0.0
        self._error: str | None = None
        self._unavailable = False

    # ---------- state ----------

    def status(self) -> dict:
        with self._lock:
            return {"present": self._seen, "faces": self._faces,
                    "last_seen_ago_s": (round(time.time() - self._last_seen_at, 1)
                                        if self._last_seen_at else None),
                    "checks": self._checks,
                    "detect_ms": round(self._ms, 2),
                    "error": self._error}

    @property
    def present(self) -> bool:
        with self._lock:
            return self._seen

    def reset(self) -> None:
        """The camera closed: presence is unknown, not false."""
        with self._lock:
            self._seen = False
            self._faces = 0
            self._misses = 0

    # ---------- detection ----------

    def _detector(self):
        if self._det is not None or self._unavailable:
            return self._det
        path = _model_path()
        if not path:
            self._unavailable = True
            self._error = "the face model is missing"
            log.error("presence: %s (looked for %s)", self._error, MODEL)
            return None
        try:
            import cv2
            self._det = cv2.FaceDetectorYN.create(path, "", (DETECT_W, DETECT_H),
                                                  SCORE_THRESHOLD)
            log.info("presence: face detector ready (%s)", os.path.basename(path))
        except Exception:
            self._unavailable = True
            self._error = "the face detector would not load"
            log.exception("presence: could not create the detector")
        return self._det

    def consider(self, frame) -> None:
        """Offered every captured frame; actually looks about once a second.

        Called from the capture thread, so it must be cheap and it must never
        raise — a detector problem cannot be allowed to stop the camera.
        """
        now = time.time()
        if now - self._last_check < DETECT_EVERY_S:
            return
        self._last_check = now
        det = self._detector()
        if det is None:
            return
        try:
            import cv2
            t0 = time.time()
            small = cv2.resize(frame, (DETECT_W, DETECT_H))
            _, faces = det.detect(small)
            took = (time.time() - t0) * 1000.0
            n = 0 if faces is None else int(len(faces))
        except Exception:
            log.debug("presence check failed", exc_info=True)
            return

        with self._lock:
            self._checks += 1
            self._ms = took
            self._faces = n
            if n > 0:
                # One sighting is enough. Being wrong about "he is here" costs
                # nothing; being wrong about "he is gone" sends his own answer
                # to his phone while he is looking at the screen.
                self._misses = 0
                self._seen = True
                self._last_seen_at = now
            else:
                self._misses += 1
                if self._misses >= ABSENT_AFTER_MISSES:
                    self._seen = False


presence = Presence()
