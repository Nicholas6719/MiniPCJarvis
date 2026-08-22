"""Deep Windows control tools: windows, volume, media, clipboard, screenshots, power."""
from __future__ import annotations

import ctypes
import re
import datetime
import logging
import webbrowser

import win32con
import win32gui

from config import APP_DIR, config
from pathlib import Path
from tools.registry import Risk, Tool, registry

log = logging.getLogger("jarvis.tools.windows")

SCREENSHOT_DIR = APP_DIR / "screenshots"


# ---------- window management ----------

def _visible_windows() -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []

    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if title.strip():
                out.append((hwnd, title))
        return True

    win32gui.EnumWindows(cb, None)
    return out


def list_windows() -> dict:
    return {"windows": [t for _, t in _visible_windows()][:40]}


def _find_window(title: str) -> tuple[int, str] | None:
    t = title.lower()
    wins = _visible_windows()
    for hwnd, wt in wins:  # exact-ish first
        if wt.lower() == t:
            return hwnd, wt
    for hwnd, wt in wins:
        if t in wt.lower():
            return hwnd, wt
    return None


def focus_window(title: str) -> dict:
    hit = _find_window(title)
    if not hit:
        return {"error": f"no window matching '{title}'"}
    hwnd, wt = hit
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        # nudge foreground permission, then bring forward
        ctypes.windll.user32.keybd_event(win32con.VK_MENU, 0, 0, 0)
        try:
            win32gui.SetForegroundWindow(hwnd)
        finally:
            ctypes.windll.user32.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
        return {"focused": wt}
    except Exception as e:
        return {"error": f"could not focus '{wt}': {e}"}


def minimize_window(title: str) -> dict:
    hit = _find_window(title)
    if not hit:
        return {"error": f"no window matching '{title}'"}
    win32gui.ShowWindow(hit[0], win32con.SW_MINIMIZE)
    return {"minimized": hit[1]}


def maximize_window(title: str) -> dict:
    hit = _find_window(title)
    if not hit:
        return {"error": f"no window matching '{title}'"}
    win32gui.ShowWindow(hit[0], win32con.SW_MAXIMIZE)
    return {"maximized": hit[1]}


def close_window(title: str) -> dict:
    hit = _find_window(title)
    if not hit:
        return {"error": f"no window matching '{title}'"}
    win32gui.PostMessage(hit[0], win32con.WM_CLOSE, 0, 0)
    return {"closed": hit[1]}


# ---------- volume / media ----------

def _endpoint_volume():
    from pycaw.pycaw import AudioUtilities
    # modern pycaw: AudioDevice wrapper exposes the endpoint volume directly
    return AudioUtilities.GetSpeakers().EndpointVolume


def get_volume() -> dict:
    vol = _endpoint_volume()
    return {"volume_percent": round(vol.GetMasterVolumeLevelScalar() * 100),
            "muted": bool(vol.GetMute())}


def set_volume(percent: int) -> dict:
    percent = max(0, min(100, int(percent)))
    vol = _endpoint_volume()
    vol.SetMasterVolumeLevelScalar(percent / 100.0, None)
    if percent > 0 and vol.GetMute():
        vol.SetMute(0, None)
    return {"volume_percent": percent}


def set_mute(muted: bool = True) -> dict:
    vol = _endpoint_volume()
    vol.SetMute(1 if muted else 0, None)
    return {"muted": bool(muted)}


_MEDIA_KEYS = {
    "play_pause": 0xB3, "next": 0xB0, "previous": 0xB1, "stop": 0xB2,
}


def media_control(action: str) -> dict:
    vk = _MEDIA_KEYS.get(action)
    if vk is None:
        return {"error": f"unknown media action '{action}'"}
    ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
    ctypes.windll.user32.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)
    return {"sent": action}


# ---------- clipboard ----------

def get_clipboard() -> dict:
    import win32clipboard
    win32clipboard.OpenClipboard()
    try:
        try:
            data = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
        except TypeError:
            return {"text": None, "note": "clipboard holds non-text content"}
        return {"text": data[:4000], "truncated": len(data) > 4000}
    finally:
        win32clipboard.CloseClipboard()


def set_clipboard(text: str) -> dict:
    import win32clipboard
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
        return {"copied": len(text)}
    finally:
        win32clipboard.CloseClipboard()


# ---------- screenshot / url / power ----------

def _our_windows() -> list[int]:
    """Top-level windows belonging to the JARVIS app itself."""
    hits = []

    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd).strip() == "JARVIS":
            hits.append(hwnd)
        return True

    win32gui.EnumWindows(cb, None)
    return hits


def _resolve_folder(destination: str | None) -> Path:
    """'desktop' / 'documents' / 'downloads' / 'pictures' / absolute path / default."""
    if not destination or not destination.strip():
        return SCREENSHOT_DIR
    d = destination.strip().lower()
    if d in ("default", "screenshots", "screenshot folder"):
        return SCREENSHOT_DIR
    folders = config.get("folders", default={}) or {}
    for name, path in folders.items():
        if name in d:  # "my desktop", "documents folder", "the downloads"
            return Path(path)
    p = Path(destination.strip()).expanduser()
    if p.is_absolute():
        return p
    return SCREENSHOT_DIR


