"""Files inside JARVIS: browse, find, preview, rename, move and recycle the user's
Desktop / Documents / Downloads / Pictures without leaving the HUD.

Everything is sandboxed to those roots (from config "folders"). Deletes go to the
Recycle Bin (undoable). Write operations are MEDIUM risk -> confirmation gated."""
from __future__ import annotations

import base64
import ctypes
import logging
import mimetypes
import os
import re
import time
from pathlib import Path

from config import config
from events import bus
from tools.registry import Risk, Tool, registry

log = logging.getLogger("jarvis.tools.files")

TEXT_EXT = {".txt", ".md", ".py", ".js", ".ts", ".tsx", ".json", ".csv", ".log", ".ini", ".cfg",
            ".toml", ".yaml", ".yml", ".xml", ".html", ".css", ".bat", ".cmd", ".ps1", ".sh", ".rs"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
MAX_PREVIEW = 200_000


def roots() -> dict[str, Path]:
    f = config.get("folders", default={}) or {}
    out: dict[str, Path] = {}
    for key in ("desktop", "documents", "downloads", "pictures"):
        p = f.get(key)
        if p and Path(p).exists():
            out[key] = Path(p)
    return out


def _resolve(path: str | None, must_exist: bool = True) -> Path | None:
    """Accept a root name ("downloads"), a relative path under a root, or an absolute
    path; only return paths inside the allowed roots."""
    rs = roots()
    if not path or path.strip() in ("", "/", "home"):
        return None
    raw = path.strip().strip('"')
    key = raw.lower().rstrip("/\\")
    if key in rs:
        return rs[key]
    for name, root in rs.items():
        if key.startswith(name + "/") or key.startswith(name + "\\"):
            cand = root / raw[len(name) + 1:]
            return _inside(cand, rs)
    p = Path(os.path.expandvars(raw)).expanduser()
    if p.is_absolute():
        return _inside(p, rs)
    for root in rs.values():
        cand = root / raw
        if cand.exists():
            return cand
    return None if must_exist else _inside(rs.get("documents", next(iter(rs.values()))) / raw, rs)


def _inside(p: Path, rs: dict[str, Path]) -> Path | None:
    try:
        rp = p.resolve()
    except OSError:
        return None
    for root in rs.values():
        try:
            rp.relative_to(root.resolve())
            return rp
        except ValueError:
            continue
    return None


def _entry(p: Path) -> dict:
    try:
        st = p.stat()
    except OSError:
        return {"name": p.name, "path": str(p), "kind": "unknown"}
    kind = "folder" if p.is_dir() else "file"
    ext = p.suffix.lower()
    ftype = ("image" if ext in IMAGE_EXT else "text" if ext in TEXT_EXT else "pdf" if ext == ".pdf"
             else "video" if ext in (".mp4", ".mkv", ".mov", ".avi") else "audio" if ext in (".mp3", ".wav", ".flac", ".m4a")
             else "archive" if ext in (".zip", ".7z", ".rar") else "document" if ext in (".docx", ".xlsx", ".pptx", ".doc")
             else "")
    return {"name": p.name, "path": str(p), "kind": kind, "type": ftype,
            "size": 0 if kind == "folder" else st.st_size, "modified": st.st_mtime}


def _display(p: Path) -> str:
    """'downloads/setup.exe' style label for speech and the HUD."""
    for name, root in roots().items():
        try:
            rel = p.resolve().relative_to(root.resolve())
            rel_s = str(rel).replace("\\", "/")
            return name if rel_s in (".", "") else f"{name}/{rel_s}"
        except ValueError:
            continue
    return p.name


async def list_folder(path: str = "downloads", limit: int = 200) -> dict:
    p = _resolve(path)
    if p is None:
        return {"error": f"'{path}' is not inside your Desktop, Documents, Downloads or Pictures",
                "roots": list(roots())}
    if not p.is_dir():
        return {"error": f"{_display(p)} is a file, not a folder"}
    entries = []
    try:
        for child in p.iterdir():
            if child.name.startswith((".", "~$")) or child.name.lower() == "desktop.ini":
                continue
            entries.append(_entry(child))
    except PermissionError:
        return {"error": f"no permission to read {_display(p)}"}
    entries.sort(key=lambda e: (e["kind"] != "folder", -e.get("modified", 0)))
    total = len(entries)
    entries = entries[:limit]
    parent = str(p.parent) if _inside(p.parent, roots()) and p.parent != p else None
    out = {"path": str(p), "label": _display(p), "parent": parent, "count": total, "entries": entries,
           "roots": {k: str(v) for k, v in roots().items()}}
    await bus.emit("files", **out)
    return {"path": str(p), "label": _display(p), "count": total,
            "entries": [{"name": e["name"], "kind": e["kind"], "size": e["size"]} for e in entries[:40]]}


async def find_files(query: str, folder: str | None = None, limit: int = 40) -> dict:
    q = query.lower().strip().strip('"')
    if not q:
        return {"error": "empty query"}
    words = [w for w in re.split(r"\s+", q) if w]
    bases = [_resolve(folder)] if folder else list(roots().values())
    hits: list[dict] = []
    t0 = time.time()
    for base in bases:
        if base is None:
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if not d.startswith((".", "node_modules", "__pycache__", ".venv", "target"))]
            depth = len(Path(dirpath).relative_to(base).parts)
            if depth >= 5:
                dirnames[:] = []
            for n in filenames + dirnames:
                nl = n.lower()
                if all(w in nl for w in words):
                    hits.append(_entry(Path(dirpath) / n))
                    if len(hits) >= limit:
                        break
            if len(hits) >= limit or time.time() - t0 > 6:
                break
        if len(hits) >= limit or time.time() - t0 > 6:
            break
    hits.sort(key=lambda e: (-(e["name"].lower() == q), -(e["name"].lower().startswith(q)), -e.get("modified", 0)))
    await bus.emit("files", path=None, label=f'search: "{query}"', parent=None, count=len(hits),
                   entries=hits, roots={k: str(v) for k, v in roots().items()}, query=query)
    return {"query": query, "count": len(hits),
            "results": [{"name": h["name"], "where": _display(Path(h["path"]).parent), "kind": h["kind"]} for h in hits[:15]]}


