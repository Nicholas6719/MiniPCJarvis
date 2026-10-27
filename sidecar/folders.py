"""Where his folders actually are.

`Path.home() / "Desktop"` is a guess, and on this machine it is the wrong one.
OneDrive redirects the shell folders, so the real Desktop is:

    assumed   C:\\Users\\nicho\\Desktop
    actual    C:\\Users\\nicho\\OneDrive\\Desktop

Both paths exist, which is what makes the failure so quiet: saving "to the
Desktop" succeeds, reports success, and puts the file somewhere he will never
look; reading "desktop/notes.txt" looks in the stale folder, finds nothing,
and reports the file missing. config.py used to carry the OneDrive path as a
hard-coded string, which was right for this PC and wrong for every other one,
and builtin.py never read it at all.

Windows knows the answer - SHGetKnownFolderPath returns the redirected path.
Ask it rather than guessing. Ported from PCJarvis's folders.py.
"""
from __future__ import annotations

import ctypes
import logging
from pathlib import Path

log = logging.getLogger("jarvis.folders")


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_uint32),
                ("Data2", ctypes.c_uint16),
                ("Data3", ctypes.c_uint16),
                ("Data4", ctypes.c_ubyte * 8)]


def _guid(a: int, b: int, c: int, rest: tuple[int, ...]) -> _GUID:
    return _GUID(a, b, c, (ctypes.c_ubyte * 8)(*rest))


# The shell folders a person actually names out loud.
KNOWN_FOLDER_IDS = {
    "desktop":   _guid(0xB4BFCC3A, 0xDB2C, 0x424C,
                       (0xB0, 0x29, 0x7F, 0xE9, 0x9A, 0x87, 0xC6, 0x41)),
    "documents": _guid(0xFDD39AD0, 0x238F, 0x46AF,
                       (0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7)),
    "downloads": _guid(0x374DE290, 0x123F, 0x4565,
                       (0x91, 0x64, 0x39, 0xC4, 0x92, 0x5E, 0x46, 0x7B)),
    "pictures":  _guid(0x33E28130, 0x4E1E, 0x4676,
                       (0x83, 0x5A, 0x98, 0x39, 0x5C, 0x3B, 0xC3, 0xBB)),
    "music":     _guid(0x4BD8D571, 0x6D19, 0x48D3,
                       (0xBE, 0x97, 0x42, 0x22, 0x20, 0x08, 0x0E, 0x43)),
    "videos":    _guid(0x18989B1D, 0x99B5, 0x455B,
                       (0x84, 0x1C, 0xAB, 0x7C, 0x74, 0xE4, 0xDD, 0xFC)),
}

_cache: dict[str, Path] = {}


def _ask_windows(folder_id: _GUID) -> Path | None:
    """SHGetKnownFolderPath, or None when the shell will not answer."""
    buffer = ctypes.c_wchar_p()
    try:
        status = ctypes.windll.shell32.SHGetKnownFolderPath(
            ctypes.byref(folder_id), 0, None, ctypes.byref(buffer))
        if status == 0 and buffer.value:
            path = Path(buffer.value)
            ctypes.windll.ole32.CoTaskMemFree(buffer)
            return path
    except Exception:
        log.debug("known-folder lookup failed", exc_info=True)
    return None


def known_folder(name: str) -> Path:
    """The real path of a shell folder, honouring OneDrive redirection.

    Falls back to the naive guess if Windows will not answer, which is better
    than failing outright - but the guess is what made files vanish into the
    unredirected folder, so the lookup is always tried first. Cached: the
    answer does not change while the sidecar runs.
    """
    key = (name or "").lower().strip()
    if key in _cache:
        return _cache[key]
    folder_id = KNOWN_FOLDER_IDS.get(key)
    path = _ask_windows(folder_id) if folder_id is not None else None
    if path is None:
        path = Path.home() / key.capitalize()
    _cache[key] = path
    return path


def local_folder(name: str) -> Path:
    """The plain C:\\Users\\<user>\\<Name> path, ignoring redirection."""
    return Path.home() / (name or "").lower().strip().capitalize()


def save_folder(name: str) -> Path:
    """Where JARVIS reads and writes a folder he names: "desktop", "documents"...

    He keeps his files on the local drive rather than in OneDrive, so Documents,
    Downloads and Pictures resolve to C:\\Users\\<user>\\<Name> whenever that
    folder exists (on this PC Windows registers Documents under OneDrive too,
    and his Coding_Projects live in the local one).

    The Desktop is the one exception, and it is not a preference: the desktop
    that appears on screen is whichever folder Windows has registered, and here
    that is the OneDrive one. The local C:\\Users\\nicho\\Desktop exists,
    succeeds, and is invisible - which is exactly how a screenshot "saved to
    the Desktop" was never seen. So the Desktop follows Windows; the rest stay
    local, and fall back to Windows' answer only when the local folder is gone.
    """
    key = (name or "").lower().strip()
    if key == "desktop":
        return known_folder("desktop")
    local = local_folder(key)
    if local.exists():
        return local
    return known_folder(key)


def describe() -> dict[str, str]:
    """Every resolved folder, for diagnostics."""
    return {name: str(known_folder(name)) for name in KNOWN_FOLDER_IDS}
