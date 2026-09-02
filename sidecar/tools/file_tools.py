"""Files inside JARVIS: browse, find, preview, rename, move and recycle the user's
Desktop / Documents / Downloads / Pictures without leaving the HUD.

Everything is sandboxed to those roots (from config "folders"). Deletes go to the
Recycle Bin (undoable). Everything is reversible (Recycle Bin / move back), so nothing asks for confirmation."""
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
            return _inside(cand, rs)   # a relative path can contain '..': must stay in-bounds
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


async def open_by_name(query: str) -> dict | None:
    """Resolve a spoken name ("jarvis install log") to a file/folder in the roots and
    show it — the folder stage promises "say open + a file name". Returns None when
    nothing matches so the caller (open_application) can fall through to websites.
    No 'files' event here: a failed app launch shouldn't flash the folder stage."""
    q = query.lower().strip().strip('"')
    words = [w for w in re.split(r"[\s_\-.]+", q) if w]
    if not words:
        return None
    best: Path | None = None
    best_mtime = -1.0
    t0 = time.time()
    for base in roots().values():
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if not d.startswith((".", "node_modules", "__pycache__", ".venv", "target"))]
            if len(Path(dirpath).relative_to(base).parts) >= 5:
                dirnames[:] = []
            for n in filenames + dirnames:
                nl = re.sub(r"[\s_\-.]+", " ", n.lower())
                if all(w in nl for w in words):
                    p = Path(dirpath) / n
                    try:
                        mt = p.stat().st_mtime
                    except OSError:
                        continue
                    if mt > best_mtime:
                        best, best_mtime = p, mt
            if time.time() - t0 > 6:
                break
        if time.time() - t0 > 6:
            break
    if best is None:
        return None
    if best.is_dir():
        return await list_folder(str(best))
    pv = await preview_file(str(best))
    if "error" in pv:
        return None
    await bus.emit("file_preview", **pv)
    return {"opened_file": best.name, "path": str(best)}


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


def _near_matches(name: str, limit: int = 5) -> list[str]:
    """Files in the allowed roots whose name resembles what he called it.

    Shortcuts on the desktop are the case this exists for: he says "Wispr Flow",
    the file is "Wispr Flow.lnk", and an exact-path tool sees nothing.
    """
    want = "".join(ch for ch in (name or "").lower() if ch.isalnum())
    if len(want) < 3:
        return []
    # SUBSTRING IS NOT ENOUGH, because he does not type the way a filename is
    # spelled. He asked for "Wisper flow" and "whisper flow"; the file is
    # "Wispr Flow.lnk", and neither of his spellings contains it or is contained
    # by it. A ratio catches the missing letter; 0.7 is loose enough for a typo
    # and tight enough that "Documents" does not match "Downloads" (0.44).
    from difflib import SequenceMatcher
    scored: list[tuple[float, str]] = []
    for root in roots().values():
        try:
            for f in root.iterdir():
                flat = "".join(ch for ch in f.stem.lower() if ch.isalnum())
                if not flat:
                    continue
                if want in flat or flat in want:
                    score = 1.0
                else:
                    score = SequenceMatcher(None, want, flat).ratio()
                if score >= 0.7:
                    scored.append((score, str(f)))
        except OSError:
            continue
    scored.sort(key=lambda s: -s[0])
    return [p for _, p in scored[:limit]]


async def delete_file(path: str) -> dict:
    """Send to the Recycle Bin (undoable in Explorer)."""
    p = _resolve(path)
    if p is None or not p.exists():
        # A BARE "not found" IS A DEAD END, and it produced one: asked to remove
        # "Wispr Flow" from the desktop, the model guessed a path, got nothing
        # back it could use, and answered "I'm sorry, sir." The file was there —
        # as a .lnk, which is not what he calls it and never will be.
        #
        # Handing back the near misses turns a refusal into the next round: the
        # tool loop can call this again with a real path. Names only; nothing is
        # deleted on a guess.
        near = _near_matches(Path(str(path)).stem if path else "")
        if near:
            return {"error": f"not found: {path}",
                    "did_you_mean": near,
                    "hint": "call delete_file again with one of these exact paths"}
        return {"error": f"not found: {path}"}
    if p in roots().values():
        return {"error": "refusing to delete a root folder"}
    rc = _shell_op(FO_DELETE, str(p), None, _QUIET | FOF_ALLOWUNDO)
    if rc != 0:
        return {"error": f"could not recycle {_display(p)} (code {rc})"}
    await _refresh(p.parent)
    return {"recycled": _display(p)}


