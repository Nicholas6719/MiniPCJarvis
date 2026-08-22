"""On-demand vision model server: Gemma3-4B + mmproj on a second llama-server.

Started lazily on the first vision request (fast: ~3GB, Vulkan-friendly),
auto-stopped after idle timeout to hand RAM back.
"""
from __future__ import annotations

import asyncio
import logging
import subprocess
import time
from pathlib import Path

import httpx

from config import config, LOG_DIR
from llm.llama_server import _job

log = logging.getLogger("jarvis.vision")

VISION_PORT = 8034
IDLE_STOP_S = 300  # stop after 5 min unused


class VisionServer:
    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self.base_url = f"http://127.0.0.1:{VISION_PORT}"
        self.last_used = 0.0
        self._lock = asyncio.Lock()
        self._reaper: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    async def ensure(self) -> bool:
        async with self._lock:
            self.last_used = time.time()
            if self.running:
                return True
            binary = config.get("llm", "server_binary")
            model = config.get("vision", "model",
                               default=r"C:\AI\models\gemma-3-4b-it-q4_0.gguf")
            mmproj = config.get("vision", "mmproj",
                                default=r"C:\AI\models\gemma-3-4b-it-mmproj.gguf")
            if not (Path(model).exists() and Path(mmproj).exists()):
                log.error("vision model files missing")
                return False
            active = config.get("llm", "models", default={}).get(config.get("llm", "active_model"), {})
            on_cpu = bool(active.get("gpu_full")) or config.get("vision", "device", default="auto") == "cpu"
            device_args = ["--device", "none", "-ngl", "0"] if on_cpu else ["-ngl", "999"]
            log.info("starting vision server (gemma3-4b, %s)", "cpu" if on_cpu else "gpu")
            self.proc = subprocess.Popen(
                [binary, "-m", model, "--mmproj", mmproj,
                 *device_args, "-t", "8", "-fa", "on", "-c", "8192",
                 "--host", "127.0.0.1", "--port", str(VISION_PORT),
                 "--log-file", str(LOG_DIR / "vision-server.log")],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW)
            _job.assign(self.proc.pid)
            for _ in range(90):
                await asyncio.sleep(1)
                if not self.running:
                    log.error("vision server exited during startup")
                    return False
                try:
                    async with httpx.AsyncClient(timeout=2) as c:
                        if (await c.get(f"{self.base_url}/health")).status_code == 200:
                            if self._reaper is None or self._reaper.done():
                                self._reaper = asyncio.create_task(self._reap_idle())
                            return True
                except Exception:
                    pass
            await self.stop()
            return False

    async def _reap_idle(self) -> None:
        while self.running:
            await asyncio.sleep(30)
            if time.time() - self.last_used > IDLE_STOP_S:
                log.info("vision server idle — stopping to free RAM")
                await self.stop()
                return

    async def stop(self) -> None:
        if self.proc is not None:
            try:
                self.proc.terminate()
                await asyncio.to_thread(self.proc.wait, 10)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
        self.proc = None

    async def describe(self, image_b64: str, question: str,
                       max_tokens: int = 400) -> str:
        self.last_used = time.time()
        body = {
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
                    {"type": "text", "text": question},
                ],
            }],
            "max_tokens": max_tokens,
        }
        async with httpx.AsyncClient(timeout=180) as c:
            r = await c.post(f"{self.base_url}/v1/chat/completions", json=body)
            r.raise_for_status()
            data = r.json()
        return (data["choices"][0]["message"].get("content") or "").strip()


vision = VisionServer()