async def preview_file(path: str) -> dict:
    p = _resolve(path)
    if p is None or not p.is_file():
        return {"error": f"file not found: {path}"}
    ext = p.suffix.lower()
    size = p.stat().st_size
    base = {"path": str(p), "label": _display(p), "name": p.name, "size": size, "modified": p.stat().st_mtime}
    if ext in IMAGE_EXT and size <= 12_000_000:
        mime = mimetypes.guess_type(p.name)[0] or "image/png"
        return {**base, "type": "image", "data": f"data:{mime};base64," + base64.b64encode(p.read_bytes()).decode()}
    if ext in TEXT_EXT or size <= 64_000:
        try:
            text = p.read_text("utf-8", errors="replace")
            if "\x00" in text[:4000]:
                return {**base, "type": "binary"}
            return {**base, "type": "text", "text": text[:MAX_PREVIEW], "truncated": len(text) > MAX_PREVIEW}
        except Exception as e:
            return {**base, "type": "binary", "error": str(e)}
    return {**base, "type": "binary"}


def _shell_op(op: int, src: str, dst: str | None, flags: int) -> int:
    """SHFileOperationW: moves/deletes with Explorer semantics (Recycle Bin, undo)."""
    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", ctypes.c_void_p), ("wFunc", ctypes.c_uint), ("pFrom", ctypes.c_wchar_p),
                    ("pTo", ctypes.c_wchar_p), ("fFlags", ctypes.c_ushort),
                    ("fAnyOperationsAborted", ctypes.c_int), ("hNameMappings", ctypes.c_void_p),
                    ("lpszProgressTitle", ctypes.c_wchar_p)]
    s = SHFILEOPSTRUCTW()
    s.hwnd = None
    s.wFunc = op
    s.pFrom = src + "\0\0"
    s.pTo = (dst + "\0\0") if dst else None
    s.fFlags = flags
    return ctypes.windll.shell32.SHFileOperationW(ctypes.byref(s))


FO_MOVE, FO_DELETE, FO_RENAME = 1, 3, 4
FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_NOERRORUI = 0x4, 0x10, 0x40, 0x400
_QUIET = FOF_SILENT | FOF_NOCONFIRMATION | FOF_NOERRORUI


async def delete_file(path: str) -> dict:
    """Send to the Recycle Bin (undoable in Explorer)."""
    p = _resolve(path)
    if p is None or not p.exists():
        return {"error": f"not found: {path}"}
    if p in roots().values():
        return {"error": "refusing to delete a root folder"}
    rc = _shell_op(FO_DELETE, str(p), None, _QUIET | FOF_ALLOWUNDO)
    if rc != 0:
        return {"error": f"could not recycle {_display(p)} (code {rc})"}
    await _refresh(p.parent)
    return {"recycled": _display(p)}


