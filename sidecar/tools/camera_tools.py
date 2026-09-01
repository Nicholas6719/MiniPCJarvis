"""Turning the camera on and off, by voice.

*"I want to be able to say toggle camera view mode and it pulls up the camera."*

Risk: LOW, not MEDIUM. A confirmation gate here would be the wrong instinct —
the spoken command IS the consent, and making him say "yes" after asking for his
own camera turns a feature into a chore. What LOW buys is the thing that
actually matters for a camera: every on and every off lands in the audit trail,
so there is always a record of when the device was open and who opened it.
"""
from __future__ import annotations

import asyncio
import logging
import time

from tools.registry import Risk, Tool, registry

log = logging.getLogger("jarvis.camera")

# A frame darker than this is the camera still opening its shutter, not a dark
# room: measured, frame 0 is ~8 and a settled picture is ~57.
SETTLE_BRIGHTNESS = 25.0


async def set_camera(on: bool | None = None) -> dict:
    """Turn the camera on, off, or the other way from wherever it is."""
    from camera import camera
    if on is None:
        res = await asyncio.to_thread(camera.toggle)
    elif on:
        res = await asyncio.to_thread(camera.start)
    else:
        res = await asyncio.to_thread(camera.stop)
    if not res.get("ok"):
        return {"error": res.get("error") or "the camera would not open"}
    return {"camera": "on" if res.get("on") else "off",
            "backend": res.get("backend") or None}


async def camera_status() -> dict:
    from camera import camera
    return camera.status()


async def look() -> dict:
    """Several looks at whatever is in front of the camera, judged together.

    If the camera is off this opens it, looks, and shuts it again — asking is
    itself the permission to look. If it was already on it stays on.

    Detection runs WHILE frames arrive, not after them. The first version
    gathered eight frames (~0.6 s) and then detected on all eight (~0.7 s),
    back to back; he called it "buffering", and he was right. One detection
    (~90 ms) is longer than one frame interval (~67 ms), so detecting each
    frame as it lands hides the gathering entirely.
    """
    from camera import camera
    from vision_identity import identity
    from vision_objects import LOOK_FRAMES, describe, objects

    was_on = camera.is_on
    if not was_on:
        res = await asyncio.to_thread(camera.start)
        if not res.get("ok"):
            return {"error": res.get("error") or "the camera would not open"}

    try:
        import cv2
        import numpy as np

        def decode(jpg):
            return cv2.imdecode(np.frombuffer(jpg, dtype=np.uint8), cv2.IMREAD_COLOR)

        deadline = time.time() + 7.0
        results = []
        last = None
        settled = False
        while len(results) < LOOK_FRAMES and time.time() < deadline:
            jpg = camera.frame()
            if jpg is None or jpg is last:
                await asyncio.sleep(0.03)
                continue
            last = jpg
            img = await asyncio.to_thread(decode, jpg)
            if img is None:
                continue
            if not settled:
                # frame 0 off this camera has a mean brightness of ~8 against
                # ~57 once the shutter settles; a look at it sees nothing
                if float(np.mean(img)) <= SETTLE_BRIGHTNESS:
                    await asyncio.sleep(0.06)
                    continue
                settled = True
            results.append(await asyncio.to_thread(objects.detect, img))
        if not results:
            return {"error": "the camera never produced a usable picture"}

        res = await asyncio.to_thread(objects.aggregate, results)
        if res.get("error"):
            return res
        # By now the capture thread's presence+identity pass has run at least
        # once, so "who" is as fresh as the frames the answer came from.
        who = identity.who()
        res["who"] = who
        res["said"] = describe(res, who=who)
        res["camera_left_on"] = was_on
        return res
    finally:
        if not was_on:
            await asyncio.to_thread(camera.stop)


async def count_fingers() -> dict:
    """How many fingers he is holding up, by majority across several frames."""
    from camera import camera
    from vision_hands import HAND_FRAMES, hands

    was_on = camera.is_on
    if not was_on:
        res = await asyncio.to_thread(camera.start)
        if not res.get("ok"):
            return {"error": res.get("error") or "the camera would not open"}
    try:
        import cv2
        import numpy as np

        def decode(jpg):
            return cv2.imdecode(np.frombuffer(jpg, dtype=np.uint8), cv2.IMREAD_COLOR)

        deadline = time.time() + 7.0
        frames = []
        last = None
        settled = False
        while len(frames) < HAND_FRAMES and time.time() < deadline:
            jpg = camera.frame()
            if jpg is None or jpg is last:
                await asyncio.sleep(0.03)
                continue
            last = jpg
            img = await asyncio.to_thread(decode, jpg)
            if img is None:
                continue
            if not settled:
                if float(np.mean(img)) <= SETTLE_BRIGHTNESS:
                    await asyncio.sleep(0.06)
                    continue
                settled = True
            frames.append(img)
            await asyncio.sleep(0.05)
        if not frames:
            return {"error": "the camera never produced a usable picture"}
        return await asyncio.to_thread(hands.read_many, frames)
    finally:
        if not was_on:
            await asyncio.to_thread(camera.stop)