# ---- the Recycle Bin itself -------------------------------------------------
# It is a shell NAMESPACE, not a folder: no path to walk. Shell32 via COM is the
# only honest way to see inside it (and the only way to restore an item, which
# is what "undo that" has to mean).
_BIN_CLSID = 10   # ssfBITBUCKET


def _bin_items():
    """(items, folder). COM must be initialised on whatever thread calls this —
    these tools run via asyncio.to_thread, so a fresh thread each time."""
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    shell = win32com.client.Dispatch("Shell.Application")
    folder = shell.Namespace(_BIN_CLSID)
    items = folder.Items()
    return [items.Item(i) for i in range(items.Count)], folder


def list_recycle_bin(limit: int = 40) -> dict:
    """What is in the Recycle Bin right now — name, original folder, when deleted."""
    try:
        items, folder = _bin_items()
    except Exception as e:
        return {"error": f"could not read the recycle bin: {e}"}
    out = []
    for it in items:
        try:
            out.append({
                "name": it.Name,
                # column 1 = original location, 2 = date deleted, 3 = size
                "original_folder": folder.GetDetailsOf(it, 1),
                "deleted": folder.GetDetailsOf(it, 2),
                "size": folder.GetDetailsOf(it, 3),
            })
        except Exception:
            continue
    out.sort(key=lambda e: e.get("deleted", ""), reverse=True)
    return {"count": len(out), "items": out[:limit]}


def restore_from_recycle_bin(name: str) -> dict:
    """Put a deleted item back where it came from (Explorer's own Restore verb)."""
    q = (name or "").strip().lower()
    if not q:
        return {"error": "which item should I restore?"}
    try:
        items, _folder = _bin_items()
    except Exception as e:
        return {"error": f"could not read the recycle bin: {e}"}
    match = next((it for it in items if it.Name.lower() == q), None) or \
        next((it for it in items if q in it.Name.lower()), None)
    if match is None:
        return {"error": f"nothing called {name} is in the recycle bin"}
    for verb in match.Verbs():
        if verb.Name.replace("&", "").lower() in ("restore", "undo delete"):
            verb.DoIt()
            return {"restored": match.Name}
    return {"error": f"Windows would not offer a Restore action for {match.Name}"}


def empty_recycle_bin() -> dict:
    """Permanently delete everything in the bin. HIGH risk: this is NOT undoable."""
    try:
        items, _ = _bin_items()
        n = len(items)
        # SHERB_NOCONFIRMATION | SHERB_NOPROGRESSUI | SHERB_NOSOUND
        rc = ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, 0x1 | 0x2 | 0x4)
        if rc not in (0, -2147418113):
            return {"error": f"could not empty the recycle bin (code {rc})"}
        return {"emptied": n}
    except Exception as e:
        return {"error": f"could not empty the recycle bin: {e}"}


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
        risk=Risk.LOW, handler=move_file, timeout=60))
    registry.register(Tool(
        name="rename_file",
        description="Rename a file or folder (keeps the extension if none is given).",
        parameters={"type": "object", "properties": {
            "path": {"type": "string"}, "new_name": {"type": "string"}}, "required": ["path", "new_name"]},
        risk=Risk.LOW, handler=rename_file, timeout=20))
    registry.register(Tool(
        name="delete_file",
        description="Send a file or folder to the Recycle Bin (undoable).",
        parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        risk=Risk.LOW, handler=delete_file, timeout=60))
    registry.register(Tool(
        name="list_recycle_bin",
        description="Show what is currently in the Windows Recycle Bin (deleted files "
                    "and folders, where they came from, and when they were deleted).",
        parameters={"type": "object", "properties": {
            "limit": {"type": "integer", "minimum": 1, "maximum": 100}}, "required": []},
        risk=Risk.SAFE, handler=list_recycle_bin, timeout=25))
    registry.register(Tool(
        name="restore_from_recycle_bin",
        description="Restore a deleted file or folder from the Recycle Bin back to where "
                    "it was. Use for 'undo that delete' / 'put X back'.",
        parameters={"type": "object", "properties": {"name": {"type": "string"}},
                    "required": ["name"]},
        risk=Risk.LOW, handler=restore_from_recycle_bin, timeout=25))
    registry.register(Tool(
        name="empty_recycle_bin",
        description="Permanently delete everything in the Recycle Bin. Cannot be undone.",
        parameters={"type": "object", "properties": {}, "required": []},
        risk=Risk.HIGH, handler=empty_recycle_bin, timeout=60))
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
