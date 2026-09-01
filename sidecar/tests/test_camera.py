"""The camera is off until he asks, and off means the handle is closed.

Phase 1 of the camera work: *"I want to be able to say toggle camera view mode
and it pulls up the camera."*

This runs with NO camera. cv2.VideoCapture is replaced by a fake device, because
the properties worth gating are not "does a webcam work" — they are the ones a
person would care about if they thought about an always-connected camera in
their office for ten seconds:

  * it is OFF until something asks
  * OFF releases the device, it does not merely stop reading
  * no frame outlives the session that produced it
  * a camera that will not open says so instead of hanging
  * asking twice does not open it twice

Run: python tests/test_camera.py
"""
import os
import sys
import tempfile
import time
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "cam.db"))

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


class FakeCap:
    """A webcam that behaves, and counts whether it was released."""
    released = 0
    opened = 0

    def __init__(self, refuse=False):
        self._refuse = refuse
        FakeCap.opened += 1

    def isOpened(self):
        return not self._refuse

    def set(self, *a):
        return True

    def get(self, prop):
        return 1920.0 if prop == 3 else 1080.0 if prop == 4 else 30.0

    def read(self):
        import numpy as np
        time.sleep(0.005)
        return True, np.zeros((16, 16, 3), dtype=np.uint8)

    # The capture loop drives the device as grab() + retrieve() so that every
    # frame the camera produces is consumed and none can queue up behind us —
    # the queue was ~100 ms of the lag he saw on his own face. The fake has to
    # model that split, or the test proves nothing about the real loop.
    def grab(self):
        time.sleep(0.005)
        return not self._refuse

    def retrieve(self):
        import numpy as np
        return True, np.zeros((16, 16, 3), dtype=np.uint8)

    def release(self):
        FakeCap.released += 1


def install_fake(refuse=False):
    """Replace cv2 for the camera module only."""
    import numpy as np
    fake = types.ModuleType("cv2")
    fake.CAP_MSMF, fake.CAP_DSHOW = 1400, 700
    fake.CAP_PROP_FRAME_WIDTH, fake.CAP_PROP_FRAME_HEIGHT = 3, 4
    fake.CAP_PROP_FOURCC, fake.CAP_PROP_FPS = 6, 5
    fake.IMWRITE_JPEG_QUALITY = 1
    fake.VideoWriter_fourcc = lambda *a: 0
    fake.VideoCapture = lambda idx, backend=None: FakeCap(refuse)
    fake.imencode = lambda ext, frame, params=None: (True, np.frombuffer(
        b"\xff\xd8\xff" + b"jpegbytes", dtype=np.uint8))
    sys.modules["cv2"] = fake


def main() -> int:
    install_fake()
    from camera import Camera

    cam = Camera()
    check("the camera starts OFF", cam.is_on is False)
    check("...and reports no frame", cam.status()["has_frame"] is False)
    check("...and nothing was opened just by existing", FakeCap.opened == 0,
          FakeCap.opened)

    res = cam.start()
    check("it opens when asked", res.get("ok") and cam.is_on, res)
    time.sleep(0.4)
    check("...and frames arrive", cam.frame() is not None)
    check("...reporting the size the DEVICE gave", cam.status()["width"] == 1920,
          cam.status())

    before = FakeCap.opened
    again = cam.start()
    check("asking twice does not open a second device",
          FakeCap.opened == before and again.get("already_on"), FakeCap.opened)

    rel = FakeCap.released
    cam.stop()
    check("off RELEASES the device", FakeCap.released == rel + 1,
          f"released {FakeCap.released}, was {rel}")
    check("...and is really off", cam.is_on is False)
    check("...and the last frame does not linger", cam.frame() is None,
          "a picture of him outlived the session that took it")

    # toggle
    cam.toggle()
    check("toggle turns it on", cam.is_on is True)
    cam.toggle()
    check("...and toggle turns it off again", cam.is_on is False)
    cam.stop()
    check("stopping an already-stopped camera is harmless", cam.is_on is False)

    # --- a camera that will not open ----------------------------------------
    install_fake(refuse=True)
    import importlib

    import camera as cammod
    importlib.reload(cammod)
    bad = cammod.Camera()
    t0 = time.time()
    res = bad.start()
    took = time.time() - t0
    check("a camera that will not open reports it", res.get("ok") is False, res)
    check("...quickly, rather than hanging the turn", took < 8.0, f"{took:.1f}s")
    check("...and leaves nothing running", bad.is_on is False)

    # --- the preview must not run behind the world --------------------------
    # He watched his own face in the HUD and it trailed him. Two causes, both
    # gated here: the capture loop declined frames the device had already made
    # (which then queued, ~100 ms measured), and the HTTP stream slept on its
    # own clock and could re-send a frame the HUD already had.
    import threading as _t

    fresh = cammod.Camera()
    check("a new camera hands out nothing and waits",
          fresh.frame_after(-1, timeout=0.05)[0] is None)

    seq_seen = []

    def consumer():
        s = -1
        for _ in range(3):
            data, s = fresh.frame_after(s, timeout=2.0)
            seq_seen.append((data, s))

    th = _t.Thread(target=consumer, daemon=True)
    th.start()
    time.sleep(0.05)
    for i in range(3):
        with fresh._new:
            fresh._frame = f"jpeg{i}".encode()
            fresh._seq += 1
            fresh._new.notify_all()
        time.sleep(0.05)
    th.join(timeout=3.0)

    check("the stream is woken by each new frame", len(seq_seen) == 3,
          f"got {len(seq_seen)}")
    got = [s for _d, s in seq_seen]
    check("...and never handed the same frame twice",
          len(set(got)) == len(got), got)
    check("...in order, newest last", got == sorted(got), got)

    t0 = time.time()
    fresh.frame_after(fresh._seq, timeout=0.2)
    waited = time.time() - t0
    check("waiting for a frame that never comes times out, never hangs",
          0.15 < waited < 1.0, f"{waited:.2f}s")

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