async def move_file(path: str, destination: str) -> dict:
    p = _resolve(path)
    if p is None or not p.exists():
        return {"error": f"not found: {path}"}
    d = _resolve(destination, must_exist=False)
    if d is None:
        return {"error": f"destination must be inside Desktop/Documents/Downloads/Pictures: {destination}"}
    if d.is_dir():
        target = d / p.name
    else:
        target = d
    if target.exists():
        return {"error": f"{_display(target)} already exists"}
    rc = _shell_op(FO_MOVE, str(p), str(target), _QUIET | FOF_ALLOWUNDO)
    if rc != 0:
        return {"error": f"could not move {_display(p)} (code {rc})"}
    await _refresh(target.parent)
    return {"moved": _display(p), "to": _display(target)}


async def rename_file(path: str, new_name: str) -> dict:
    p = _resolve(path)
    if p is None or not p.exists():
        return {"error": f"not found: {path}"}
    new_name = new_name.strip().strip('"')
    if not new_name or any(c in new_name for c in '\\/:*?"<>|'):
        return {"error": "invalid name"}
    if "." not in new_name and p.is_file() and p.suffix:
        new_name += p.suffix   # keep the extension when the user just says a new name
    target = p.with_name(new_name)
    if target.exists():
        return {"error": f"{new_name} already exists"}
    try:
        p.rename(target)
    except OSError as e:
        return {"error": f"could not rename: {e}"}
    await _refresh(target.parent)
    return {"renamed": p.name, "to": target.name}


async def open_with_windows(path: str) -> dict:
    """Open a file in its default Windows app (explicit user ask only)."""
    p = _resolve(path)
    if p is None or not p.exists():
        return {"error": f"not found: {path}"}
    try:
        os.startfile(str(p))
        return {"opened": _display(p)}
    except Exception as e:
        return {"error": f"could not open: {e}"}


async def _refresh(folder: Path) -> None:
    try:
        await list_folder(str(folder))
    except Exception:
        pass


def register_all() -> None:
    registry.register(Tool(
        name="list_folder",
        description="Show a folder in JARVIS's FILES view and return its entries. Folder can be "
                    "'desktop', 'documents', 'downloads', 'pictures', or a path inside them.",
        parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        risk=Risk.SAFE, handler=list_folder, timeout=15))
    registry.register(Tool(
        name="find_files",
        description="Find files or folders by name in the user's Desktop/Documents/Downloads/Pictures "
                    "and show them in the FILES view.",
        parameters={"type": "object", "properties": {
            "query": {"type": "string", "description": "words from the file name"},
            "folder": {"type": "string", "description": "optional: limit to one root or folder"}},
            "required": ["query"]},
        risk=Risk.SAFE, handler=find_files, timeout=20))
    registry.register(Tool(
        name="preview_file",
        description="Show a file (text or image) inside JARVIS and return its contents/metadata.",
        parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        risk=Risk.SAFE, handler=_preview_and_show, timeout=20))
    registry.register(Tool(
        name="move_file",
        description="Move a file or folder to another folder inside Desktop/Documents/Downloads/Pictures.",
        parameters={"type": "object", "properties": {
            "path": {"type": "string"}, "destination": {"type": "string"}},
            "required": ["path", "destination"]},
        risk=Risk.MEDIUM, handler=move_file, timeout=60))
    registry.register(Tool(
        name="rename_file",
        description="Rename a file or folder (keeps the extension if none is given).",
        parameters={"type": "object", "properties": {
            "path": {"type": "string"}, "new_name": {"type": "string"}}, "required": ["path", "new_name"]},
        risk=Risk.MEDIUM, handler=rename_file, timeout=20))
    registry.register(Tool(
        name="delete_file",
        description="Send a file or folder to the Recycle Bin (undoable).",
        parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        risk=Risk.MEDIUM, handler=delete_file, timeout=60))
    registry.register(Tool(
        name="open_with_windows",
        description="Open a file in its default Windows application (only when the user explicitly "
                    "asks to open it in another app; prefer preview_file to show it inside JARVIS).",
        parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        risk=Risk.LOW, handler=open_with_windows, timeout=15))


async def _preview_and_show(path: str) -> dict:
    pv = await preview_file(path)
    if "error" not in pv:
        await bus.emit("file_preview", **{k: v for k, v in pv.items() if k != "text" or True})
        summary = {k: v for k, v in pv.items() if k != "data"}
        if summary.get("text"):
            summary["text"] = summary["text"][:4000]
        return summary
    return pv
