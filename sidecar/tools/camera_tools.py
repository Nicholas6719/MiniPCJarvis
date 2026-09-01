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
    itself the permission to look, and leaving the device running afterwards
    would be taking more than he offered. If it was already on it stays on.

    It does NOT look at the first frame it can get. Measured on his own camera,
    frame 0 has a mean brightness of 8 against ~57 once exposure settles — so
    the first version was routinely reading a nearly black picture, which is a
    large part of why it missed a water bottle he was holding.
    """
    from camera import camera
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

        # Wait for a frame at all, then for the picture to stop being dark.
        deadline = time.time() + 6.0
        settled = None
        while time.time() < deadline:
            jpg = camera.frame()
            if jpg is not None:
                img = await asyncio.to_thread(decode, jpg)
                if img is not None and float(np.mean(img)) > SETTLE_BRIGHTNESS:
                    settled = img
                    break
            await asyncio.sleep(0.1)
        if settled is None:
            return {"error": "the camera never produced a usable picture"}

        # Now gather DISTINCT frames. The camera runs at ~15 fps, so a short
        # wait between grabs is what makes them different pictures rather than
        # the same one eight times.
        frames = [settled]
        last = None
        while len(frames) < LOOK_FRAMES and time.time() < deadline:
            await asyncio.sleep(0.08)
            jpg = camera.frame()
            if jpg is None or jpg is last:
                continue
            last = jpg
            img = await asyncio.to_thread(decode, jpg)
            if img is not None:
                frames.append(img)

        res = await asyncio.to_thread(objects.detect_many, frames)
        if res.get("error"):
            return res
        res["said"] = describe(res)
        res["camera_left_on"] = was_on
        return res
    finally:
        if not was_on:
            await asyncio.to_thread(camera.stop)


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
        name="camera_status",
        description="Whether the webcam is currently on, and how it is running.",
        parameters={"type": "object", "properties": {}, "required": []},
        risk=Risk.SAFE, handler=camera_status, timeout=10))
