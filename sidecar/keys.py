"""Synthetic keyboard input that actually lands.

Every keystroke JARVIS sends — dictation's Ctrl+V, `press_keys`, `type_text` —
went through `keybd_event` with a scan code of zero and no regard for what his
own fingers were doing. Three things were wrong with that, each found on
2026-09-04 and each a separate way for a keystroke to report success and do
nothing:

  * PHYSICAL MODIFIERS. Dictation is hold-to-talk on Ctrl+Shift+D. The release
    fires the moment D comes up; Parakeet answers in ~140 ms; his fingers are
    still on Ctrl and Shift. `keybd_event` does not clear physical state, so
    the target app saw Ctrl+Shift+V — "paste as plain text" in Chrome, "paste
    formatting" in Word, nothing in the document — while `pasted: True`.

  * SCAN CODES. `keybd_event(vk, 0, ...)` sends scan code 0. Some XAML and
    raw-input consumers drop events with no scan code. `SendInput` with the
    real code from MapVirtualKey is what a keyboard produces.

  * UNICODE. `keybd_event`'s scan argument is a BYTE, so anything above U+00FF
    — a curly apostrophe, an em dash, every emoji — raised OverflowError or
    was truncated to a control character. `KEYEVENTF_UNICODE` takes a WORD.

`desktop_info` is the diagnostic for the fourth possibility, that the sidecar
is on a different window station or desktop from the app it is typing into —
which would make every keystroke vanish and is otherwise invisible.
"""
from __future__ import annotations

import ctypes
import logging
import time
from ctypes import wintypes

log = logging.getLogger("jarvis.keys")

user32 = ctypes.windll.user32

INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008
MAPVK_VK_TO_VSC = 0

VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN = 0x10, 0x11, 0x12, 0x5B, 0x5C
_MODIFIERS = (VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN,
              0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5)      # L/R shift, ctrl, alt

# Keys whose scan code needs the extended flag or Windows reads them as the
# numpad twin (Right Ctrl as Left Ctrl, arrow keys as digits, Delete as '.').
_EXTENDED = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E,
             0x5B, 0x5C, 0x5D, 0xA3, 0xA5, 0x6F, 0x0D}


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("ki", _KEYBDINPUT), ("pad", ctypes.c_byte * 32)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


def _send(events: list[_INPUT]) -> int:
    if not events:
        return 0
    arr = (_INPUT * len(events))(*events)
    return int(user32.SendInput(len(events), arr, ctypes.sizeof(_INPUT)))


def _vk_event(vk: int, up: bool) -> _INPUT:
    scan = user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC)
    flags = KEYEVENTF_KEYUP if up else 0
    if vk in _EXTENDED:
        flags |= KEYEVENTF_EXTENDEDKEY
    ev = _INPUT()
    ev.type = INPUT_KEYBOARD
    ev.ki = _KEYBDINPUT(vk, scan, flags, 0, None)
    return ev


def _unicode_event(unit: int, up: bool) -> _INPUT:
    ev = _INPUT()
    ev.type = INPUT_KEYBOARD
    ev.ki = _KEYBDINPUT(0, unit, KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0), 0, None)
    return ev


def modifiers_down() -> list[int]:
    """Which modifier keys are PHYSICALLY held right now."""
    return [vk for vk in _MODIFIERS if user32.GetAsyncKeyState(vk) & 0x8000]


def wait_modifiers_released(timeout: float = 1.0) -> bool:
    """Wait, briefly, for his fingers to come off Ctrl/Shift/Alt/Win.

    Bounded: a stuck key must not hold a paste forever. False means they were
    still down when time ran out, and the caller sends anyway — with explicit
    releases first, so the app at least sees the combination that was meant.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not modifiers_down():
            return True
        time.sleep(0.02)
    return not modifiers_down()


def press(vk: int, mods: tuple[int, ...] = ()) -> bool:
    """Tap one key with the given modifiers, as a real keyboard would.

    Physical modifiers are waited for and then explicitly released, because a
    Ctrl that is still under his finger turns Ctrl+V into Ctrl+Shift+V and a
    plain 'h' into Ctrl+H. Everything goes out in ONE SendInput call, so no
    other input can interleave with the combination.
    """
    if not wait_modifiers_released():
        held = modifiers_down()
        log.warning("keys: modifiers still held %s — releasing them first", held)
        _send([_vk_event(m, True) for m in held])
    events = [_vk_event(m, False) for m in mods]
    events += [_vk_event(vk, False), _vk_event(vk, True)]
    events += [_vk_event(m, True) for m in reversed(mods)]
    sent = _send(events)
    if sent != len(events):
        log.warning("keys: SendInput delivered %d of %d events (error %d)",
                    sent, len(events), ctypes.get_last_error())
        return False
    return True


def type_text(text: str, per_char_delay: float = 0.0) -> bool:
    """Type arbitrary text, including everything above U+00FF, as Unicode
    key events. Surrogate pairs are sent as two units, which is how Windows
    expects them; newlines become Enter."""
    if not text:
        return True
    if not wait_modifiers_released():
        _send([_vk_event(m, True) for m in modifiers_down()])
    ok = True
    for ch in text:
        if ch == "\n":
            ok &= press(0x0D)
            continue
        if ch == "\r":
            continue
        units = [ord(ch)] if ord(ch) <= 0xFFFF else [
            0xD800 + ((ord(ch) - 0x10000) >> 10), 0xDC00 + ((ord(ch) - 0x10000) & 0x3FF)]
        events = []
        for u in units:
            events += [_unicode_event(u, False), _unicode_event(u, True)]
        if _send(events) != len(events):
            ok = False
        if per_char_delay:
            time.sleep(per_char_delay)
    return ok


def desktop_info() -> dict:
    """Where this process's input would GO. If the thread desktop is not the
    input desktop, or the window station is not WinSta0, nothing this process
    types can reach a window he is looking at — and nothing else would say so."""
    UOI_NAME = 2

    def name(handle) -> str:
        if not handle:
            return ""
        buf = ctypes.create_unicode_buffer(256)
        needed = wintypes.DWORD()
        if user32.GetUserObjectInformationW(handle, UOI_NAME, buf, 512, ctypes.byref(needed)):
            return buf.value
        return ""

    out = {"window_station": "", "thread_desktop": "", "input_desktop": "",
           "same_desktop": None, "modifiers_down": modifiers_down()}
    try:
        out["window_station"] = name(user32.GetProcessWindowStation())
        out["thread_desktop"] = name(user32.GetThreadDesktop(ctypes.windll.kernel32.GetCurrentThreadId()))
        h = user32.OpenInputDesktop(0, False, 0x0100)       # DESKTOP_READOBJECTS
        if h:
            out["input_desktop"] = name(h)
            user32.CloseDesktop(h)
        out["same_desktop"] = bool(out["thread_desktop"]) and \
            out["thread_desktop"] == out["input_desktop"]
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    return out
