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
        st = presence.status()
        from vision_identity import identity
        st["who"] = identity.who()
        st["enrolled"] = identity.enrolled
        return st
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
# 30 fps, which is everything this camera has. He asked why the preview was not
# 30, or 60. The 15 was a guess — "a face and a room; this is not a video call" —
# made before any of this was measured, and half of every frame the device
# produced was being thrown away for no benefit he could see.
#
# Probed (.agent/scripts/fpsprobe.py): the camera IGNORES every mode request and
# returns 1920x1080 at 30 fps regardless — asking for 1280x720@60 still gives
# back 1080p30 — so 60 is not on offer at any resolution. At 30 fps the whole
# per-frame cost is decode 4.9 ms + encode 3.0 ms = 7.9 ms, i.e. 238 ms of one
# second, about 24% of ONE core of sixteen, and 3.6 MB/s over loopback. That is
# affordable, and it halves the interval between frames from 67 ms to 33 ms,
# which is latency as well as smoothness.
TARGET_FPS = 30.0
JPEG_QUALITY = 75          # ~116 KB/frame, 1.7 MB/s over loopback

# WHEN THE ROOM IS DARK, LIFT THE PICTURE. Measured on his camera at 06:40:
# mean brightness 36.9/255, a third of the pixels near black, sharpness 13.8.
# Every device control is ignored by this camera (exposure, gain and brightness
# all accept a value and keep the old one, exactly as it already ignores
# resolution and fps), so the only place left to fix it is here.
#
# CLAHE on the luma channel took that frame from mean 38.1 / sharpness 13.6 to
# mean 61.2 / sharpness 94.9 for 5.7 ms. Sharpness is the one that matters:
# local contrast is what the hand tracker keys on, and "hard to use hand
# controls" in a dark room is what he actually reported.
DARK_BELOW = 70.0          # mean luma at which the lift comes on
LIGHT_ABOVE = 95.0         # ...and the gap it has to climb back over to go off
DARK_SAMPLE_S = 1.0        # how often brightness is checked, on a thumbnail
OPEN_TIMEOUT_S = 6.0       # a camera that will not open must not hang the turn


class _DarkLift:
    """Brighten only while it is actually dark, and decide that cheaply.

    Hysteresis rather than a single threshold: a room sitting exactly on the
    line would otherwise alternate between two visibly different pictures every
    second, which looks like a fault.
    """

    def __init__(self) -> None:
        self._clahe = None
        self._on = False
        self._checked = 0.0

    @property
    def active(self) -> bool:
        return self._on

    def apply(self, frame):
        """Never raises. The rule at the top of this file is that nothing
        optional may stop the camera, and this is as optional as it gets: the
        whole body is guarded, not just the part that seemed likely to fail.

        Learned immediately. The brightness sample was outside the try, and the
        camera gate — which drives the loop with a stubbed cv2 — went from
        passing to "the capture thread failed, no frames within 8s". A cv2 that
        cannot resize is far-fetched; a cosmetic improvement taking the whole
        preview down with it is the actual bug, and it would have shipped."""
        try:
            import cv2
            now = time.time()
            if now - self._checked >= DARK_SAMPLE_S:
                self._checked = now
                # On a thumbnail: the mean of a 160-wide image is the mean of
                # the picture for this, and costs a fraction of a millisecond.
                small = cv2.resize(frame, (160, 90), interpolation=cv2.INTER_AREA)
                mean = float(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).mean())
                if self._on and mean > LIGHT_ABOVE:
                    self._on = False
                    log.info("camera: room is lit again (mean %.0f) - lift off", mean)
                elif not self._on and mean < DARK_BELOW:
                    self._on = True
                    log.info("camera: dark room (mean %.0f) - lifting the picture", mean)
            if not self._on:
                return frame
            if self._clahe is None:        # built once, not rebuilt per frame
                self._clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            yuv = cv2.cvtColor(frame, cv2.COLOR_BGR2YUV)
            yuv[:, :, 0] = self._clahe.apply(yuv[:, :, 0])
            return cv2.cvtColor(yuv, cv2.COLOR_YUV2BGR)
        except Exception:
            # A picture that cannot be improved is still a picture. Switched off
            # rather than retried every frame for the rest of the session.
            log.debug("dark-room lift unavailable; leaving the frame alone",
                      exc_info=True)
            self._on = False
            self._checked = float("inf")
            return frame


