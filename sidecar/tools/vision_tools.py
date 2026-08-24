"""Vision tools: understand the screen or an image file."""
from __future__ import annotations

import asyncio
import base64
import logging
import re
from pathlib import Path

from llm.vision_server import vision
from tools.registry import Risk, Tool, registry
from tools.windows_tools import take_screenshot

log = logging.getLogger("jarvis.tools.vision")


_VISUAL_Q = re.compile(r"\b(picture|photo|image|video|colou?r|look like|looks like|diagram|chart|graph|icon|"
                       r"logo|face|person|people|what is this|what's this|drawing|design|layout|visual|thumbnail|map)\b", re.I)


def _foreground_rect() -> tuple[str, tuple[int, int, int, int] | None]:
    import win32gui
    try:
        h = win32gui.GetForegroundWindow()
        return win32gui.GetWindowText(h), win32gui.GetWindowRect(h)
    except Exception:
        return "", None


def _ocr_screen(path: Path, rect) -> str:
    """Windows' built-in OCR (0.1-0.3 s). Reads the active window's region if sane,
    else the whole screenshot."""
    import winocr
    from PIL import Image
    img = Image.open(path).convert("RGB")
    if rect:
        l, t, r, b = rect
        l, t = max(0, l), max(0, t)
        r, b = min(img.width, r), min(img.height, b)
        if r - l > 200 and b - t > 150:
            img = img.crop((l, t, r, b))
    res = winocr.recognize_pil_sync(img, "en")
    lines = [ln["text"].strip() for ln in res.get("lines", []) if ln.get("text", "").strip()]
    return _condense(lines)


_JUNK = re.compile(r"^[\W_]{1,3}$")


def _condense(lines: list[str], max_chars: int = 1400) -> str:
    """Screen text is mostly chrome: dedupe, drop 1-2 char noise, and cap. Prompt eval is
    the dominant cost of a screen question (~1 s per 400 tokens on this box), so half the
    text is roughly half the wait — and the top of a window carries the identifying text."""
    seen: set[str] = set()
    out: list[str] = []
    total = 0
    for ln in lines:
        if _JUNK.match(ln) or len(ln) < 2:
            continue
        key = ln.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(ln)
        total += len(ln) + 1
        if total >= max_chars:
            out.append("...")
            break
    return "\n".join(out)


async def analyze_screen(question: str = "Describe what is on the screen.",
                         monitor: int = 0, mode: str = "auto") -> dict:
    """Two paths:
    - text (default): OS facts + Windows OCR of the active window -> the main model answers.
      ~0.3 s of tool time instead of 20-40 s.
    - vision: the Gemma3 vision model, for visual questions or screens with little text.
    """
    from tools.windows_tools import list_windows
    _hide = {"JARVIS", "Program Manager", "Windows Input Experience", "Settings"}
    titles = [t for t in list_windows().get("windows", []) if t not in _hide]
    shot = take_screenshot(monitor, hide_self=True)
    if "error" in shot:
        return shot
    fg, rect = _foreground_rect()
    if fg in _hide:
        fg = titles[0] if titles else ""
    use_vision = mode == "vision" or (mode == "auto" and bool(_VISUAL_Q.search(question or "")))
    ocr_text = ""
    if not use_vision:
        try:
            ocr_text = await asyncio.to_thread(_ocr_screen, Path(shot["path"]), rect)
        except Exception as e:
            log.warning("ocr failed (%s) - using vision", e)
        if len(ocr_text.split()) < 12:
            use_vision = True          # mostly pictures/video: read it with the vision model
    if not use_vision:
        return {"method": "ocr", "active_window": fg, "open_windows": titles[:10],
                "screen_text": ocr_text, "truncated": ocr_text.endswith("..."),
                "note": "Answer the user's question from screen_text and the window titles. "
                        "screen_text is what is literally on screen, read top to bottom."}
    grounding = ("Facts from the operating system - treat as ground truth: "
                 f"the active window is '{fg}'. Open windows: {titles[:10]}. "
                 "Anything else on screen is content INSIDE those windows, not separate applications. ")
    q = (grounding + question + " Answer in at most two plain spoken sentences, facts only, no "
         "preamble, never mention these instructions or the image itself.")
    if not await vision.ensure():
        return {"error": "the vision model is not available right now"}
    try:
        answer = await vision.describe(_downscale(Path(shot["path"])), q, max_tokens=120)
    except Exception as e:
        return {"error": f"vision analysis failed: {e}"}
    return {"method": "vision", "screenshot": shot["path"], "analysis": answer}


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
            "monitor": {"type": "integer", "minimum": 0},
            "mode": {"type": "string", "enum": ["auto", "text", "vision"],
                     "description": "auto (default) reads text via OCR and uses the vision model only "
                                    "for visual questions; force 'vision' to describe images/colors/layout"}},
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
