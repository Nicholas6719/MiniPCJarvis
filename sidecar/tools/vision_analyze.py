"""Describe an object in a photo.

NOT a new pipeline. `tools/vision_tools.analyze_image()` already runs the Gemma 3
4B + mmproj model this needs, already downscales, already handles a missing model.
This is a different PROMPT over that plumbing, aimed at a thing rather than a
screen — and a second input path, so a photo sent to the paired Telegram chat can
be looked at instead of refused.

WHY THE PROMPT IS THE WHOLE FEATURE. Asked to "describe this image", a vision
model narrates a scene: where things sit, what colour the wall is. He wants to
know what the OBJECT is — what it appears to be made of, how it looks built,
anything notable or wrong with it. Same model, same weights, different question.

The honesty rule is inherited from the rest of this system and restated in the
prompt, because a vision model will happily invent a brand name off a blurry
logo: say what is visible, say plainly when something cannot be made out, and do
not guess at text that is not legible. A confident wrong answer about the thing
in his hand is worse than "I can't tell from this angle".
"""
from __future__ import annotations

import logging
from pathlib import Path

from tools.registry import Risk, Tool, registry

log = logging.getLogger("jarvis.tools.vision_analyze")

OBJECT_PROMPT = (
    "Look at the object in this photo and describe it for someone who cannot see it. "
    "Cover, in one short paragraph: what the object is; what it appears to be made of; "
    "how it looks constructed or finished; and anything notable, unusual or damaged. "
    "Describe only what is actually visible. If part of it is out of frame, blurred or "
    "too dark to make out, say so plainly rather than guessing. Do not invent brand "
    "names, model numbers or text you cannot clearly read."
)

MAX_BYTES = 15_000_000
SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".bmp")


async def analyze_object(path: str, question: str = "") -> dict:
    """One photo, one description. Never raises — a vision failure is a sentence."""
    p = Path(str(path or "")).expanduser()
    if not p.exists() or p.suffix.lower() not in SUFFIXES:
        return {"error": f"image not found or unsupported type: {path}"}
    try:
        if p.stat().st_size > MAX_BYTES:
            return {"error": "that image is too large to look at"}
    except OSError as e:
        return {"error": f"could not read that image: {e}"}

    from llm.vision_server import vision
    from tools.vision_tools import _downscale
    if not await vision.ensure():
        return {"error": "the vision model is not available right now"}

    ask = (question or "").strip()
    prompt = f"{OBJECT_PROMPT}\n\nAlso answer specifically: {ask}" if ask else OBJECT_PROMPT
    try:
        img = _downscale(p)
        answer = await vision.describe(img, prompt, max_tokens=260)
    except Exception as e:
        log.exception("object analysis failed")
        return {"error": f"I couldn't make that out: {e}"}
    if not (answer or "").strip():
        return {"error": "the vision model returned nothing"}
    return {"image": str(p), "analysis": answer.strip(),
            "asked": ask or "general description"}


def register_all() -> None:
    registry.register(Tool(
        name="analyze_object",
        description="Look closely at a photo of a THING and describe it — what it is, what "
                    "it appears to be made of, how it is built, anything notable or damaged. "
                    "For the user's screen use analyze_screen; for a general question about "
                    "a picture use analyze_image.",
        parameters={"type": "object", "properties": {
            "path": {"type": "string", "description": "local image file path"},
            "question": {"type": "string", "description": "optional specific question"}},
            "required": ["path"]},
        risk=Risk.SAFE, handler=analyze_object, timeout=180))
