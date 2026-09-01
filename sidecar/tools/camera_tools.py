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

from tools.registry import Risk, Tool, registry

log = logging.getLogger("jarvis.camera")


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
        name="camera_status",
        description="Whether the webcam is currently on, and how it is running.",
        parameters={"type": "object", "properties": {}, "required": []},
        risk=Risk.SAFE, handler=camera_status, timeout=10))
