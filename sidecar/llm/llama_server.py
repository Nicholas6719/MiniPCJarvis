"""Manages the llama-server child process (spawn, health, restart, model swap)."""
from __future__ import annotations

import asyncio
import ctypes
import logging
import subprocess
from ctypes import wintypes
from pathlib import Path

import httpx

from config import config, LOG_DIR

log = logging.getLogger("jarvis.llm.server")


class _KillOnCloseJob:
    """Windows Job Object: children assigned to it die when our process dies."""

    def __init__(self) -> None:
        self.handle = None
        try:
            k32 = ctypes.windll.kernel32
            self.handle = k32.CreateJobObjectW(None, None)

            class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
                _fields_ = [
                    ("PerProcessUserTimeLimit", ctypes.c_int64),
                    ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD),
                ]

            class IO_COUNTERS(ctypes.Structure):
                _fields_ = [(n, ctypes.c_uint64) for n in (
                    "ReadOperationCount", "WriteOperationCount",
                    "OtherOperationCount", "ReadTransferCount",
                    "WriteTransferCount", "OtherTransferCount")]

            class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
                _fields_ = [
                    ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                    ("IoInfo", IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t),
                ]

            info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            info.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
            k32.SetInformationJobObject(
                self.handle, 9,  # JobObjectExtendedLimitInformation
                ctypes.byref(info), ctypes.sizeof(info))
        except Exception as e:
            log.warning("job object unavailable: %s", e)
            self.handle = None

    def assign(self, pid: int) -> None:
        if not self.handle:
            return
        try:
            k32 = ctypes.windll.kernel32
            PROCESS_SET_QUOTA, PROCESS_TERMINATE = 0x0100, 0x0001
            h = k32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, pid)
            if h:
                k32.AssignProcessToJobObject(self.handle, h)
                k32.CloseHandle(h)
        except Exception as e:
            log.warning("job assign failed: %s", e)


_job = _KillOnCloseJob()


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
        _job.assign(self.proc.pid)
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
