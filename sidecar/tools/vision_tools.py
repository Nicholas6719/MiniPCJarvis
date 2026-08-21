"""Vision tools: understand the screen or an image file."""
from __future__ import annotations

import base64
import logging
from pathlib import Path

from llm.vision_server import vision
from tools.registry import Risk, Tool, registry
from tools.windows_tools import take_screenshot

log = logging.getLogger("jarvis.tools.vision")


async def analyze_screen(question: str = "Describe what is on the screen.",
                         monitor: int = 0) -> dict:
    shot = take_screenshot(monitor)
    if "error" in shot:
        return shot
    if not await vision.ensure():
        return {"error": "the vision model is not available right now"}
    img = base64.b64encode(Path(shot["path"]).read_bytes()).decode()
    try:
        answer = await vision.describe(img, question)
    except Exception as e:
        return {"error": f"vision analysis failed: {e}"}
    return {"screenshot": shot["path"], "analysis": answer}


async def analyze_image(path: str, question: str = "Describe this image.") -> dict:
    p = Path(path).expanduser()
    if not p.exists() or p.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp", ".bmp"):
        return {"error": f"image not found or unsupported type: {path}"}
    if p.stat().st_size > 15_000_000:
        return {"error": "image too large"}
    if not await vision.ensure():
        return {"error": "the vision model is not available right now"}
    img = base64.b64encode(p.read_bytes()).decode()
    try:
        answer = await vision.describe(img, question)
    except Exception as e:
        return {"error": f"vision analysis failed: {e}"}
    return {"image": str(p), "analysis": answer}


def register_all() -> None:
    registry.register(Tool(
        name="analyze_screen",
        description="Look at the user's screen and answer a question about it — "
                    "use when asked what's on screen, what's wrong with something "
                    "visible, or to read visible content.",
        parameters={"type": "object", "properties": {
            "question": {"type": "string"},
            "monitor": {"type": "integer", "minimum": 0}},
            "required": []},
        risk=Risk.LOW, handler=analyze_screen, timeout=180))
    registry.register(Tool(
        name="analyze_image",
        description="Analyze an image file and answer a question about it.",
        parameters={"type": "object", "properties": {
            "path": {"type": "string"},
            "question": {"type": "string"}},
            "required": ["path"]},
        risk=Risk.LOW, handler=analyze_image, timeout=180))
