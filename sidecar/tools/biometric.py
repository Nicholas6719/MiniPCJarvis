"""His face as a SECOND signal on HIGH-risk confirmations.

Built on the SFace embedder already in vision_identity.py — bundled in the spec,
gated by 22 offline tests, storing embeddings and never images. insightface was
declined: it would duplicate this while adding a Cython build, an opencv
dependency risking the fight mediapipe already caused, and a ~300 MB model
download AT RUNTIME, which breaks both the offline guarantee and the
bundle-the-models pattern every other model here follows.

THE SECURITY PROPERTY, and it is enforced in registry.execute() rather than
promised here: this is ADDITIVE. It runs only AFTER a spoken yes has already been
given, and it can only ever REFUSE. There is no path by which a face grants
anything, and no configuration flag that lets it stand in for the voice. A bare
webcam match has no liveness guarantee — a photograph held to the lens would pass
it — so it may raise confidence and must never confer permission.

THE ONE JUDGEMENT CALL, stated plainly because it is a real security decision.
"Both must pass" is unsatisfiable when the second signal does not exist: no face
enrolled, camera in use by something else, model missing. Failing CLOSED there
would lock him out of every HIGH-risk tool on his own machine the first time a
webcam driver misbehaved — a denial of service on himself, caused by a feature
that is meant to be optional. So when the signal is UNAVAILABLE the spoken gate
stands alone, exactly as it does today; when the signal is AVAILABLE and says the
face is not his, the action is refused. This never weakens what exists; it can
only add a refusal.

Scope is exactly HIGH-risk tools. Not wake, not presence, not MEDIUM.
"""
from __future__ import annotations

import asyncio
import logging
import time

from config import config
from tools.registry import Risk, Tool, registry

log = logging.getLogger("jarvis.tools.biometric")

SETTLE_FRAMES = 3          # the first frames off a cold camera are dark
MAX_LOOKS = 12


def enabled() -> bool:
    return bool(config.get("biometric", "enabled", default=True))


async def _one_look() -> tuple[str | None, float]:
    """Grab a frame and check it. ("him"|"unknown"|"no_face"|None, score)."""
    from camera import camera
    from vision_identity import identity
    from vision_presence import presence

    frame = await asyncio.to_thread(camera.frame_bgr) if hasattr(camera, "frame_bgr") else None
    if frame is None:
        # camera.frame() hands out JPEG bytes for the HUD; the recogniser needs
        # pixels, so decode the newest frame rather than adding a second capture
        # path that could disagree with what the HUD is showing.
        data = camera.frame()
        if data is None:
            return None, 0.0
        try:
            import cv2
            import numpy as np
            frame = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
        except Exception:
            log.debug("biometric: could not decode a frame", exc_info=True)
            return None, 0.0
    if frame is None:
        return None, 0.0
    small, faces = await asyncio.to_thread(presence.find_faces, frame)
    if small is None:
        return None, 0.0
    return await asyncio.to_thread(identity.check_once, small, faces)


async def face_confirm(timeout_s: float = 8.0) -> dict:
    """Is the person at the camera him? Never raises, never grants alone.

    Returns {"match": bool, "available": bool, "reason": str}. `available` is the
    field that matters to the caller: False means no second opinion was possible,
    and the spoken gate stands alone.
    """
    out = {"match": False, "available": False, "reason": ""}
    if not enabled():
        out["reason"] = "face confirmation is switched off"
        return out
    try:
        from camera import camera
        from vision_identity import identity

        if not identity.enrolled:
            out["reason"] = "no face is enrolled"
            return out

        opened_here = False
        if not camera.is_on:
            res = await asyncio.to_thread(camera.start)
            if not res.get("ok"):
                out["reason"] = "the camera would not open"
                return out
            opened_here = True

        try:
            deadline = time.time() + max(1.0, min(30.0, float(timeout_s)))
            verdict, score, looks = None, 0.0, 0
            while time.time() < deadline and looks < MAX_LOOKS:
                looks += 1
                verdict, score = await _one_look()
                if verdict == "him":
                    out.update(match=True, available=True, score=round(score, 3),
                               reason="recognised")
                    return out
                if verdict == "unknown":
                    # A face that is definitely NOT his is an answer, not a
                    # reason to keep looking until one happens to match.
                    out.update(match=False, available=True, score=round(score, 3),
                               reason="that is not your face")
                    return out
                await asyncio.sleep(0.25)      # no_face / could-not-tell: look again
            if verdict == "no_face":
                out.update(available=True, reason="nobody is at the camera")
            else:
                out["reason"] = "I couldn't get a clear look"
            return out
        finally:
            if opened_here:
                # Leave the camera exactly as it was found. A confirmation must
                # not quietly turn his webcam on and leave it running.
                await asyncio.to_thread(camera.stop)
    except Exception:
        log.exception("face_confirm failed")
        out["reason"] = "the face check failed"
        return out


async def second_signal(tool_name: str) -> tuple[bool, str]:
    """Called by the registry for HIGH-risk tools, after a spoken yes.

    Returns (allow, why). Allows when the signal is unavailable — see the module
    docstring; that is the documented judgement call, not an oversight.
    """
    res = await face_confirm(float(config.get("biometric", "confirm_timeout_s",
                                              default=8.0)))
    if res.get("match"):
        return True, "face confirmed"
    if not res.get("available"):
        log.info("biometric: no second signal for %s (%s) — spoken gate stands alone",
                 tool_name, res.get("reason"))
        return True, f"unavailable: {res.get('reason')}"
    return False, res.get("reason") or "the face did not match"


def register_all() -> None:
    registry.register(Tool(
        name="face_confirm",
        description="Check the webcam for the user's face as an ADDITIONAL signal on a "
                    "high-risk confirmation. Never a substitute for his spoken yes.",
        parameters={"type": "object", "properties": {
            "timeout_s": {"type": "number", "minimum": 1, "maximum": 30}},
            "required": []},
        risk=Risk.SAFE, handler=face_confirm, timeout=40))