class Camera:
    def __init__(self) -> None:
        self._cap = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._frame: bytes | None = None          # newest JPEG, and only the newest
        self._lock = threading.Lock()
        # Bumped every time a new JPEG lands, so the HTTP stream can WAIT for a
        # frame instead of sleeping on its own clock and re-sending stale ones.
        self._seq = 0
        self._new = threading.Condition(self._lock)
        self._started_at = 0.0
        self._frames = 0
        self._lift = _DarkLift()
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
                # Named, because a brightened picture that does not say it is
                # brightened is a quiet lie about what the room looks like.
                "lifted": self._lift.active,
                "presence": _presence_status()}

    # ---------- lifecycle ----------

    def start(self) -> dict:
        """Open the camera. Idempotent; never raises."""
        if self.is_on:
            return {"ok": True, "already_on": True, **self.status()}
        # A THREAD THAT NEVER CAME BACK STILL OWNS THE DEVICE. If the last
        # stop() timed out with the capture thread stuck inside the driver,
        # clearing `_stop` here would let that thread — if it ever returns —
        # run its loop forever alongside the new one: two capture threads and a
        # light that never goes out. Refuse until it is actually gone.
        stuck = getattr(self, "_stuck", None)
        if stuck is not None and stuck.is_alive():
            self._error = "the camera is still held by a capture that did not stop"
            log.warning("camera: %s", self._error)
            return {"ok": False, "error": self._error}
        self._stuck = None
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
        opened = ready.wait(OPEN_TIMEOUT_S)
        if self._error:
            self.stop()
            return {"ok": False, "error": self._error}
        if not opened:
            # NOT "ok". The open is still blocked in the driver (MSMF after a
            # sleep/resume does this); this used to answer "Camera's on, sir"
            # with a black stream behind it.
            self._error = "the camera did not open in time"
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
                self._stuck = t          # start() refuses until this has exited
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

        # PRELOAD OFF THIS THREAD. Not because it was measurably slow — all four
        # models together load in 0.93 s (face 0.26, identity 0.07, hands 0.50,
        # YOLOX 0.10) — but because this is the thread that produces frames, and
        # anything that blocks it stops the preview by construction. It cost
        # nothing to move and it removes a whole class of future stall.
        #
        # WHAT IT IS NOT: I first blamed this for a 25-second ramp from 4.8 fps
        # to 30 on a cold start, and that was an artifact of my instrument — fps
        # computed from the status counters rather than from frames actually
        # arriving. Counting real frames off the stream, a cold start reaches
        # 24 fps within four seconds and holds 28-30. The loop itself times at
        # grab 27ms, retrieve 2.8ms, presence 0.0ms, encode 3.9ms. There was no
        # frame-rate problem to fix.
        def _preload() -> None:
            try:
                presence._detector()
                from vision_identity import identity
                identity._recognizer()
                from vision_objects import objects
                objects._load()
                from vision_hands import hands
                hands._landmarker()
                log.info("vision models ready")
            except Exception:
                log.debug("vision model preload failed", exc_info=True)

        threading.Thread(target=_preload, name="vision-preload",
                         daemon=True).start()
        params = [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY]
        misses = 0
        # LATENCY, measured rather than assumed. He watched his own face in the
        # HUD and it trailed him. The old loop did read() then slept to pace 15
        # fps — but the device delivers 30, so the frames we declined to take
        # QUEUED: a probe found three of them waiting (~100 ms) after any pause,
        # and every read() then handed back the oldest of them. The preview ran a
        # fixed distance behind the world and stayed there.
        #
        # Now the loop consumes EVERY frame the device produces, so nothing can
        # accumulate, and only decodes/encodes on the 15 fps beat. grab() does
        # not decode — it is the device wait, not work — so this costs no extra
        # CPU, which matters because the LLM needs those cores.
        # Keep every Nth frame by COUNT, not by clock. A deadline of "now +
        # 66.7 ms" set from the frame just encoded lands a hair after the next
        # device frame on a 33.3 ms grid, so that one is missed and the one
        # after it is taken: a measured 10 fps against a 15 fps target, drifting
        # by construction. A stride cannot drift.
        src_fps = 0.0
        try:
            src_fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        except Exception:
            pass
        if not (1.0 <= src_fps <= 240.0):
            src_fps = 30.0                          # what this camera reports
        stride = max(1, int(round(src_fps / TARGET_FPS)))
        log.info("camera: %.0f fps device, keeping 1 in %d -> %.1f fps preview",
                 src_fps, stride, src_fps / stride)
        n = -1
        try:
            while not self._stop.is_set():
                if not cap.grab():                 # blocks ~33 ms on a 30 fps cam
                    misses += 1
                    if misses >= 30:
                        self._error = "the camera stopped delivering frames"
                        log.warning("camera: %s", self._error)
                        break
                    time.sleep(0.05)
                    continue
                n += 1
                if n % stride:
                    continue                       # frame consumed, deliberately not kept
                ok, frame = cap.retrieve()
                if not ok or frame is None:
                    continue
                misses = 0
                # BEFORE everything downstream, so the presence detector, the
                # hand tracker and the picture he sees all get the same lifted
                # frame. Lifting only the preview would leave hand control
                # exactly as hard as he found it.
                frame = self._lift.apply(frame)
                # Presence looks at roughly one frame a second and returns
                # immediately the rest of the time. It never raises, so a
                # detector problem cannot stop the camera.
                try:
                    presence.consider(frame)
                except Exception:
                    log.debug("presence pass failed", exc_info=True)
                ok, buf = cv2.imencode(".jpg", frame, params)
                if ok:
                    data = buf.tobytes()
                    with self._new:                # the lock, plus a wake-up
                        self._frame = data         # only ever the newest
                        self._seq += 1
                        self._new.notify_all()
                    self._frames += 1
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

    def frame_after(self, seq: int, timeout: float = 1.0):
        """Block until a frame NEWER than `seq` exists. Returns (data, seq).

        The stream used to sleep 1/15 s and take whatever was there, which added
        up to another frame of lag on top of the capture path and could re-send
        one it had already sent. Waiting on the condition means the HUD gets each
        frame the instant it is encoded, and never the same one twice.
        """
        with self._new:
            if self._seq <= seq:
                self._new.wait(timeout)
            return self._frame, self._seq


camera = Camera()
