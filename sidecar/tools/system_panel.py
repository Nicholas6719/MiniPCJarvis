"""SYSTEM view data: everything the user would otherwise open Windows Settings for."""
from __future__ import annotations

import re
import subprocess
import time

import psutil

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _wifi() -> dict:
    try:
        out = subprocess.run(["netsh", "wlan", "show", "interfaces"], capture_output=True, text=True,
                             timeout=5, creationflags=_NO_WINDOW).stdout
        ssid = re.search(r"^\s*SSID\s*:\s*(.+)$", out, re.M)
        sig = re.search(r"^\s*Signal\s*:\s*(\d+)%", out, re.M)
        state = re.search(r"^\s*State\s*:\s*(.+)$", out, re.M)
        return {"ssid": ssid.group(1).strip() if ssid else None,
                "signal": int(sig.group(1)) if sig else None,
                "state": state.group(1).strip() if state else "unknown"}
    except Exception:
        return {"ssid": None, "signal": None, "state": "unknown"}


def _ip() -> str | None:
    try:
        for name, addrs in psutil.net_if_addrs().items():
            if psutil.net_if_stats().get(name) and psutil.net_if_stats()[name].isup and "Loopback" not in name:
                for a in addrs:
                    if a.family.name == "AF_INET" and not a.address.startswith(("127.", "169.254.")):
                        return a.address
    except Exception:
        pass
    return None


def snapshot() -> dict:
    from tools.builtin import get_system_stats
    from tools.windows_tools import get_volume
    stats = get_system_stats()
    try:
        vol = get_volume()
    except Exception:
        vol = {"volume_percent": None, "muted": None}
    try:
        from audio.io import mic
        mic_name = getattr(mic, "device_name", None) or getattr(mic, "name", None)
    except Exception:
        mic_name = None
    net = _wifi()
    io = psutil.net_io_counters()
    return {
        "stats": stats,
        "volume": vol,
        "mic": mic_name,
        "battery": stats.get("battery"),
        "network": {**net, "ip": _ip(), "sent_mb": round(io.bytes_sent / 1e6), "recv_mb": round(io.bytes_recv / 1e6)},
        "uptime_s": int(time.time() - psutil.boot_time()),
        "processes": sorted(
            ({"name": p.info["name"], "cpu": p.info["cpu_percent"], "mem_mb": round((p.info["memory_info"].rss if p.info["memory_info"] else 0) / 1e6)}
             for p in psutil.process_iter(["name", "cpu_percent", "memory_info"])),
            key=lambda x: -(x["mem_mb"] or 0))[:8],
    }
