"""The webcam, owned by the sidecar the same way the microphone is.

He bought the C920 for this: *"I want to be able to say toggle camera view mode
and it pulls up the camera... that's how I can interact with the UI."* Later it
becomes presence — whether he is actually there — and later still, an overlay.

ONE OWNER. The sidecar opens the device and everything else asks the sidecar,
exactly as it works for audio. The HUD could open the camera itself through
WebView2 and it would be less code, but then two processes would want the same
webcam the moment presence detection exists, and the first thing to break would
be the thing he cares about most. Frames come from here or they do not come.

Three properties this file will not give up:

  * It never touches the event loop. Capture runs on its own thread, like the
    audio writer, because a blocking read on a USB device is exactly the shape
    of the bug that froze him for forty minutes.
  * OFF means the device is RELEASED, not paused. This is a camera in a man's
    home office; "off" has to mean the light is out and the handle is closed.
  * Nothing is ever written to disk. Frames live in one variable and are
    replaced by the next one.

Verified on his machine 2026-09-01, with JARVIS running and holding the same
C920 as its microphone: the video endpoint opens with no effect on the audio
one. MSMF opened in 278 ms, DSHOW in 897 ms, both delivering 30/30 frames.
"""
from __future__ import annotations

import logging
import threading
import time

log = logging.getLogger("jarvis.camera")


def _presence_status() -> dict:
    """Presence, folded into camera status. Never raises."""
    try:
        from vision_presence import presence
        return presence.status()
    except Exception:
        return {"present": False, "error": "presence unavailable"}

# Native 1080p, which is what the C920 gives whether you ask for it or not.
#
# The first version of this forced 720p to save CPU. Measured on his machine,
# that was wrong twice over. The camera IGNORED the request — asking for
# 1280x720 returned 1920x1080 anyway, so the code would have quietly run at
# native resolution while claiming otherwise. And the saving did not exist:
# re-encoding costs 3.6 ms/frame at 720p against 4.0 ms at 1080p, and the read
# is 27.9 ms either way because that is a 30 fps device being waited on, not
# work being done. The whole difference was 0.4 ms and 17 KB per frame.
#
# So: take what the camera gives. Phase 2 will downscale to about 320x240 for
# face detection, because THAT is where resolution genuinely costs something.
WIDTH, HEIGHT = 1920, 1080
TARGET_FPS = 15.0          # a face and a room; this is not a video call
JPEG_QUALITY = 75          # ~116 KB/frame, 1.7 MB/s over loopback
OPEN_TIMEOUT_S = 6.0       # a camera that will not open must not hang the turn


