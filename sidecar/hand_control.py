"""Watching his hands, but only while there is something to move.

THIS LOOP IS THE EXPENSIVE PART OF PHASE E, so almost all of this file is about
when NOT to run.

  * It runs only while a hologram is on the stage. Nothing to move means nothing
    to watch, and a hand tracker running behind a weather answer is pure cost.
  * It runs only when he has armed it. A hologram opening does NOT switch the
    webcam on by itself — a camera that turns itself on is a surprise nobody
    wants, however good the reason.
  * It disarms itself when the stage closes, when the camera stops, and after a
    stretch with no hand in frame. The resting state of this feature is off.
  * It reads at a modest rate rather than as fast as the camera can produce, and
    one detection is awaited before the next begins, so it can never pile up.

Everything it emits is a `holo_control` payload — the same ones the spoken
commands produce — so hands and voice drive one control surface. That is what
makes rule one of phase E true: hands are never required, because there is
nothing they can do that words cannot.
"""
from __future__ import annotations

import asyncio
import logging
import time

import hand_gestures
from events import bus, spawn

log = logging.getLogger("jarvis.hands")

# Detections per second, chosen from measurement rather than picked.
#
# The landmarker is about 30 ms a frame here, and it — not the JPEG decode — is
# the cost: decoding at half size took the whole thing from 49% of a core to
# 43%, which is the decode being a small share of it. So the rate is the lever.
# At 14 fps this was 43% of a core; at 10 it is comfortably under a third, and
# 100 ms of latency is fine for what hands are FOR here. They are the coarse,
# fast, enjoyable path — "rotate it exactly ninety degrees" is a sentence, and
# the sentence is the precise one.
TARGET_FPS = 10.0

# Off again after this long with nothing to see. Short engagements are rule
# three: the resting state is hands down, and a tracker left running against an
# empty room is exactly the cost this file exists to avoid.
IDLE_OFF_S = 45.0


class HandControl:
    def __init__(self) -> None:
        self.armed = False
        self._task: asyncio.Task | None = None
        self._tracker = hand_gestures.GestureTracker()
        self._last_hand_at = 0.0
        self.frames = 0
        self.detects = 0

    def status(self) -> dict:
        return {"armed": self.armed, "frames": self.frames,
                "detects": self.detects}

    # ---- arming ---------------------------------------------------------
    def arm(self) -> dict:
        from camera import camera
        from tools.holo_tools import current

        if not current().get("path"):
            return {"error": "there's nothing on the stage to move, sir"}
        if not camera.is_on:
            # NOT switched on automatically. He can say "turn the camera on",
            # which is one sentence and leaves the decision his.
            return {"error": "the camera's off, sir — say the word and I'll "
                             "turn it on"}
        if self.armed:
            return {"armed": True, "already": True}
        self.armed = True
        self._tracker.reset()
        self._last_hand_at = time.time()
        self.frames = self.detects = 0
        self._task = spawn(self._loop(), name="hands:track")
        return {"armed": True}

    def _stand_down(self) -> bool:
        """Clear the state without touching the task. Safe to call FROM the loop."""
        was = self.armed
        self.armed = False
        self._tracker.reset()
        return was

    def disarm(self, why: str = "") -> dict:
        """Stop watching. Called from outside the loop — it cancels the task."""
        was = self._stand_down()
        # NOT from inside `_loop`: cancelling the task you are running is a
        # CancelledError delivered at some later await, to whatever happens to be
        # there. The loop uses `_stand_down` and then breaks, which is the same
        # outcome and no surprise.
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None
        return {"armed": False, "was": was, "why": why}

    # ---- the loop -------------------------------------------------------
    async def _loop(self) -> None:
        from camera import camera
        from tools.holo_tools import current

        period = 1.0 / TARGET_FPS
        try:
            while self.armed:
                await asyncio.sleep(period)
                # Every reason to stop, checked before any work is done.
                if not current().get("path"):
                    self._stand_down()
                    break
                if not camera.is_on:
                    self._stand_down()
                    break
                if time.time() - self._last_hand_at > IDLE_OFF_S:
                    self._stand_down()
                    await bus.emit("hands", action="disarmed",
                                   why="nothing in frame")
                    break

                jpg = camera.frame()
                if not jpg:
                    continue
                self.frames += 1
                # OFF THE LOOP. The landmarker is tens of milliseconds of native
                # work, and the event loop is where he waits for answers.
                res = await asyncio.to_thread(self._detect, jpg)
                if not res or res.get("error"):
                    continue
                self.detects += 1
                hands = res.get("hands") or []
                if hands:
                    self._last_hand_at = time.time()
                for ev in self._tracker.update(hands, time.time()):
                    await self._apply(ev)
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("hand tracking stopped")
            self._stand_down()
        finally:
            self._task = None

    @staticmethod
    def _detect(jpg: bytes) -> dict:
        """BLOCKING: decode and run the landmarker. Called in a thread.

        DECODED AT HALF SIZE. `IMREAD_REDUCED_COLOR_2` decodes straight to half
        dimensions — a quarter of the pixels — which makes both the JPEG decode
        and the detection substantially cheaper. It costs nothing in accuracy:
        the landmarker resizes to a small square internally regardless, and the
        landmarks it returns are NORMALISED 0..1, so every coordinate downstream
        is identical either way.

        Worth being precise about the gain, because the first note here
        overstated it: half-size decoding took the whole tracker from ~49% of a
        core to ~43%, which is what proved the decode was NOT the cost. The
        landmarker is, at ~30 ms a frame, and the frame rate is what took it the
        rest of the way down.
        """
        try:
            import cv2
            import numpy as np
            from vision_hands import hands as hand_model
            frame = cv2.imdecode(np.frombuffer(jpg, dtype=np.uint8),
                                 cv2.IMREAD_REDUCED_COLOR_2)
            if frame is None:
                return {}
            return hand_model.read_pose(frame)
        except Exception:
            log.debug("hand detect failed", exc_info=True)
            return {}

    async def _apply(self, ev: dict) -> None:
        """One gesture -> the same event a spoken command would produce."""
        action = ev.get("action")
        if action in ("grab", "release"):
            await bus.emit("hands", action=action, why=ev.get("why", ""))
            return
        from tools.holo_tools import current
        await bus.emit("holo_control", name=current().get("name", ""),
                       **{k: v for k, v in ev.items() if k != "why"})


control = HandControl()
