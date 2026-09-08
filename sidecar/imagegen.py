"""Making a picture, locally: stable-diffusion.cpp on the 780M over Vulkan.

The same shape as `llm.server_binary` and `C:\\AI\\model3d`: a binary outside
the repo, driven as a subprocess, so nothing heavy joins a bundle that is
already 980 MB. The 780M runs llama.cpp's Vulkan backend already, so this
needs no CUDA, no torch and no Python environment of its own.

SD-Turbo rather than SDXL-Turbo: 512x512 in four steps against SDXL's near
seven gigabytes and much slower sampling. Measured here on 2026-09-08, first
try, cold: sampling 3.6 s, VAE decode 7.0 s, 12.0 s of generation and 19.5 s
end to end including the model load. That is above the cost threshold, so a
picture is asked about exactly as a render is.

Not installed is a first-class answer: `available()` is false and the tool
says what is missing rather than failing somewhere deeper.
"""
from __future__ import annotations

import asyncio
import logging
import re
import subprocess
import time
from pathlib import Path

from config import config

log = logging.getLogger("jarvis.imagegen")

DEFAULT_DIR = Path(r"C:\AI\imagegen")
TIMEOUT_S = 300.0
# Four steps is where SD-Turbo stops improving; one is grainy, eight is twice
# the wait for nothing. Measured on the apple probe, 2026-09-08.
STEPS = 4
CFG = 1.0                      # turbo models are trained for no guidance
MAX_SIDE = 768                 # beyond this the VAE decode dominates on an iGPU


def _dir() -> Path:
    return Path(str(config.get("imagegen", "dir", default=str(DEFAULT_DIR))))


def binary() -> Path:
    return _dir() / "sd-cli.exe"


def model() -> Path:
    return Path(str(config.get("imagegen", "model",
                               default=str(_dir() / "sd_turbo.safetensors"))))


def available() -> bool:
    try:
        return binary().exists() and model().exists()
    except Exception:
        return False


def missing() -> str:
    """What to say when it is not installed."""
    if not binary().exists():
        return f"the image generator isn't installed — I'd need it at {binary()}"
    if not model().exists():
        return f"the image model isn't there — I'd need it at {model()}"
    return ""


def _clean(prompt: str) -> str:
    """His words, without anything that would confuse a command line."""
    p = re.sub(r"[\r\n\t]+", " ", (prompt or "")).strip()
    return re.sub(r"\s{2,}", " ", p)[:700]


async def generate(prompt: str, out: Path, *, width: int = 512, height: int = 512,
                   steps: int = STEPS, seed: int | None = None,
                   negative: str = "") -> dict:
    """Make one picture. Returns {path, seconds, seed} or {error}."""
    if not available():
        return {"error": missing(), "unavailable": True}
    prompt = _clean(prompt)
    if not prompt:
        return {"error": "what should I make a picture of, sir?"}
    width = max(256, min(MAX_SIDE, int(width) // 64 * 64 or 512))
    height = max(256, min(MAX_SIDE, int(height) // 64 * 64 or 512))
    if seed is None:
        seed = int(time.time() * 1000) % 2_147_483_647
    out.parent.mkdir(parents=True, exist_ok=True)
    args = [str(binary()), "-m", str(model()), "-p", prompt,
            "-o", str(out), "--steps", str(max(1, min(20, int(steps)))),
            "--cfg-scale", str(CFG), "-W", str(width), "-H", str(height),
            "--sampling-method", "euler_a", "--seed", str(seed)]
    if negative.strip():
        args += ["-n", _clean(negative)]
    t0 = time.time()
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, cwd=str(_dir()),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW)
    except Exception as e:
        log.exception("could not start the image generator")
        return {"error": f"I couldn't start the image generator: {e}"}
    try:
        raw, _ = await asyncio.wait_for(proc.communicate(), timeout=TIMEOUT_S)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return {"error": f"the picture took longer than {int(TIMEOUT_S)} seconds, sir"}
    took = time.time() - t0
    text = (raw or b"").decode("utf-8", "replace")
    if proc.returncode != 0 or not out.exists():
        tail = " ".join(text.strip().split()[-40:])
        log.warning("image generation failed (rc=%s): %s", proc.returncode, tail[:400])
        return {"error": f"the picture didn't come out: {tail[-200:]}"}
    log.info("image generated in %.1fs: %s", took, out.name)
    return {"path": str(out), "seconds": round(took, 1), "seed": seed,
            "width": width, "height": height, "steps": steps}
