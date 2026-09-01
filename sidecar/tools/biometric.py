"""Face as a SECOND signal on HIGH-risk confirmations. STUB — Phase 4.

Built on the SFace embedder already in vision_identity.py rather than adding
insightface: that model is already bundled in the spec, already gated by 22
offline tests, already stores embeddings and never images, and adding a second
face stack would mean a Cython build, a ~300 MB model download at RUNTIME (which
breaks both the offline guarantee and the bundle-the-models pattern every other
model here follows), and an opencv dependency that risks the exact fight mediapipe
already caused.

THE SECURITY PROPERTY, and it is a property of the code and not of this comment:
face_confirm() is ADDITIVE. The spoken yes/no gate on HIGH-risk tools stays
mandatory, there is no configuration that disables it, and Phase 4 ships the test
that proves a HIGH-risk call still blocks when the face matches but the voice has
not answered. A bare webcam match has no liveness guarantee — a photograph held to
the lens would pass it — so it may raise confidence and must never grant
permission on its own.

Scope is exactly HIGH-risk tools. Not wake, not presence, not any other tier.
"""
from __future__ import annotations

import logging

from tools.registry import Risk, Tool, registry

log = logging.getLogger("jarvis.tools.biometric")


async def face_confirm(timeout_s: float = 8.0) -> dict:
    """-> {"match": bool, "reason": str}. Never raises, never grants alone."""
    raise NotImplementedError("biometric: Phase 4")


def register_all() -> None:
    registry.register(Tool(
        name="face_confirm",
        description="Check the webcam for the user's face as an ADDITIONAL signal on a "
                    "high-risk confirmation. Never a substitute for his spoken yes.",
        parameters={"type": "object", "properties": {
            "timeout_s": {"type": "number", "minimum": 1, "maximum": 30}},
            "required": []},
        risk=Risk.SAFE, handler=face_confirm, timeout=40))
