"""Remote hands (R2) — typing, keys and clicks, driven from anywhere.

Every function here can act on ANY window, so every one of them is risk-gated:
MEDIUM at minimum, so a remote request always stops for DO IT / NO on the phone.
Nothing here reads the screen; sight comes from screenshot_grid() + the vision
model, and the user names the target ("click C4" / "click the blue button").

Windows notes learned the hard way:
- SendInput (via pywin32's keybd_event/mouse_event) targets the FOREGROUND
  window, so every action focuses its target first and verifies it took.
- Unicode typing uses KEYEVENTF_UNICODE, which sends characters the layout
  cannot produce (em dashes, emoji) — much safer than VK scan codes.
- A locked workstation cannot be driven at all: Windows blocks synthetic input
  at the secure desktop. We detect that and say so instead of silently failing.
"""
from __future__ import annotations

import asyncio
import ctypes
import logging
import re
import time

import win32api
import win32con
import win32gui

from events import bus
from tools.registry import Risk, Tool, registry

log = logging.getLogger("jarvis.input")

# grid labels: columns A.. across, rows 1.. down (spoken as "C4")
GRID_COLS, GRID_ROWS = 6, 8


def _locked() -> bool:
    """True when the workstation is locked / on the secure desktop."""
    try:
        h = ctypes.windll.user32.OpenInputDesktop(0, False, 0x0100)  # DESKTOP_READOBJECTS
        if not h:
            return True
        ctypes.windll.user32.CloseDesktop(h)
        return False
    except Exception:
        return False


def _screen_size() -> tuple[int, int]:
    return (win32api.GetSystemMetrics(win32con.SM_CXVIRTUALSCREEN),
            win32api.GetSystemMetrics(win32con.SM_CYVIRTUALSCREEN))


def _cell_to_xy(cell: str) -> tuple[int, int] | None:
    m = re.fullmatch(r"\s*([A-Za-z])\s*(\d{1,2})\s*", cell or "")
    if not m:
        return None
    col = ord(m.group(1).upper()) - ord("A")
    row = int(m.group(2)) - 1
    if not (0 <= col < GRID_COLS and 0 <= row < GRID_ROWS):
        return None
    w, h = _screen_size()
    return (int((col + 0.5) * w / GRID_COLS), int((row + 0.5) * h / GRID_ROWS))


def _focus(window: str | None) -> str | None:
    """Bring a window to the front by title fragment. Returns its title, or None
    when no match; empty/None means 'whatever is already focused'."""
    if not window:
        hwnd = win32gui.GetForegroundWindow()
        return win32gui.GetWindowText(hwnd) if hwnd else None
    q = window.strip().lower()
    hits: list[tuple[int, str]] = []

    def _scan(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            t = win32gui.GetWindowText(hwnd)
            if t and q in t.lower():
                hits.append((hwnd, t))
        return True
    win32gui.EnumWindows(_scan, None)
    if not hits:
        return None
    hwnd, title = hits[0]
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        # Windows refuses SetForegroundWindow from a background process unless we
        # nudge it; ALT is the documented trick and is harmless.
        win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
        win32gui.SetForegroundWindow(hwnd)
        win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
    except Exception:
        log.warning("could not focus %r", title, exc_info=True)
    time.sleep(0.25)
    return title


def _type_unicode(text: str) -> None:
    for ch in text:
        code = ord(ch)
        win32api.keybd_event(0, code, win32con.KEYEVENTF_UNICODE, 0)
        win32api.keybd_event(0, code, win32con.KEYEVENTF_UNICODE | win32con.KEYEVENTF_KEYUP, 0)
        time.sleep(0.005)


_VK = {
    "enter": win32con.VK_RETURN, "return": win32con.VK_RETURN, "tab": win32con.VK_TAB,
    "escape": win32con.VK_ESCAPE, "esc": win32con.VK_ESCAPE, "space": win32con.VK_SPACE,
    "backspace": win32con.VK_BACK, "delete": win32con.VK_DELETE, "del": win32con.VK_DELETE,
    "up": win32con.VK_UP, "down": win32con.VK_DOWN, "left": win32con.VK_LEFT,
    "right": win32con.VK_RIGHT, "home": win32con.VK_HOME, "end": win32con.VK_END,
    "pageup": win32con.VK_PRIOR, "pagedown": win32con.VK_NEXT,
    "f1": win32con.VK_F1, "f2": win32con.VK_F2, "f3": win32con.VK_F3, "f4": win32con.VK_F4,
    "f5": win32con.VK_F5, "f11": win32con.VK_F11, "f12": win32con.VK_F12,
}
_MODS = {"ctrl": win32con.VK_CONTROL, "control": win32con.VK_CONTROL,
         "alt": win32con.VK_MENU, "shift": win32con.VK_SHIFT, "win": win32con.VK_LWIN}


def _press(combo: str) -> bool:
    parts = [p.strip().lower() for p in re.split(r"[+\s]+", combo) if p.strip()]
    if not parts:
        return False
    mods = [_MODS[p] for p in parts if p in _MODS]
    rest = [p for p in parts if p not in _MODS]
    if len(rest) != 1:
        return False
    key = rest[0]
    vk = _VK.get(key) or (win32api.VkKeyScan(key) & 0xFF if len(key) == 1 else None)
    if not vk or vk == 0xFF:
        return False
    for m in mods:
        win32api.keybd_event(m, 0, 0, 0)
    win32api.keybd_event(vk, 0, 0, 0)
    win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)
    for m in reversed(mods):
        win32api.keybd_event(m, 0, win32con.KEYEVENTF_KEYUP, 0)
    return True


