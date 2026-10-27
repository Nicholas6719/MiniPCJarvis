"""Asking him to keep an eye on something and report back.

These are the only tools that do not finish when the sentence does. Everything
else answers now; these answer later, possibly after a restart, possibly hours
later. That is the whole point of them.

One tool per kind rather than a free-form "condition" string, because a model
asked to fill in a condition field invents plausible nonsense. A tool per kind
means the only thing it has to get right is which one to call. The checking is
in standing.py; proactive.run_checks runs it once a minute and hands every hit
to delivery, which decides whether to say it or send it.
"""
from __future__ import annotations

import logging
import os
import re

import standing
from config import config
from tools.registry import Risk, Tool, registry

log = logging.getLogger("jarvis.tools.watches")

_FOLDERS = {"downloads": "downloads", "download": "downloads", "documents": "documents",
            "docs": "documents", "desktop": "desktop", "pictures": "pictures", "photos": "pictures"}


def _clean(name: str) -> str:
    return (name or "").strip().strip(".!?,").strip()


def watch_for_process(name: str) -> dict:
    clean = _clean(name).lower()
    if clean.endswith(".exe"):
        clean = clean[:-4]
    if not clean:
        return {"error": "no program named"}
    if clean in ("jarvis", "jarvis-sidecar", "llama-server", "sidecar"):
        return {"error": "I can't watch myself finish, sir."}
    running = standing.process_running(clean)
    w = standing.add("process", clean, description=f"when {clean} finishes", armed=running)
    if running:
        return {"id": w["id"], "watching": f"I'll tell you when {clean} finishes.", "running": True}
    return {"id": w["id"], "running": False,
            "watching": f"I don't see {clean} running yet. I'll tell you once it has started and finished."}


def watch_for_download(name: str = "") -> dict:
    clean = _clean(name)
    desc = f"when {clean} finishes downloading" if clean else "when the next download finishes"
    w = standing.add("download", clean, description=desc)
    return {"id": w["id"], "watching": f"I'll tell you {desc}."}


def watch_for_file(pattern: str = "", folder: str = "downloads") -> dict:
    pat = _clean(pattern)          # empty: any NEW file in the folder
    f = (folder or "downloads").strip()
    key = _FOLDERS.get(re.sub(r"^(?:my|the)\s+", "", f.lower()).strip())
    path = str(config.get("folders", key, default="") or "") if key else f
    if not path or not os.path.isdir(path):
        return {"error": f"I don't know a folder called {f}."}
    word = key or os.path.basename(path.rstrip("\\/")) or path
    what = pat or "a new file"
    w = standing.add("file", pat, folder=path, description=f"when {what} appears in {word}")
    return {"id": w["id"], "watching": f"I'll tell you when {what} appears in {word}."}


def watch_for_battery(percent: int = 100) -> dict:
    try:
        level = max(1, min(100, int(percent)))
    except (TypeError, ValueError):
        level = 100
    try:
        import psutil
        b = psutil.sensors_battery()
    except Exception:
        b = None
    if b is None:
        return {"error": "This machine has no battery to watch, sir."}
    desc = "when the battery is fully charged" if level >= 100 else f"when the battery reaches {level} percent"
    w = standing.add("battery", level=level, description=desc)
    return {"id": w["id"], "watching": f"I'll tell you {desc}."}


def list_watches() -> dict:
    ws = standing.all_watches()
    return {"watches": [{"id": w["id"], "kind": w["kind"], "description": w["description"],
                         "armed": w["armed"]} for w in ws],
            "spoken": _spoken(ws)}


def _spoken(ws: list[dict]) -> str:
    if not ws:
        return "I'm not watching for anything at the moment, sir."
    parts = []
    for w in ws:
        d = w["description"] or w["kind"]
        if w["kind"] == "process" and not w["armed"]:
            d += " (not started yet)"
        parts.append(d)
    if len(parts) == 1:
        return f"I'm watching for {parts[0]}."
    return "I'm watching for " + ", ".join(parts[:-1]) + f", and {parts[-1]}."


def cancel_watch(which: str = "") -> dict:
    dropped = standing.cancel(_clean(which))
    return {"removed": len(dropped), "dropped": [w["description"] for w in dropped]}


def register_all() -> None:
    registry.register(Tool(
        name="watch_for_process",
        description="Tell the user when a running program finishes (exits). Use for 'tell me when "
                    "the build is done', 'let me know when HandBrake finishes'. The name is the "
                    "program as Task Manager shows it; partial names are fine.",
        parameters={"type": "object", "properties": {
            "name": {"type": "string", "description": "program name, e.g. 'handbrake', 'blender'"}},
            "required": ["name"]},
        risk=Risk.LOW, handler=watch_for_process))
    registry.register(Tool(
        name="watch_for_download",
        description="Tell the user when a download finishes landing in his Downloads folder. "
                    "Use for 'tell me when that download is done'.",
        parameters={"type": "object", "properties": {
            "name": {"type": "string", "description": "part of the filename, or empty for the next download"}},
            "required": []},
        risk=Risk.LOW, handler=watch_for_download))
    registry.register(Tool(
        name="watch_for_file",
        description="Tell the user when a file whose name contains `pattern` appears in a folder "
                    "(downloads, documents, desktop, pictures, or a full path).",
        parameters={"type": "object", "properties": {
            "pattern": {"type": "string", "description": "part of the filename; empty for any new file"},
            "folder": {"type": "string", "description": "downloads | documents | desktop | pictures | a path"}},
            "required": []},
        risk=Risk.LOW, handler=watch_for_file))
    registry.register(Tool(
        name="watch_for_battery",
        description="Tell the user when the battery charges up to a level. Use for 'tell me when "
                    "it's charged' (100) or 'let me know at 80 percent'.",
        parameters={"type": "object", "properties": {
            "percent": {"type": "integer", "description": "1-100; 100 means fully charged"}},
            "required": []},
        risk=Risk.LOW, handler=watch_for_battery))
    registry.register(Tool(
        name="list_watches",
        description="List the things the user asked JARVIS to watch for (processes, downloads, files, battery).",
        parameters={"type": "object", "properties": {}, "required": []},
        risk=Risk.LOW, handler=list_watches))
    registry.register(Tool(
        name="cancel_watch",
        description="Stop watching for something the user asked about. Empty cancels every watch.",
        parameters={"type": "object", "properties": {
            "which": {"type": "string", "description": "part of what was being watched"}},
            "required": []},
        risk=Risk.LOW, handler=cancel_watch))
