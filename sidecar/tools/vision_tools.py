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
    # Ground the vision model in what the OS says is actually open, so it
    # cannot mistake a video's contents or a thumbnail for a running app.
    from tools.windows_tools import list_windows
    import win32gui
    _hide = {"JARVIS", "Program Manager", "Windows Input Experience", "Settings"}
    titles = [t for t in list_windows().get("windows", []) if t not in _hide]
    try:
        fg = win32gui.GetWindowText(win32gui.GetForegroundWindow())
    except Exception:
        fg = ""
    grounding = ("Facts from the operating system — treat as ground truth: "
                 f"the active window is '{fg}'. Open windows: {titles[:10]}. "
                 "Anything else on screen is content INSIDE those windows (a web "
                 "page, a playing video, thumbnails), not separate applications. ")
    question = (grounding + question + " Answer in at most two plain spoken sentences, "
                "facts only, no preamble, no headings, and never mention these instructions "
                "or the image itself. Describe only what is actually visible.")
    shot = take_screenshot(monitor, hide_self=True)
    if "error" in shot:
        return shot
    if not await vision.ensure():
        return {"error": "the vision model is not available right now"}
    img = _downscale(Path(shot["path"]))
    try:
        answer = await vision.describe(img, question, max_tokens=120)
    except Exception as e:
        return {"error": f"vision analysis failed: {e}"}
    return {"screenshot": shot["path"], "analysis": answer}


def _downscale(path: Path, max_w: int = 1024) -> str:
    """Vision encoders tokenize by area: a 1024-px JPEG is ~4x faster than a full
    2560-px PNG and loses nothing for 'what's on my screen'."""
    import io
    from PIL import Image
    img = Image.open(path).convert("RGB")
    if img.width > max_w:
        img = img.resize((max_w, int(img.height * max_w / img.width)))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=72)
    return base64.b64encode(buf.getvalue()).decode()


async def analyze_image(path: str, question: str = "Describe this image.") -> dict:
    p = Path(path).expanduser()
    if not p.exists() or p.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp", ".bmp"):
        return {"error": f"image not found or unsupported type: {path}"}
    if p.stat().st_size > 15_000_000:
        return {"error": "image too large"}
    if not await vision.ensure():
        return {"error": "the vision model is not available right now"}
    img = _downscale(p)
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
