"""Describe an object in a photo. STUB — Phase 3 fills this in.

NOT a new pipeline. tools/vision_tools.analyze_image() already runs the Gemma 3 4B
+ mmproj model this needs and is already registered; this is a different PROMPT
over the same plumbing, aimed at an object rather than a screen: material, likely
composition, notable features.

Two input paths, and the second is why Phase 3 waits for Phase 2: a local file,
or a photo sent to the paired Telegram chat — which lands in the same poller
branch that Phase 2 edits, so the poller is touched once rather than twice.
"""
from __future__ import annotations

import logging

from tools.registry import Risk, Tool, registry

log = logging.getLogger("jarvis.tools.vision_analyze")


async def analyze_object(path: str, question: str = "") -> dict:
    raise NotImplementedError("vision_analyze: Phase 3")


def register_all() -> None:
    registry.register(Tool(
        name="analyze_object",
        description="Look closely at a photo of a THING and describe it — what it is, what "
                    "it appears to be made of, notable features. For the user's screen use "
                    "analyze_screen; for a general image question use analyze_image.",
        parameters={"type": "object", "properties": {
            "path": {"type": "string", "description": "local image file path"},
            "question": {"type": "string", "description": "optional specific question"}},
            "required": ["path"]},
        risk=Risk.SAFE, handler=analyze_object, timeout=180))