# ------------------------------------------------------------------ the tools
async def type_text(text: str, window: str | None = None, press_enter: bool = False) -> dict:
    """Type into the focused (or named) window."""
    if _locked():
        return {"error": "the PC is locked — Windows blocks typing until it's unlocked"}
    if not text:
        return {"error": "nothing to type"}
    title = await asyncio.to_thread(_focus, window)
    if window and title is None:
        return {"error": f"I don't see a window matching '{window}'"}
    await asyncio.to_thread(_type_unicode, text)
    if press_enter:
        await asyncio.to_thread(_press, "enter")
    await bus.emit("remote_input", action="type", chars=len(text), window=title)
    return {"typed": text[:80], "window": title, "sent": press_enter}


async def press_keys(keys: str, window: str | None = None, repeat: int = 1) -> dict:
    """Press a key or combination: 'enter', 'ctrl+s', 'alt+tab', 'win+d'."""
    if _locked():
        return {"error": "the PC is locked — Windows blocks key presses until it's unlocked"}
    title = await asyncio.to_thread(_focus, window)
    if window and title is None:
        return {"error": f"I don't see a window matching '{window}'"}
    for _ in range(max(1, min(20, repeat))):
        if not await asyncio.to_thread(_press, keys):
            return {"error": f"I don't know the key combination '{keys}'"}
        await asyncio.sleep(0.05)
    await bus.emit("remote_input", action="keys", keys=keys, window=title)
    return {"pressed": keys, "window": title}


async def click_screen(cell: str = "", x: int | None = None, y: int | None = None,
                       button: str = "left", double: bool = False) -> dict:
    """Click a grid cell from the last grid screenshot ('C4'), or exact coordinates."""
    if _locked():
        return {"error": "the PC is locked — Windows blocks clicking until it's unlocked"}
    if cell:
        xy = _cell_to_xy(cell)
        if xy is None:
            return {"error": f"'{cell}' isn't a cell on the grid (A1 to "
                             f"{chr(ord('A') + GRID_COLS - 1)}{GRID_ROWS})"}
        x, y = xy
    if x is None or y is None:
        return {"error": "tell me a grid cell (like C4) or exact coordinates"}
    w, h = _screen_size()
    x, y = max(0, min(w - 1, int(x))), max(0, min(h - 1, int(y)))

    def _do():
        win32api.SetCursorPos((x, y))
        time.sleep(0.05)
        down, up = ((win32con.MOUSEEVENTF_RIGHTDOWN, win32con.MOUSEEVENTF_RIGHTUP)
                    if button == "right" else
                    (win32con.MOUSEEVENTF_LEFTDOWN, win32con.MOUSEEVENTF_LEFTUP))
        for _ in range(2 if double else 1):
            win32api.mouse_event(down, x, y, 0, 0)
            win32api.mouse_event(up, x, y, 0, 0)
            time.sleep(0.06)
    await asyncio.to_thread(_do)
    await asyncio.sleep(0.35)   # let the click land; asyncio.sleep, because a
    # plain sleep here froze the event loop and the wake word with it
    await bus.emit("remote_input", action="click", x=x, y=y, cell=cell or None)
    return {"clicked": cell or f"{x},{y}", "button": button, "double": double,
            "window": win32gui.GetWindowText(win32gui.GetForegroundWindow())}