def take_screenshot(monitor: int = 0, hide_self: bool = False,
                    destination: str | None = None, filename: str | None = None) -> dict:
    """Capture the screen. hide_self=True minimizes JARVIS's own window first so
    'look at my screen' sees the user's screen, not the assistant.
    destination: default screenshots folder, or desktop/documents/downloads/
    pictures, or an absolute folder path."""
    import mss
    import mss.tools
    import time as _t
    folder = _resolve_folder(destination)
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return {"error": f"cannot use folder {folder}: {e}"}
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    name = (filename or "").strip()
    if name:
        name = re.sub(r"[^\w\- .]", "", name)
        if not name.lower().endswith(".png"):
            name += ".png"
    path = folder / (name or f"screen-{ts}.png")
    hidden = []
    if hide_self:
        for hwnd in _our_windows():
            if not win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
                hidden.append(hwnd)
        if hidden:
            _t.sleep(0.45)  # let the desktop repaint
    try:
        with mss.mss() as sct:
            mon = sct.monitors[monitor] if monitor < len(sct.monitors) else sct.monitors[0]
            img = sct.grab(mon)
            mss.tools.to_png(img.rgb, img.size, output=str(path))
    finally:
        for hwnd in hidden:
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    return {"path": str(path), "width": img.size[0], "height": img.size[1]}


def open_url(url: str) -> dict:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    webbrowser.open(url)
    return {"opened": url}


def lock_computer() -> dict:
    ctypes.windll.user32.LockWorkStation()
    return {"locked": True}


def power_action(action: str) -> dict:
    import subprocess
    cmds = {
        "sleep": ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
        "shutdown": ["shutdown", "/s", "/t", "10"],
        "restart": ["shutdown", "/r", "/t", "10"],
    }
    cmd = cmds.get(action)
    if not cmd:
        return {"error": f"unknown power action '{action}'"}
    subprocess.Popen(cmd)
    note = "in 10 seconds — run 'shutdown /a' to abort" if action != "sleep" else "now"
    return {"action": action, "note": note}


def register_all() -> None:
    T = Tool
    registry.register(T(
        name="list_windows",
        description="List the titles of currently open windows.",
        parameters={"type": "object", "properties": {}, "required": []},
        risk=Risk.SAFE, handler=list_windows))
    registry.register(T(
        name="focus_window",
        description="Bring a window to the foreground by (partial) title match.",
        parameters={"type": "object", "properties": {
            "title": {"type": "string"}}, "required": ["title"]},
        risk=Risk.LOW, handler=focus_window))
    registry.register(T(
        name="minimize_window",
        description="Minimize a window by (partial) title match.",
        parameters={"type": "object", "properties": {
            "title": {"type": "string"}}, "required": ["title"]},
        risk=Risk.LOW, handler=minimize_window))
    registry.register(T(
        name="maximize_window",
        description="Maximize a window by (partial) title match.",
        parameters={"type": "object", "properties": {
            "title": {"type": "string"}}, "required": ["title"]},
        risk=Risk.LOW, handler=maximize_window))
    registry.register(T(
        name="close_window",
        description="Close a window by (partial) title match (sends a normal close request).",
        parameters={"type": "object", "properties": {
            "title": {"type": "string"}}, "required": ["title"]},
        risk=Risk.MEDIUM, handler=close_window))
    registry.register(T(
        name="get_volume",
        description="Get the current system volume and mute state.",
        parameters={"type": "object", "properties": {}, "required": []},
        risk=Risk.SAFE, handler=get_volume))
    registry.register(T(
        name="set_volume",
        description="Set system volume to a percentage (0-100).",
        parameters={"type": "object", "properties": {
            "percent": {"type": "integer", "minimum": 0, "maximum": 100}},
            "required": ["percent"]},
        risk=Risk.LOW, handler=set_volume))
    registry.register(T(
        name="set_mute",
        description="Mute or unmute system audio.",
        parameters={"type": "object", "properties": {
            "muted": {"type": "boolean"}}, "required": ["muted"]},
        risk=Risk.LOW, handler=set_mute))
    registry.register(T(
        name="media_control",
        description="Send a media key: play_pause, next, previous, or stop.",
        parameters={"type": "object", "properties": {
            "action": {"type": "string", "enum": list(_MEDIA_KEYS)}},
            "required": ["action"]},
        risk=Risk.LOW, handler=media_control))
    registry.register(T(
        name="get_clipboard",
        description="Read the current clipboard text.",
        parameters={"type": "object", "properties": {}, "required": []},
        risk=Risk.LOW, handler=get_clipboard))
    registry.register(T(
        name="set_clipboard",
        description="Copy text to the clipboard.",
        parameters={"type": "object", "properties": {
            "text": {"type": "string"}}, "required": ["text"]},
        risk=Risk.LOW, handler=set_clipboard))
    registry.register(T(
        name="take_screenshot",
        description="Capture a screenshot. Saves to the default screenshots folder "
                    "unless the user names a place: destination 'desktop', "
                    "'documents', 'downloads', 'pictures', or a folder path. "
                    "Optional filename.",
        parameters={"type": "object", "properties": {
            "destination": {"type": "string",
                            "description": "desktop | documents | downloads | pictures | folder path | default"},
            "filename": {"type": "string"},
            "monitor": {"type": "integer", "minimum": 0}}, "required": []},
        risk=Risk.LOW, handler=take_screenshot))
    registry.register(T(
        name="open_url",
        description="Open a URL in the default browser.",
        parameters={"type": "object", "properties": {
            "url": {"type": "string"}}, "required": ["url"]},
        risk=Risk.LOW, handler=open_url))
    registry.register(T(
        name="lock_computer",
        description="Lock the Windows session immediately.",
        parameters={"type": "object", "properties": {}, "required": []},
        risk=Risk.MEDIUM, handler=lock_computer))
    registry.register(T(
        name="power_action",
        description="Sleep, shut down, or restart the computer.",
        parameters={"type": "object", "properties": {
            "action": {"type": "string", "enum": ["sleep", "shutdown", "restart"]}},
            "required": ["action"]},
        risk=Risk.HIGH, handler=power_action))
