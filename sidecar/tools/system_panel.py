"""SYSTEM view data: everything the user would otherwise open Windows Settings for."""
from __future__ import annotations

import re
import subprocess
import time

import psutil

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


_wifi_cache: tuple[float, dict] = (0.0, {})


def _wifi() -> dict:
    global _wifi_cache
    ts, cached = _wifi_cache
    if time.time() - ts < 15.0 and cached:
        return cached
    return _wifi_uncached()


def _wifi_uncached() -> dict:
    global _wifi_cache
    try:
        out = subprocess.run(["netsh", "wlan", "show", "interfaces"], capture_output=True, text=True,
                             timeout=5, creationflags=_NO_WINDOW).stdout
        ssid = re.search(r"^\s*SSID\s*:\s*(.+)$", out, re.M)
        sig = re.search(r"^\s*Signal\s*:\s*(\d+)%", out, re.M)
        state = re.search(r"^\s*State\s*:\s*(.+)$", out, re.M)
        out = {"ssid": ssid.group(1).strip() if ssid else None,
               "signal": int(sig.group(1)) if sig else None,
               "state": state.group(1).strip() if state else "unknown"}
    except Exception:
        out = {"ssid": None, "signal": None, "state": "unknown"}
    _wifi_cache = (time.time(), out)
    return out


def _ip() -> str | None:
    try:
        stats = psutil.net_if_stats()          # once, not once per interface
        for name, addrs in psutil.net_if_addrs().items():
            st = stats.get(name)
            if st and st.isup and "Loopback" not in name:
                for a in addrs:
                    if a.family.name == "AF_INET" and not a.address.startswith(("127.", "169.254.")):
                        return a.address
    except Exception:
        pass
    return None


_proc_cache: tuple[float, list[dict]] = (0.0, [])


def _top_processes(limit: int = 8, ttl: float = 20.0) -> list[dict]:
    """Top processes by memory. Cached: walking ~260 processes was 64% of the sidecar's
    CPU while the SYSTEM tab was open (measured with py-spy). Memory info is cheap;
    per-process cpu_percent is what cost the most, so it is no longer collected."""
    global _proc_cache
    ts, cached = _proc_cache
    if time.time() - ts < ttl and cached:
        return cached
    rows = []
    for p in psutil.process_iter(["name", "memory_info"]):
        try:
            mi = p.info["memory_info"]
            if mi:
                rows.append({"name": p.info["name"], "mem_mb": round(mi.rss / 1e6)})
        except Exception:
            continue
    rows.sort(key=lambda x: -x["mem_mb"])
    _proc_cache = (time.time(), rows[:limit])
    return _proc_cache[1]


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
        "processes": _top_processes(),
    }
