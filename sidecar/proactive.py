"""Proactive intelligence: system observations JARVIS raises on its own.

Design per spec: JARVIS must know when NOT to speak. Every alert passes
suppression — quiet hours, per-alert cooldown, hourly cap, and idle-only
announcement (announce() already refuses to interrupt activity).
"""
from __future__ import annotations

import asyncio
import ctypes
import datetime as dt
import logging
import time

import psutil

from config import config
from events import bus

log = logging.getLogger("jarvis.proactive")

CHECK_INTERVAL_S = 60


class _LastInput(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def user_idle_seconds() -> float:
    """Seconds since last keyboard/mouse input (session-wide)."""
    info = _LastInput()
    info.cbSize = ctypes.sizeof(info)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    millis = ctypes.windll.kernel32.GetTickCount() - info.dwTime
    return millis / 1000.0


class Proactive:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._last_fired: dict[str, float] = {}   # alert key -> ts
        self._hour_window: list[float] = []
        self._active_since: float | None = None   # continuous user activity start
        self.announce = None  # wired to orchestrator.announce
        self._rule_since: dict[str, float] = {}   # rule key -> when its condition started holding

    # ---------- config ----------

    def _cfg(self, key: str, default):
        return config.get("proactive", key, default=default)

    @property
    def enabled(self) -> bool:
        return bool(self._cfg("enabled", True))

    def in_quiet_hours(self, now: dt.datetime | None = None) -> bool:
        now = now or dt.datetime.now()
        try:
            qs = dt.datetime.strptime(str(self._cfg("quiet_start", "22:00")), "%H:%M").time()
            qe = dt.datetime.strptime(str(self._cfg("quiet_end", "08:00")), "%H:%M").time()
        except ValueError:
            return False
        t = now.time()
        if qs <= qe:
            return qs <= t < qe
        return t >= qs or t < qe  # window crosses midnight

    # ---------- suppression ----------

    def _allowed(self, key: str, cooldown_h: float) -> bool:
        if not self.enabled or self.in_quiet_hours():
            return False
        now = time.time()
        if now - self._last_fired.get(key, 0) < cooldown_h * 3600:
            return False
        self._hour_window = [t for t in self._hour_window if now - t < 3600]
        if len(self._hour_window) >= int(self._cfg("max_per_hour", 2)):
            return False
        return True

    async def _fire(self, key: str, text: str, cooldown_h: float = 4.0) -> bool:
        if not self._allowed(key, cooldown_h):
            return False
        now = time.time()
        self._last_fired[key] = now
        self._hour_window.append(now)
        log.info("proactive: %s", key)
        await bus.emit("proactive", alert=key, text=text)
        if self.announce is not None:
            try:
                await self.announce(text)
            except Exception:
                log.exception("proactive announce failed")
        return True

    # ---------- lifecycle ----------

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    def stop(self) -> None:
        if self._task:
            self._task.cancel()

    async def _loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(CHECK_INTERVAL_S)
                await self.run_checks()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("proactive tick failed")

    # ---------- user rules ("tell me if CPU goes above 90 percent") ----------

    METRICS = {
        "cpu": lambda: psutil.cpu_percent(interval=0.2),
        "ram": lambda: psutil.virtual_memory().percent,
        "disk_free_gb": lambda: psutil.disk_usage("C:\\").free / 1e9,
        "battery": lambda: (psutil.sensors_battery().percent if psutil.sensors_battery() else None),
    }
    METRIC_WORDS = {"cpu": "CPU", "ram": "memory", "disk_free_gb": "free disk space", "battery": "the battery"}


def _memory_hogs(n: int = 3) -> str:
    """' - the model server 22 GB, JARVIS 3 GB, Brave 1 GB' - by PRIVATE bytes,
    the number that is actually committed (llama-server on this iGPU shows a
    7 GB working set and 22 GB private)."""
    try:
        by: dict[str, float] = {}
        for p in psutil.process_iter(["name", "memory_info"]):
            try:
                nm = (p.info["name"] or "").lower()
                priv = getattr(p.info["memory_info"], "private", 0) or p.info["memory_info"].rss
            except Exception:
                continue
            label = {"llama-server.exe": "the model server", "jarvis-sidecar.exe": "JARVIS",
                     "brave.exe": "Brave", "chrome.exe": "Chrome", "msedge.exe": "Edge",
                     "memory compression": "memory compression"}.get(nm, nm.removesuffix(".exe"))
            by[label] = by.get(label, 0.0) + priv / 1e9
        top = sorted(by.items(), key=lambda kv: -kv[1])[:n]
        top = [(k, v) for k, v in top if v >= 0.5]
        if not top:
            return ""
        return " - " + ", ".join(f"{k} {v:.0f} GB" if v >= 1 else f"{k} {v * 1000:.0f} MB" for k, v in top)
    except Exception:
        return ""

    def rules(self) -> list[dict]:
        return list(config.get("proactive", "rules", default=[]) or [])

    @staticmethod
    def rule_key(r: dict) -> str:
        return f"{r['metric']}{r['op']}{r['value']}"

    def add_rule(self, rule: dict) -> None:
        rules = [r for r in self.rules() if self.rule_key(r) != self.rule_key(rule)]
        rules.append(rule)
        config.set("proactive", "rules", value=rules)

    def remove_rules(self, metric: str | None) -> int:
        rules = self.rules()
        keep = [r for r in rules if metric and r["metric"] != metric]
        config.set("proactive", "rules", value=keep)
        return len(rules) - len(keep)

    def describe(self, r: dict) -> str:
        unit = {"cpu": " percent", "ram": " percent", "disk_free_gb": " gigabytes", "battery": " percent"}[r["metric"]]
        word = "above" if r["op"] == ">" else "below"
        hold = f" for {int(r['for_min'])} minutes" if r.get("for_min") else ""
        return f"{self.METRIC_WORDS[r['metric']]} is {word} {int(r['value'])}{unit}{hold}"

    async def run_rules(self) -> None:
        now = time.time()
        for r in self.rules():
            try:
                val = self.METRICS[r["metric"]]()
            except Exception:
                continue
            if val is None:
                continue
            holds = val > float(r["value"]) if r["op"] == ">" else val < float(r["value"])
            key = self.rule_key(r)
            if not holds:
                self._rule_since.pop(key, None)
                continue
            since = self._rule_since.setdefault(key, now)
            if now - since < float(r.get("for_min", 0)) * 60:
                continue
            text = r.get("message") or f"Heads up: {self.describe(r)}. It's at {int(val)} right now."
            if await self._fire("rule:" + key, text, cooldown_h=float(r.get("cooldown_h", 1))):
                self._rule_since[key] = now

    # ---------- checks ----------

    async def run_checks(self) -> None:
        await self.run_rules()
        # disk space
        try:
            disk = psutil.disk_usage("C:\\")
            free_gb = disk.free / 1e9
            warn_gb = float(self._cfg("disk_free_gb_warn", 50))
            if free_gb < warn_gb:
                await self._fire(
                    "disk_low",
                    f"A heads up — drive C is down to about "
                    f"{int(free_gb)} gigabytes of free space.",
                    cooldown_h=12)
        except Exception:
            # A check that silently stops running looks exactly like a machine
            # with nothing wrong with it.
            log.debug("disk check failed", exc_info=True)

        # sustained RAM pressure (what is holding it is in the sentence)
        try:
            vm = psutil.virtual_memory()
            if vm.percent >= float(self._cfg("ram_percent_warn", 94)):
                # SAY WHAT IS USING IT. "Memory is running very tight" reached him
                # every two hours for a day with nothing running but JARVIS
                # (2026-10-13); the hog was llama-server's own host prompt cache.
                # A warning that cannot name the culprit sends him looking at
                # the wrong things.
                await self._fire(
                    "ram_high",
                    f"Memory is at {int(vm.percent)} percent{_memory_hogs()}. "
                    "Things may slow down.",
                    cooldown_h=2)
        except Exception:
            log.debug("memory check failed", exc_info=True)

        # continuous work session → break suggestion
        try:
            idle = user_idle_seconds()
            now = time.time()
            if idle < 300:  # active within the last 5 minutes
                if self._active_since is None:
                    self._active_since = now
            else:
                self._active_since = None
            break_after = float(self._cfg("break_after_min", 180)) * 60
            if (self._active_since is not None
                    and now - self._active_since > break_after):
                if await self._fire(
                        "break_time",
                        "You've been at it for about three hours. "
                        "Worth a short break?",
                        cooldown_h=3):
                    self._active_since = now  # reset the clock after suggesting
        except Exception:
            log.debug("work-session check failed", exc_info=True)


proactive = Proactive()
