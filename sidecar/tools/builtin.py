"""Phase 1 built-in tools: system stats, open/close app, web search, read file."""
from __future__ import annotations

import logging
import os
import re
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
    "vs code": "visual studio code",
    "vscode": "visual studio code",
    "code": "visual studio code",
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


_START_MENUS = [
    _HOME / "AppData/Roaming/Microsoft/Windows/Start Menu/Programs",
    Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "Microsoft/Windows/Start Menu/Programs",
]


_store_apps_cache: dict[str, str] | None = None


def _store_apps() -> dict[str, str]:
    """Name -> AppUserModelId for everything Windows' Start search knows (incl. Store
    apps like Spotify). Cached; gathered once with a hidden PowerShell (~1 s)."""
    global _store_apps_cache
    if _store_apps_cache is not None:
        return _store_apps_cache
    apps: dict[str, str] = {}
    try:
        import json
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress"],
            capture_output=True, text=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        data = json.loads(out.stdout or "[]")
        for row in (data if isinstance(data, list) else [data]):
            if row.get("Name") and row.get("AppID"):
                apps[str(row["Name"]).lower()] = str(row["AppID"])
    except Exception as e:
        log.warning("Get-StartApps failed: %s", e)
    _store_apps_cache = apps
    return apps


def _best_match(key: str, names) -> str | None:
    exact = [n for n in names if n == key]
    if exact:
        return exact[0]
    subs = sorted((n for n in names if key in n and "uninstall" not in n), key=len)
    return subs[0] if subs else None


def _resolve_app(name: str) -> str | None:
    """Find something launchable for a friendly app name, WITHOUT a shell:
    alias -> Start Menu shortcut -> PATH exe -> Start-search app list (Store apps).
    Returns None if nothing matches so the caller can report it (no stray windows)."""
    import shutil
    key = name.strip().lower()
    target = _APP_ALIASES.get(key)
    if target:
        if target.startswith("ms-settings") or target.endswith(":"):
            return target
        found = shutil.which(target)
        if found and not found.lower().endswith((".cmd", ".bat")):
            return found
    # Start Menu shortcuts (what the Start search launches)
    links: dict[str, str] = {}
    for root in _START_MENUS:
        if root.exists():
            for lnk in root.rglob("*.lnk"):
                links.setdefault(lnk.stem.lower(), str(lnk))
    keys = [key]
    if target and not target.startswith("ms-settings"):
        keys.append(target.lower().removesuffix(".exe"))
    for k in keys:
        m = _best_match(k, links)
        if m:
            return links[m]
    for k in keys:
        found = shutil.which(k) or shutil.which(k + ".exe")
        if found and not found.lower().endswith((".cmd", ".bat")):
            return found
    apps = _store_apps()
    for k in keys:
        m = _best_match(k, apps)
        if m:
            return "shell:AppsFolder\\" + apps[m]
    return None


def open_application(name: str) -> dict:
    key = name.strip().lower()
    if re.search(r"^https?://|\b[a-z0-9-]+\.(?:com|org|net|io|gov|edu|co|tv|ai)\b", key):
        # a website: never hand it to the default browser - stays inside JARVIS
        return {"error": f"'{name}' is a website, not an app. Use open_url to show it inside JARVIS."}
    target = _resolve_app(name)
    if not target:
        return {"error": f"I can't find an app called '{name}' on this PC."}
    try:
        if target.startswith("shell:AppsFolder"):
            subprocess.Popen(["explorer.exe", target])   # Store app via its AppID, no console
        else:
            os.startfile(target)   # no shell, no console window
        return {"launched": name, "target": target}
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


def watch_metric(metric: str, op: str, value: float, for_min: int = 0, message: str = "") -> dict:
    """User-defined proactive rule: JARVIS speaks up when a system metric crosses a line."""
    from proactive import proactive
    if metric not in proactive.METRICS or op not in (">", "<"):
        return {"error": "metric must be cpu|ram|disk_free_gb|battery and op > or <"}
    rule = {"metric": metric, "op": op, "value": float(value), "for_min": int(for_min or 0)}
    if message:
        rule["message"] = message
    proactive.add_rule(rule)
    return {"watching": f"I'll tell you if {proactive.describe(rule)}.", "rules": len(proactive.rules())}


def unwatch_metric(metric: str | None = None) -> dict:
    from proactive import proactive
    return {"removed": proactive.remove_rules(metric)}


def register_all() -> None:
    registry.register(Tool(
        name="watch_metric",
        description="Set a standing alert: JARVIS will tell the user when a system metric crosses a "
                    "threshold (cpu/ram/battery in percent, disk_free_gb in gigabytes), optionally "
                    "only after it has held for for_min minutes.",
        parameters={"type": "object", "properties": {
            "metric": {"type": "string", "enum": ["cpu", "ram", "disk_free_gb", "battery"]},
            "op": {"type": "string", "enum": [">", "<"]},
            "value": {"type": "number"}, "for_min": {"type": "integer"}, "message": {"type": "string"}},
            "required": ["metric", "op", "value"]},
        risk=Risk.LOW, handler=watch_metric))
    registry.register(Tool(
        name="unwatch_metric",
        description="Remove standing metric alerts (all, or for one metric).",
        parameters={"type": "object", "properties": {"metric": {"type": "string"}}, "required": []},
        risk=Risk.LOW, handler=unwatch_metric))
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
