"""Manages the llama-server child process (spawn, health, restart, model swap)."""
from __future__ import annotations

import asyncio
import ctypes
import logging
import os
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


def _pid_on_port(port: int) -> int | None:
    try:
        import psutil
        for c in psutil.net_connections(kind="tcp"):
            if c.laddr and c.laddr.port == port and c.status == "LISTEN":
                return c.pid
    except Exception:
        pass
    return None


def _parent_alive(pid: int | None) -> bool:
    """True if the process has a living, non-system parent (someone manages it)."""
    if not pid:
        return True  # unknown — be conservative, treat as owned
    try:
        import psutil
        parent = psutil.Process(pid).parent()
        return parent is not None and parent.is_running() and parent.pid > 4
    except Exception:
        return True


class LlamaServer:
    def __init__(self) -> None:
        import secrets as _secrets
        import socket
        self.proc: subprocess.Popen | None = None
        self.model_name: str | None = None
        # dynamic port per session — a fixed port let an orphaned server from a
        # previous session answer our health checks with the wrong API key
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.external = False  # adopted server owned by another app (e.g. Houston)
        self.adopted_pid: int | None = None
        # How many slots the server has (from /props). The conversation owns
        # slot 0; every side call (fact classifier, night school, newsroom,
        # part generation) goes to slot 1 when there is one, so its prompt
        # never evicts the conversation's cached prefix. Measured 2026-09-04:
        # one shared slot meant the turn after a classified answer re-read
        # the whole ~6k-token prompt, 20+ s.
        self.n_slots = 1
        # per-session API key so other local processes can't use our server
        self.api_key: str | None = _secrets.token_hex(16)
        self._starting = asyncio.Lock()

    @property
    def running(self) -> bool:
        if self.external:
            return True
        if self.proc is None and self.adopted_pid:
            import psutil
            return psutil.pid_exists(self.adopted_pid)
        return self.proc is not None and self.proc.poll() is None

    async def _try_adopt(self, model_name: str) -> bool:
        """This machine runs other assistants (Houston on :8080) that may already
        be serving the exact same GGUF. Reuse instead of loading a duplicate 11GB."""
        mcfg = config.get("llm", "models", default={}).get(model_name)
        if not mcfg:
            return False
        want = str(mcfg["path"]).lower()
        for port in config.get("llm", "adopt_ports", default=[8080]):
            try:
                async with httpx.AsyncClient(timeout=2.0) as c:
                    r = await c.get(f"http://127.0.0.1:{port}/v1/models")
                    if r.status_code != 200:
                        continue
                    ids = [str(m.get("id", "")).lower() for m in r.json().get("data", [])]
                    if any(want == i or want in i for i in ids):
                        self.base_url = f"http://127.0.0.1:{port}"
                        self.model_name = model_name
                        self.api_key = None  # shared servers are unauthenticated
                        self.proc = None
                        # Is anyone still managing it? If its parent is dead it's
                        # an orphan — take ownership so we shut it down on exit.
                        self.adopted_pid = _pid_on_port(port)
                        owner_alive = _parent_alive(self.adopted_pid)
                        self.external = owner_alive
                        if not owner_alive and self.adopted_pid:
                            # bind the orphan to our job object: it dies with us
                            # even if we're hard-killed
                            _job.assign(self.adopted_pid)
                        log.info("adopted %s llama-server on :%s for %s (pid %s)",
                                 "shared" if owner_alive else "orphaned",
                                 port, model_name, self.adopted_pid)
                        return True
            except Exception:
                continue
        return False

    async def ensure(self, model_name: str | None = None) -> bool:
        """Ensure llama-server is up and serving `model_name` (default: active model)."""
        model_name = model_name or config.get("llm", "active_model")
        async with self._starting:
            if self.running and self.model_name == model_name:
                if await self.healthy():
                    return True
                # adopted server vanished — fall through to respawn our own
                self.external = False
                self.model_name = None
            if await self._try_adopt(model_name):
                return True
            await self.stop()
            return await self._start(model_name)

    async def _start(self, model_name: str) -> bool:
        if not self.api_key:
            import secrets as _secrets
            self.api_key = _secrets.token_hex(16)
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
            "-c", str(mcfg.get("context") or config.get("llm", "context", default=16384)),
            "--host", "127.0.0.1", "--port", str(self.port),
            "--log-file", str(LOG_DIR / "llama-server.log"),
            *mcfg.get("args", []),
        ]
        if mcfg.get("mmproj") and Path(mcfg["mmproj"]).exists():
            args += ["--mmproj", mcfg["mmproj"]]
        log.info("starting llama-server: %s", model_name)
        # the API key goes in the child's ENVIRONMENT, never on its command line:
        # any local process can read another process's argv (WMI/Win32_Process).
        env = {**os.environ, "LLAMA_API_KEY": self.api_key}
        self.proc = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
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
                await self.learn_slots()
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

    async def learn_slots(self) -> int:
        """Ask the server how many slots it has; 1 when it will not say."""
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        try:
            async with httpx.AsyncClient(timeout=3.0, headers=headers) as c:
                r = await c.get(f"{self.base_url}/props")
                n = int((r.json() or {}).get("total_slots") or 1) if r.status_code == 200 else 1
        except Exception:
            n = 1
        self.n_slots = max(1, n)
        log.info("llama-server has %d slot(s); side calls use slot %d",
                 self.n_slots, 1 if self.n_slots > 1 else 0)
        return self.n_slots

    async def stop(self) -> None:
        if self.external:
            # never kill a server another application owns
            self.external = False
            self.model_name = None
            self.base_url = f"http://127.0.0.1:{self.port}"
            return
        if self.proc is None and getattr(self, "adopted_pid", None):
            # orphan we took ownership of
            try:
                import psutil
                psutil.Process(self.adopted_pid).kill()
                log.info("stopped adopted orphan llama-server pid %s", self.adopted_pid)
            except Exception:
                pass
            self.adopted_pid = None
            self.model_name = None
            return
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