class Camera:
    def __init__(self) -> None:
        self._cap = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._frame: bytes | None = None          # newest JPEG, and only the newest
        self._lock = threading.Lock()
        self._started_at = 0.0
        self._frames = 0
        self._error: str | None = None
        self._backend_name = ""
        self._actual = (0, 0)      # what the device really gave, not what we asked

    # ---------- state ----------

    @property
    def is_on(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def status(self) -> dict:
        with self._lock:
            has = self._frame is not None
        up = time.time() - self._started_at if self.is_on else 0.0
        return {"on": self.is_on, "has_frame": has,
                "frames": self._frames, "uptime_s": round(up, 1),
                "backend": self._backend_name,
                # what the DEVICE gave, not what was requested - this camera
                # ignores the request and the HUD should not be lied to
                "width": self._actual[0], "height": self._actual[1],
                "error": self._error,
                "presence": _presence_status()}

    # ---------- lifecycle ----------

    def start(self) -> dict:
        """Open the camera. Idempotent; never raises."""
        if self.is_on:
            return {"ok": True, "already_on": True, **self.status()}
        self._stop.clear()
        self._error = None
        self._frames = 0
        ready = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(ready,),
                                        name="jarvis-camera", daemon=True)
        self._started_at = time.time()
        self._thread.start()
        # Wait only for the OPEN, not for a frame: the first frame off a C920 is
        # ~900 ms behind the open and he should not wait for it to be told yes.
        ready.wait(OPEN_TIMEOUT_S)
        if self._error:
            self.stop()
            return {"ok": False, "error": self._error}
        return {"ok": True, **self.status()}

    def stop(self) -> dict:
        """Release the device. Off means off — the handle is closed."""
        self._stop.set()
        t, self._thread = self._thread, None
        if t is not None and t.is_alive():
            t.join(timeout=3.0)
            if t.is_alive():
                # Do not pretend. A capture thread stuck in the driver is the
                # same family as the audio writer that would not come back.
                log.warning("camera thread did not stop; the device may still be held")
        with self._lock:
            self._frame = None          # never leave the last picture of him lying around
        try:
            from vision_presence import presence
            presence.reset()            # camera shut: presence is UNKNOWN, not false
        except Exception:
            log.debug("could not reset presence", exc_info=True)
        return {"ok": True, "on": self.is_on}

    def toggle(self) -> dict:
        return self.stop() if self.is_on else self.start()

    # ---------- the capture thread ----------

    def _open(self):
        import cv2
        # MSMF is the modern Windows path and reports frame rate honestly;
        # DSHOW is the fallback because on some machines MSMF simply refuses.
        for name, backend in (("MSMF", cv2.CAP_MSMF), ("DSHOW", cv2.CAP_DSHOW)):
            try:
                cap = cv2.VideoCapture(0, backend)
                if not cap.isOpened():
                    cap.release()
                    continue
                # MJPG first: without it the driver hands back raw YUY2 and the
                # frame rate collapses. The size request is asked for honestly
                # and NOT relied on — this camera returns 1920x1080 regardless,
                # measured, so whatever it actually gives is what gets used.
                cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, WIDTH)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, HEIGHT)
                self._backend_name = name
                self._actual = (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                                int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
                log.info("camera opened via %s at %dx%d", name, *self._actual)
                return cap
            except Exception:
                log.debug("camera backend %s failed", name, exc_info=True)
        return None

    def _run(self, ready: threading.Event) -> None:
        import cv2

        from vision_presence import presence
        cap = None
        try:
            cap = self._open()
            if cap is None:
                self._error = "no camera could be opened"
                log.error("camera: %s", self._error)
                return
        finally:
            ready.set()          # unblock start() whether it worked or not

        self._cap = cap
        params = [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY]
        period = 1.0 / TARGET_FPS
        misses = 0
        try:
            while not self._stop.is_set():
                t0 = time.time()
                ok, frame = cap.read()
                if not ok or frame is None:
                    misses += 1
                    if misses >= 30:
                        self._error = "the camera stopped delivering frames"
                        log.warning("camera: %s", self._error)
                        break
                    time.sleep(0.05)
                    continue
                misses = 0
                # Presence looks at roughly one frame a second and returns
                # immediately the rest of the time. It never raises, so a
                # detector problem cannot stop the camera.
                try:
                    presence.consider(frame)
                except Exception:
                    log.debug("presence pass failed", exc_info=True)
                ok, buf = cv2.imencode(".jpg", frame, params)
                if ok:
                    with self._lock:
                        self._frame = buf.tobytes()   # only ever the newest
                    self._frames += 1
                # Pace to TARGET_FPS rather than spinning as fast as the device
                # will go; the LLM needs those cores more than the HUD does.
                rest = period - (time.time() - t0)
                if rest > 0:
                    self._stop.wait(rest)
        except Exception:
            self._error = "the capture thread failed"
            log.exception("camera capture thread failed")
        finally:
            try:
                cap.release()
            except Exception:
                log.debug("camera release failed", exc_info=True)
            self._cap = None
            log.info("camera released after %d frames", self._frames)

    # ---------- what the HUD reads ----------

    def frame(self) -> bytes | None:
        with self._lock:
            return self._frame


camera = Camera()
