"""Presence is a soft signal, and it is only allowed to vote one way.

Phase 2 of the camera work: YuNet looks at one frame a second and answers "is
there a face in front of the camera". Measured on his machine: 3.00 ms a check,
0.3% of one core at 1 fps, and at threshold 0.85 it finds exactly the one real
face in a test image where 0.5 invents three and 0.3 invents twelve.

The properties gated here are the ones that decide whether this HELPS him:

  * ONE sighting says present; a long run of empty frames is required to say
    absent. The asymmetry is deliberate — being wrong about "he is here" costs
    nothing, being wrong about "he is gone" sends his own answer to his phone
    while he is sitting reading it.
  * The camera may say "he IS here" and may never say "he is NOT". It is not
    evidence of absence: he leans out of frame, the room goes dark, and the
    camera is usually off entirely.
  * It is NOT authentication. A photograph would satisfy it. Nothing dangerous
    is allowed to hang on it, and this file exists partly to keep that honest.
  * A missing model, a broken detector or a closed camera degrade to "I don't
    know" and never raise into the capture thread.

Offline: no camera, no model needed. Run: python tests/test_presence.py
"""
import os
import sys
import tempfile
import time
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "pres.db"))

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


class FakeDetector:
    """Returns however many faces the test says are in front of the camera."""
    faces = 0

    def detect(self, img):
        n = FakeDetector.faces
        return None, ([[0, 0, 10, 10]] * n if n else None)


def main() -> int:
    import vision_presence as vp

    # a cv2 stand-in: resize is identity, no model is loaded
    fake = types.ModuleType("cv2")
    fake.resize = lambda img, size: img
    sys.modules["cv2"] = fake

    p = vp.Presence()
    p._det = FakeDetector()          # skip model loading entirely

    def look(faces, n=1):
        """Offer n frames with `faces` faces, defeating the 1/second throttle."""
        FakeDetector.faces = faces
        for _ in range(n):
            p._last_check = 0.0
            p.consider(object())

    check("presence starts unknown, not present", p.present is False)

    look(1)
    check("one sighting is enough to say he is here", p.present is True)
    check("...and it counts the faces", p.status()["faces"] == 1, p.status())

    # a single empty frame must NOT evict him
    look(0)
    check("one empty frame does not send him away", p.present is True,
          "he blinked and JARVIS decided he had left")

    look(0, vp.ABSENT_AFTER_MISSES - 2)
    check("...nor does a short run of them", p.present is True, p.status())

    look(0, 3)
    check("a sustained absence IS absence", p.present is False, p.status())

    look(1)
    check("and he comes back instantly", p.present is True)

    # closing the camera means unknown, not "he left"
    p.reset()
    check("closing the camera clears presence", p.present is False)

    # --- it must never raise into the capture thread ------------------------
    class Explodes:
        def detect(self, img):
            raise RuntimeError("the detector fell over")

    p2 = vp.Presence()
    p2._det = Explodes()
    p2._last_check = 0.0
    try:
        p2.consider(object())
        ok = True
    except Exception as e:
        ok = False
        print("     raised:", e)
    check("a broken detector does not raise into the camera", ok)
    check("...and presence stays false rather than lying", p2.present is False)

    p3 = vp.Presence()
    p3._unavailable = True
    p3._last_check = 0.0
    try:
        p3.consider(object())
        ok = True
    except Exception:
        ok = False
    check("a missing model is survivable", ok and p3.present is False)

    # --- the throttle: it must not look at every frame ----------------------
    p4 = vp.Presence()
    p4._det = FakeDetector()
    FakeDetector.faces = 1
    for _ in range(50):
        p4.consider(object())        # 50 frames, no time passing
    check("it checks about once a second, not once a frame",
          p4.status()["checks"] <= 2, f"{p4.status()['checks']} checks from 50 frames")

    # --- the camera may only vote ONE way in delivery ------------------------
    import delivery as D
    real_locked, real_idle = D.workstation_locked, D.user_idle_seconds
    try:
        D.workstation_locked = lambda: False
        D.user_idle_seconds = lambda: 9999.0          # idle clock says "long gone"
        D._camera_sees_him = lambda: True
        check("a face on camera can say he IS here when the idle clock gave up",
              D.is_present() is True)
        D._camera_sees_him = lambda: False
        check("...and with no face it falls back to idle, still away",
              D.is_present() is False)

        D.user_idle_seconds = lambda: 1.0             # he is plainly at the keyboard
        D._camera_sees_him = lambda: False
        check("the camera can NEVER say he is absent", D.is_present() is True,
              "an empty frame overrode a man typing")

        D.workstation_locked = lambda: True
        D._camera_sees_him = lambda: True
        check("a locked workstation is away whatever the camera sees",
              D.is_present() is False)
    finally:
        D.workstation_locked, D.user_idle_seconds = real_locked, real_idle

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
