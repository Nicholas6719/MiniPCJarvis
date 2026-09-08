"""Making him a picture, on his own machine.

`show_images` searches the web; this DRAWS one. Same contract as a render,
because it costs about the same: an estimate, a question above the threshold,
the work in the background so he can keep talking, and the result announced
through `delivery` (spoken if he is here, sent to his phone if he is not).

Measured cold on the 780M, 2026-09-08: 19.5 s end to end at 512x512. That is
over `fabrication.ask_above_seconds`, so it asks first — for the same reason
a render does. He may not want to spend twenty seconds.
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path

import imagegen
import render_estimates as est
from render_queue import queue
from tools.registry import Risk, Tool, registry

log = logging.getLogger("jarvis.tools.image")

# Its own clock, like the detailed render's. Filed under 9 so a picture can
# never become the estimate for a 3D model or the other way round.
EST_KEY = 9

_SIZES = {"square": (512, 512), "portrait": (512, 704), "landscape": (704, 512),
          "wide": (768, 448), "tall": (448, 768)}

_STRIP = re.compile(
    r"^(?:please\s+)?(?:can you\s+|could you\s+|i want you to\s+|i'd like you to\s+)?"
    r"(?:make|draw|create|generate|paint|render|do|give me|show me)\s+"
    r"(?:me\s+)?(?:an?|the|some)?\s*"
    r"(?:picture|image|drawing|painting|illustration|artwork|art)?\s*"
    r"(?:of|showing|with)?\s*", re.I)


def _subject(description: str) -> str:
    """What the picture is OF, for the file name and for the sentence."""
    d = _STRIP.sub("", (description or "").strip()).strip(" .,")
    return d or (description or "").strip()


def _safe(name: str) -> str:
    out = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "", (name or "").strip())
    return re.sub(r"\s+", " ", out)[:60].strip(". ") or "picture"


def _where(name: str) -> Path:
    """A new file in his Pictures folder, never over an existing one."""
    from tools.file_tools import _resolve
    base = _safe(name)
    p = _resolve(f"pictures/{base}.png", must_exist=False)
    if p is None:
        from config import APP_DIR
        p = APP_DIR / "pictures" / f"{base}.png"
    n = 2
    while p.exists():
        p = p.with_name(f"{base} ({n}).png")
        n += 1
    return p


async def generate_image(description: str = "", name: str = "", size: str = "square",
                         steps: int = 0, confirmed: bool = False) -> dict:
    """Draw a picture from a description and save it."""
    subject = _subject(description)
    if not subject:
        return {"error": "what should I make a picture of, sir?"}
    if not imagegen.available():
        return {"error": imagegen.missing(), "unavailable": True,
                "instruction": "Say plainly that you cannot draw pictures on this "
                               "machine yet. Do not offer to search the web instead "
                               "unless he asks."}

    seconds = est.estimate(EST_KEY)
    if not confirmed and seconds > est.ask_threshold():
        return {"_ask": {
            "subject": subject,
            "question": (f"That's {est.spoken(seconds)}{est.confidence_note(EST_KEY)}, "
                         f"sir. Shall I?"),
            "tool": "generate_image",
            "args": {"description": description, "name": name, "size": size,
                     "steps": steps, "confirmed": True},
        }}

    w, h = _SIZES.get((size or "square").strip().lower(), _SIZES["square"])
    out = _where(name or subject)
    label = subject[:48]

    async def job() -> dict:
        from events import bus
        r = await imagegen.generate(subject, out, width=w, height=h,
                                    steps=int(steps) or imagegen.STEPS)
        if r.get("error"):
            return r
        # ON SCREEN THE MOMENT IT EXISTS. The render stage already knows how to
        # show one picture full-frame, and that is exactly what this is.
        try:
            import base64
            blob = Path(r["path"]).read_bytes()
            if len(blob) < 12_000_000:
                await bus.emit("render_preview", label=f"picture · {label}",
                               image="data:image/png;base64," + base64.b64encode(blob).decode())
        except Exception:
            log.debug("could not put the picture on the stage", exc_info=True)
        r["image"] = r["path"]
        r["spoken_size"] = f"{w} by {h}"
        return r

    sub = queue.submit(EST_KEY, f"picture of {label}", job, estimate_key=EST_KEY)
    if sub.get("error"):
        return sub
    behind = sub.get("queued_behind") or 0
    return {"started": True, "label": label, "path": str(out), **sub,
            "spoken": (f"Drawing it now, sir — {sub['estimate_spoken']}."
                       if not behind else
                       f"It's queued behind {behind} other{'s' if behind > 1 else ''}, sir."),
            "instruction": "Say it is being drawn and roughly how long. It will "
                           "announce itself when it is done; do not promise to "
                           "describe it."}


def register_all() -> None:
    registry.register(Tool(
        name="generate_image",
        description="DRAW a new picture from a description and save it to his "
                    "Pictures folder — an illustration, a diagram, a poster, "
                    "artwork, a picture of something imagined. This makes a "
                    "new image; `show_images` searches the web for existing "
                    "ones. It runs in the background and announces itself.",
        parameters={"type": "object", "properties": {
            "description": {"type": "string", "description": "what the picture shows"},
            "name": {"type": "string", "description": "the file name, optional"},
            "size": {"type": "string",
                     "enum": ["square", "portrait", "landscape", "wide", "tall"]},
            "confirmed": {"type": "boolean",
                          "description": "he has agreed to spend the time"}},
            "required": ["description"]},
        risk=Risk.LOW, handler=generate_image, timeout=60))
