"""Live thumbnails of open windows for the APPS view (PrintWindow -> small JPEG)."""
from __future__ import annotations

import base64
import ctypes
import io
import logging

import win32con
import win32gui
import win32process
import win32ui

log = logging.getLogger("jarvis.tools.thumbs")
PW_RENDERFULLCONTENT = 0x00000002
THUMB_W = 320


def _thumb(hwnd: int) -> str | None:
    """JPEG data URL of a window's current contents, or None (minimized / protected)."""
    if win32gui.IsIconic(hwnd):
        return None
    l, t, r, b = win32gui.GetWindowRect(hwnd)
    w, h = r - l, b - t
    if w < 40 or h < 40 or w > 8000:
        return None
    hwnd_dc = win32gui.GetWindowDC(hwnd)
    mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
    save_dc = mfc_dc.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    try:
        bmp.CreateCompatibleBitmap(mfc_dc, w, h)
        save_dc.SelectObject(bmp)
        ok = ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), PW_RENDERFULLCONTENT)
        if not ok:
            return None
        info = bmp.GetInfo()
        bits = bmp.GetBitmapBits(True)
        from PIL import Image
        img = Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]), bits, "raw", "BGRX", 0, 1)
        if img.getbbox() is None:
            return None   # all black = nothing rendered (some GPU-composited apps)
        scale = THUMB_W / max(1, img.width)
        img = img.resize((THUMB_W, max(1, int(img.height * scale))))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=60)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception as e:
        log.debug("thumb failed for %s: %s", hwnd, e)
        return None
    finally:
        try:
            win32gui.DeleteObject(bmp.GetHandle())
            save_dc.DeleteDC()
            mfc_dc.DeleteDC()
            win32gui.ReleaseDC(hwnd, hwnd_dc)
        except Exception:
            pass


def _process_name(hwnd: int) -> str:
    try:
        import psutil
        pid = win32process.GetWindowThreadProcessId(hwnd)[1]
        return psutil.Process(pid).name().removesuffix(".exe")
    except Exception:
        return ""


def windows_with_thumbs(include_self: bool = False, thumbs: bool = True) -> list[dict]:
    from tools.windows_tools import _visible_windows
    out = []
    fg = win32gui.GetForegroundWindow()
    for hwnd, title in _visible_windows():
        if not include_self and title == "JARVIS":
            continue
        out.append({
            "hwnd": hwnd, "title": title, "process": _process_name(hwnd),
            "minimized": bool(win32gui.IsIconic(hwnd)), "active": hwnd == fg,
            "thumb": _thumb(hwnd) if thumbs else None,
        })
    return out


def act(hwnd: int, action: str) -> dict:
    if not win32gui.IsWindow(hwnd):
        return {"error": "window is gone"}
    title = win32gui.GetWindowText(hwnd)
    if action == "focus":
        from tools.windows_tools import focus_window
        return focus_window(title)
    if action == "minimize":
        win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
        return {"minimized": title}
    if action == "maximize":
        win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
        return {"maximized": title}
    if action == "close":
        win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        return {"closed": title}
    return {"error": f"unknown action {action}"}


def capture_window_png(title_exact: str) -> bytes | None:
    """Full-size PNG of a top-level window by exact title (PrintWindow, works even if
    another window covers it)."""
    import io
    hwnd = win32gui.FindWindow(None, title_exact)
    if not hwnd:
        return None
    l, t, r, b = win32gui.GetWindowRect(hwnd)
    w, h = r - l, b - t
    if w < 50 or h < 50:
        return None
    hwnd_dc = win32gui.GetWindowDC(hwnd)
    mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
    save_dc = mfc_dc.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    try:
        bmp.CreateCompatibleBitmap(mfc_dc, w, h)
        save_dc.SelectObject(bmp)
        if not ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), PW_RENDERFULLCONTENT):
            return None
        info = bmp.GetInfo()
        from PIL import Image
        img = Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]), bmp.GetBitmapBits(True), "raw", "BGRX", 0, 1)
        buf = io.BytesIO(); img.save(buf, "PNG")
        return buf.getvalue()
    finally:
        try:
            win32gui.DeleteObject(bmp.GetHandle()); save_dc.DeleteDC(); mfc_dc.DeleteDC(); win32gui.ReleaseDC(hwnd, hwnd_dc)
        except Exception:
            pass
