"""The dark-room lift: on when it helps, off when it doesn't, never fatal.

He reported the camera being "significantly downgraded" and hand controls being
"hard to use". Measured at 06:40 on his own camera: mean brightness 36.9/255,
a third of the pixels near black, sharpness 13.8 — and every device control
ignored, so the only fix left is after capture.

The thing most worth gating is not that it brightens. It is that it CANNOT take
the camera down: the first version sampled brightness outside its own try, and
the camera gate went straight from passing to "the capture thread failed, no
frames within 8s". A cosmetic improvement that can stop the preview is a worse
bug than the dim picture it was meant to fix.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}{'' if ok else '  — ' + str(detail)[:120]}")
    if not ok:
        FAILURES.append(name)


def main() -> int:
    import cv2
    import numpy as np

    import camera

    # A REALISTIC dark frame, not flat blocks. The first version of this drew
    # rectangles on a constant background and the sharpness assertion failed at
    # 4.2 -> 4.7 — correctly, because CLAHE recovers LOCAL contrast and there
    # was none to recover. A real dark webcam frame is fine texture and sensor
    # noise compressed into a narrow band near black, which is exactly what it
    # is good at, and on his actual camera it went 13.6 -> 94.9.
    rng = np.random.default_rng(7)
    tex = rng.normal(0, 9, (1080, 1920)).astype(np.float32)
    yy, xx = np.mgrid[0:1080, 0:1920]
    tex += 6 * np.sin(xx / 23.0) + 6 * np.cos(yy / 19.0)      # detail to recover
    dark = np.clip(30 + tex, 0, 255).astype(np.uint8)
    cv2.rectangle(dark, (600, 300), (1300, 800), 48, -1)
    dark = cv2.cvtColor(dark, cv2.COLOR_GRAY2BGR)
    bright = np.clip(150 + tex, 0, 255).astype(np.uint8)
    bright = cv2.cvtColor(bright, cv2.COLOR_GRAY2BGR)

    def sharp(f):
        return cv2.Laplacian(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var()

    print("\na dark frame is lifted")
    lift = camera._DarkLift()
    out = lift.apply(dark)
    check("it turned itself on", lift.active)
    check("the picture got brighter",
          cv2.cvtColor(out, cv2.COLOR_BGR2GRAY).mean()
          > cv2.cvtColor(dark, cv2.COLOR_BGR2GRAY).mean(),
          f"{cv2.cvtColor(out, cv2.COLOR_BGR2GRAY).mean():.1f}")
    check("...and sharper, which is what hand tracking needs",
          sharp(out) > sharp(dark) * 2,
          f"{sharp(dark):.1f} -> {sharp(out):.1f}")

    print("\na lit room is left alone")
    lift2 = camera._DarkLift()
    out2 = lift2.apply(bright)
    check("it stayed off", not lift2.active)
    check("and the frame came back untouched", out2 is bright)

    print("\nit does not flicker on the threshold")
    lift3 = camera._DarkLift()
    lift3.apply(dark)
    check("on when dark", lift3.active)
    mid = np.full((1080, 1920, 3), 80, dtype=np.uint8)   # between the two limits
    lift3._checked = 0.0
    lift3.apply(mid)
    check("...and stays on between the two thresholds", lift3.active,
          "it switched off in the hysteresis gap")

    print("\nA BROKEN LIFT MUST NEVER STOP THE CAMERA")
    lift4 = camera._DarkLift()
    same = lift4.apply("not a frame at all")          # every cv2 call will throw
    check("it returned the frame instead of raising", same == "not a frame at all")
    check("and switched itself off rather than failing every frame",
          not lift4.active)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: " + ", ".join(FAILURES))
        return 1
    print("dark-room lift: all good")
    return 0


sys.exit(main())