async def scroll_screen(direction: str = "down", amount: int = 3) -> dict:
    """Scroll the window under the cursor."""
    if _locked():
        return {"error": "the PC is locked"}
    clicks = max(1, min(20, amount)) * (1 if direction.lower().startswith("up") else -1)

    def _do():
        for _ in range(abs(clicks)):
            win32api.mouse_event(win32con.MOUSEEVENTF_WHEEL, 0, 0,
                                 120 if clicks > 0 else -120, 0)
            time.sleep(0.04)
    await asyncio.to_thread(_do)
    return {"scrolled": direction, "amount": abs(clicks)}


async def screenshot_grid(window: str | None = None) -> dict:
    """Screenshot with a labelled A1..F8 grid drawn over it, so a remote user can
    say exactly where to click without guessing pixels."""
    from PIL import Image, ImageDraw
    from tools.windows_tools import take_screenshot
    if window:
        await asyncio.to_thread(_focus, window)
    shot = await asyncio.to_thread(take_screenshot, 0, True, None, None)
    if "error" in shot:
        return shot
    path = shot["path"]

    def _draw():
        img = Image.open(path).convert("RGB")
        d = ImageDraw.Draw(img, "RGBA")
        w, h = img.size
        for c in range(1, GRID_COLS):
            d.line([(w * c / GRID_COLS, 0), (w * c / GRID_COLS, h)], fill=(39, 199, 255, 130), width=2)
        for r in range(1, GRID_ROWS):
            d.line([(0, h * r / GRID_ROWS), (w, h * r / GRID_ROWS)], fill=(39, 199, 255, 130), width=2)
        size = max(18, w // 55)
        for c in range(GRID_COLS):
            for r in range(GRID_ROWS):
                label = f"{chr(ord('A') + c)}{r + 1}"
                x, y = c * w / GRID_COLS + 8, r * h / GRID_ROWS + 6
                d.rectangle([x - 3, y - 2, x + size * 1.5, y + size + 2], fill=(4, 12, 20, 170))
                d.text((x, y), label, fill=(39, 199, 255), font_size=size)
        img.save(path)
        return img.size
    size = await asyncio.to_thread(_draw)
    await bus.emit("remote_input", action="grid_screenshot", path=path)
    return {"path": path, "width": size[0], "height": size[1],
            "grid": f"A1 to {chr(ord('A') + GRID_COLS - 1)}{GRID_ROWS}",
            "instruction": "Tell the user they can say a cell like 'click C4'."}


def register_all() -> None:
    registry.register(Tool(
        name="type_text",
        description="Type text into the focused window (or a named one). Use for filling "
                    "a field, writing a message, entering a command.",
        parameters={"type": "object", "properties": {
            "text": {"type": "string"},
            "window": {"type": "string", "description": "optional window title fragment"},
            "press_enter": {"type": "boolean", "description": "press Enter afterwards"}},
            "required": ["text"]},
        risk=Risk.MEDIUM, handler=type_text, timeout=60))
    registry.register(Tool(
        name="press_keys",
        description="Press a key or combination such as 'enter', 'ctrl+s', 'alt+tab', 'win+d'.",
        parameters={"type": "object", "properties": {
            "keys": {"type": "string"}, "window": {"type": "string"},
            "repeat": {"type": "integer", "minimum": 1, "maximum": 20}},
            "required": ["keys"]},
        risk=Risk.MEDIUM, handler=press_keys, timeout=30))
    registry.register(Tool(
        name="click_screen",
        description="Click somewhere on screen. Prefer a grid cell from the last grid "
                    "screenshot (like 'C4'); exact x/y also works.",
        parameters={"type": "object", "properties": {
            "cell": {"type": "string", "description": "grid cell, e.g. C4"},
            "x": {"type": "integer"}, "y": {"type": "integer"},
            "button": {"type": "string", "enum": ["left", "right"]},
            "double": {"type": "boolean"}}, "required": []},
        risk=Risk.MEDIUM, handler=click_screen, timeout=30))
    registry.register(Tool(
        name="scroll_screen",
        description="Scroll the window under the pointer up or down.",
        parameters={"type": "object", "properties": {
            "direction": {"type": "string", "enum": ["up", "down"]},
            "amount": {"type": "integer", "minimum": 1, "maximum": 20}}, "required": []},
        risk=Risk.LOW, handler=scroll_screen, timeout=30))
    registry.register(Tool(
        name="screenshot_grid",
        description="Take a screenshot with a labelled click-grid drawn on it, so the user "
                    "can say exactly where to click. Use when they ask to click something "
                    "and you need to see the screen first.",
        parameters={"type": "object", "properties": {"window": {"type": "string"}},
                    "required": []},
        risk=Risk.SAFE, handler=screenshot_grid, timeout=45))