async def learn_face(samples: int = 0) -> dict:
    """Teach JARVIS whose face this is. "Remember my face."

    Embeddings only — no image of him is ever written anywhere. Ten samples
    over a couple of seconds, so one odd expression does not define him.
    """
    from camera import camera
    from vision_identity import ENROLL_SAMPLES, identity
    from vision_presence import presence

    want = samples or ENROLL_SAMPLES
    was_on = camera.is_on
    if not was_on:
        res = await asyncio.to_thread(camera.start)
        if not res.get("ok"):
            return {"error": res.get("error") or "the camera would not open"}
    try:
        import cv2
        import numpy as np

        def decode(jpg):
            return cv2.imdecode(np.frombuffer(jpg, dtype=np.uint8), cv2.IMREAD_COLOR)

        def sample_once():
            jpg = camera.frame()
            if jpg is None:
                return None
            img = decode(jpg)
            if img is None or float(np.mean(img)) <= SETTLE_BRIGHTNESS:
                return None
            small, faces = presence.find_faces(img)
            if small is None or faces is None or len(faces) == 0:
                return None
            return identity._embed(small, faces[0])

        got = []
        deadline = time.time() + 10.0
        while len(got) < want and time.time() < deadline:
            emb = await asyncio.to_thread(sample_once)
            if emb is not None:
                got.append(emb)
            await asyncio.sleep(0.2)
        return identity.enroll_from(got)
    finally:
        if not was_on:
            await asyncio.to_thread(camera.stop)


async def forget_face() -> dict:
    from vision_identity import identity
    ok = await asyncio.to_thread(identity.forget)
    return {"ok": ok} if ok else {"error": "the profile could not be deleted"}


def register_all() -> None:
    registry.register(Tool(
        name="set_camera",
        description="Turn the webcam view on or off, or toggle it. Use for "
                    "'toggle camera view mode', 'turn the camera on', 'show me "
                    "the camera', 'turn the camera off', 'close the camera'. "
                    "Omit 'on' to toggle.",
        parameters={"type": "object", "properties": {
            "on": {"type": "boolean",
                   "description": "true to turn on, false to turn off, omit to toggle"}},
            "required": []},
        risk=Risk.LOW, handler=set_camera, timeout=15))
    registry.register(Tool(
        name="look",
        description="Look through the webcam and report what is actually there. "
                    "Use for 'what do you see', 'what am I holding', 'look at "
                    "this', 'what's in front of you'. Opens the camera briefly "
                    "if it is off, and closes it again.",
        parameters={"type": "object", "properties": {}, "required": []},
        risk=Risk.LOW, handler=look, timeout=40))
    registry.register(Tool(
        name="count_fingers",
        description="Count how many fingers the user is holding up on camera. "
                    "Use for 'how many fingers am I holding up'.",
        parameters={"type": "object", "properties": {}, "required": []},
        risk=Risk.LOW, handler=count_fingers, timeout=30))
    registry.register(Tool(
        name="learn_face",
        description="Learn the user's face so JARVIS recognises him from now on. "
                    "Use for 'remember my face', 'learn what I look like'. Stores "
                    "embeddings only, never images.",
        parameters={"type": "object", "properties": {}, "required": []},
        risk=Risk.LOW, handler=learn_face, timeout=30))
    registry.register(Tool(
        name="forget_face",
        description="Delete the stored face profile. Use for 'forget my face'.",
        parameters={"type": "object", "properties": {}, "required": []},
        risk=Risk.LOW, handler=forget_face, timeout=15))
    registry.register(Tool(
        name="camera_status",
        description="Whether the webcam is currently on, and how it is running.",
        parameters={"type": "object", "properties": {}, "required": []},
        risk=Risk.SAFE, handler=camera_status, timeout=10))
