"""Phase 1 built-in tools: system stats, open/close app, web search, read file."""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

import httpx
import psutil

from config import config, secrets
from tools.registry import Risk, Tool, registry

log = logging.getLogger("jarvis.tools.builtin")

_HOME = Path.home()
_ALLOWED_READ_ROOTS = [
    _HOME / "Documents", _HOME / "Downloads", _HOME / "Desktop", _HOME / "Pictures",
]

# Friendly-name launch table; anything else falls through to PATH lookup.
_APP_ALIASES = {
    "chrome": "chrome.exe",
    "google chrome": "chrome.exe",
    "edge": "msedge.exe",
    "notepad": "notepad.exe",
    "calculator": "calc.exe",
    "explorer": "explorer.exe",
    "file explorer": "explorer.exe",
    "spotify": "spotify.exe",
    "vs code": "code",
    "vscode": "code",
    "code": "code",
    "terminal": "wt.exe",
    "settings": "ms-settings:",
    "steam": "steam.exe",
    "task manager": "taskmgr.exe",
}


def get_system_stats() -> dict:
    vm = psutil.virtual_memory()
    disk = psutil.disk_usage("C:\\")
    batt = None
    try:
        b = psutil.sensors_battery()
        if b:
            batt = {"percent": b.percent, "plugged": b.power_plugged}
    except Exception:
        pass
    return {
        "cpu_percent": psutil.cpu_percent(interval=0.3),
        "ram_used_gb": round(vm.used / 1e9, 1),
        "ram_total_gb": round(vm.total / 1e9, 1),
        "ram_percent": vm.percent,
        "disk_c_free_gb": round(disk.free / 1e9, 1),
        "disk_c_percent": disk.percent,
        "battery": batt,
        "process_count": len(psutil.pids()),
    }


def open_application(name: str) -> dict:
    key = name.strip().lower()
    target = _APP_ALIASES.get(key, key)
    try:
        if target.startswith("ms-settings"):
            os.startfile(target)
        else:
            subprocess.Popen(
                f'start "" "{target}"', shell=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"launched": name}
    except Exception as e:
        return {"error": f"could not launch {name}: {e}"}


def close_application(name: str) -> dict:
    key = name.strip().lower().removesuffix(".exe")
    exe = _APP_ALIASES.get(key, key + ".exe").removesuffix(".exe") + ".exe"
    killed = 0
    for p in psutil.process_iter(["name"]):
        try:
            if p.info["name"] and p.info["name"].lower() == exe.lower():
                p.terminate()
                killed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    if killed == 0:
        return {"error": f"no running process matched {name}"}
    return {"closed": name, "processes": killed}


async def _ddg_search(query: str, count: int) -> dict:
    """Keyless search via DuckDuckGo's HTML endpoint (no API, no account)."""
    from lxml import html as _html
    headers = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/126.0.0.0 Safari/537.36")}
    async with httpx.AsyncClient(timeout=12, follow_redirects=True,
                                 headers=headers, http2=True) as c:
        r = await c.post("https://html.duckduckgo.com/html/", data={"q": query})
        r.raise_for_status()
    doc = _html.fromstring(r.text)
    results = []
    for res in doc.cssselect("div.result"):
        a = res.cssselect("a.result__a")
        sn = res.cssselect(".result__snippet")
        if not a:
            continue
        href = a[0].get("href", "")
        # DDG wraps links: //duckduckgo.com/l/?uddg=<url>&...
        if "uddg=" in href:
            from urllib.parse import parse_qs, unquote, urlparse
            href = unquote(parse_qs(urlparse(href).query).get("uddg", [href])[0])
        results.append({"title": a[0].text_content().strip(), "url": href,
                        "snippet": sn[0].text_content().strip() if sn else ""})
        if len(results) >= count:
            break
    return {"query": query, "results": results, "provider": "duckduckgo"}


async def web_search(query: str, count: int = 5) -> dict:
    key = secrets.get("brave_api_key")
    from events import bus
    await bus.emit("web", stage="searching", query=query)
    if not key:
        # No key needed: drive the user's installed Brave browser (hidden).
        from search_brave_web import brave_web
        if brave_web.available:
            try:
                results = await brave_web.search(query, count)
                if results:
                    await bus.emit("web", stage="results", query=query, results=results)
                    return {"query": query, "results": results, "provider": "brave-browser"}
                await bus.emit("web", stage="empty", query=query)
            except Exception as e:
                log.warning("brave browser search failed: %s", e)
                await bus.emit("web", stage="error", query=query, error=str(e)[:120])
        try:
            return await _ddg_search(query, count)
        except Exception as e:
            return {"error": f"web search failed: {e}"}
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": min(count, 10)},
            headers={"X-Subscription-Token": key, "Accept": "application/json"},
        )
        r.raise_for_status()
        data = r.json()
    results = [
        {"title": w.get("title"), "url": w.get("url"),
         "snippet": w.get("description")}
        for w in (data.get("web", {}).get("results") or [])[:count]
    ]
    return {"query": query, "results": results}


def read_file(path: str, max_chars: int = 4000) -> dict:
    p = Path(path).expanduser()
    if not p.is_absolute():
        for root in _ALLOWED_READ_ROOTS:
            cand = root / path
            if cand.exists():
                p = cand
                break
    try:
        p = p.resolve()
    except OSError:
        return {"error": "invalid path"}
    if not any(str(p).lower().startswith(str(r.resolve()).lower()) for r in _ALLOWED_READ_ROOTS):
        return {"error": f"reading outside allowed folders (Documents/Downloads/Desktop/Pictures) "
                         f"is not permitted: {p}"}
    if not p.exists() or not p.is_file():
        return {"error": f"file not found: {p}"}
    if p.stat().st_size > 5_000_000:
        return {"error": "file too large to read directly"}
    try:
        text = p.read_text("utf-8", errors="replace")
    except Exception as e:
        return {"error": f"could not read file: {e}"}
    truncated = len(text) > max_chars
    return {"path": str(p), "truncated": truncated, "content": text[:max_chars]}


def register_all() -> None:
    registry.register(Tool(
        name="get_system_stats",
        description="Get current CPU, RAM, disk, battery, and process stats for this PC.",
        parameters={"type": "object", "properties": {}, "required": []},
        risk=Risk.SAFE, handler=get_system_stats))
    registry.register(Tool(
        name="open_application",
        description="Launch an application on this PC by name, e.g. 'chrome', 'spotify', 'notepad'.",
        parameters={"type": "object", "properties": {
            "name": {"type": "string", "description": "Application name"}},
            "required": ["name"]},
        risk=Risk.LOW, handler=open_application))
    registry.register(Tool(
        name="close_application",
        description="Close a running application by name.",
        parameters={"type": "object", "properties": {
            "name": {"type": "string", "description": "Application name"}},
            "required": ["name"]},
        risk=Risk.MEDIUM, handler=close_application))
    registry.register(Tool(
        name="web_search",
        description="Search the web for current information. Returns titles, URLs and snippets.",
        parameters={"type": "object", "properties": {
            "query": {"type": "string"},
            "count": {"type": "integer", "minimum": 1, "maximum": 10}},
            "required": ["query"]},
        risk=Risk.LOW, handler=web_search, timeout=45))
    registry.register(Tool(
        name="read_file",
        description="Read a text file from the user's Documents, Downloads, Desktop or Pictures folders.",
        parameters={"type": "object", "properties": {
            "path": {"type": "string", "description": "File path or name"}},
            "required": ["path"]},
        risk=Risk.LOW, handler=read_file))
