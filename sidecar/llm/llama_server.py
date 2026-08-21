"""Manages the llama-server child process (spawn, health, restart, model swap)."""
from __future__ import annotations

import asyncio
import logging
import subprocess
from pathlib import Path

import httpx

from config import config, LOG_DIR

log = logging.getLogger("jarvis.llm.server")


class LlamaServer:
    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self.model_name: str | None = None
        self.port: int = config.get("llm", "port", default=8033)
        self.base_url = f"http://127.0.0.1:{self.port}"
        self._starting = asyncio.Lock()

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    async def ensure(self, model_name: str | None = None) -> bool:
        """Ensure llama-server is up and serving `model_name` (default: active model)."""
        model_name = model_name or config.get("llm", "active_model")
        async with self._starting:
            if self.running and self.model_name == model_name:
                return await self.healthy()
            await self.stop()
            return await self._start(model_name)

    async def _start(self, model_name: str) -> bool:
        mcfg = config.get("llm", "models", default={}).get(model_name)
        if not mcfg:
            log.error("unknown model %s", model_name)
            return False
        binary = config.get("llm", "server_binary")
        model_path = mcfg["path"]
        if not Path(binary).exists() or not Path(model_path).exists():
            log.error("missing binary or model: %s / %s", binary, model_path)
            return False
        args = [
            binary, "-m", model_path,
            "-c", str(config.get("llm", "context", default=16384)),
            "--host", "127.0.0.1", "--port", str(self.port),
            "--log-file", str(LOG_DIR / "llama-server.log"),
            *mcfg.get("args", []),
        ]
        log.info("starting llama-server: %s", model_name)
        self.proc = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        self.model_name = model_name
        for _ in range(120):  # model load can take ~10-60s
            await asyncio.sleep(1)
            if not self.running:
                log.error("llama-server exited during startup")
                return False
            if await self.healthy():
                log.info("llama-server ready (%s)", model_name)
                return True
        log.error("llama-server failed to become healthy")
        await self.stop()
        return False

    async def healthy(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=2.0) as c:
                r = await c.get(f"{self.base_url}/health")
                return r.status_code == 200
        except Exception:
            return False

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
        self.model_name = None


llama = LlamaServer()
