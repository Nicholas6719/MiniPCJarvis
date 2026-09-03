"""Deep Windows control tools: windows, volume, media, clipboard, screenshots, power."""
from __future__ import annotations

import ctypes
import re

import psutil
import datetime
import logging

import win32con
import win32gui

from config import APP_DIR, config
from pathlib import Path
from tools.registry import Risk, Tool, registry

log = logging.getLogger("jarvis.tools.windows")

SCREENSHOT_DIR = APP_DIR / "screenshots"


# ---------- window management ----------

DWMWA_CLOAKED = 14


def _is_cloaked(hwnd: int) -> bool:
    """True for a window DWM is hiding even though IsWindowVisible says otherwise.

    Windows 11 keeps the frame of a closed UWP app (Settings, Calculator, Store, Mail)
    alive and suspended. IsWindowVisible still reports True for it, so JARVIS insisted
    "you have Settings open" when it had been closed for hours — twice over, since these
    apps own both an ApplicationFrameWindow and a CoreWindow. Cloaking is the only
    reliable way to tell. This also excludes windows sitting on another virtual desktop,
    which is right for "what do I have open" — they are not on screen and switching to
    one would yank the desktop out from under him.
    """
    import ctypes.wintypes as wt
    value = ctypes.c_int(0)
    try:
        res = ctypes.windll.dwmapi.DwmGetWindowAttribute(
            wt.HWND(hwnd), DWMWA_CLOAKED, ctypes.byref(value), ctypes.sizeof(value))
    except Exception:
        return False
    return res == 0 and value.value != 0


def _visible_windows() -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []

    import win32con, win32process
    hidden_pids: set[int] = set()
    try:
        import psutil
        for pr in psutil.process_iter(["name", "cmdline"]):
            try:
                if (pr.info["name"] or "").lower() == "brave.exe":
                    cl = " ".join(pr.info["cmdline"] or []).lower()
                    if "jarvis" in cl and ("browser-profile" in cl or "session-browser" in cl):
                        hidden_pids.add(pr.pid)
            except Exception:
                continue
    except Exception:
        pass

    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if not title.strip():
                return True
            if _is_cloaked(hwnd):
                return True   # closed UWP app, or another virtual desktop
            # skip JARVIS's own hidden search browser (tool-window, off-screen)
            try:
                if win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE) & win32con.WS_EX_TOOLWINDOW:
                    return True
                if hidden_pids and win32process.GetWindowThreadProcessId(hwnd)[1] in hidden_pids:
                    return True
                # JARVIS's hidden browsers park off-screen (not minimized): never a user window
                l, t, r, b = win32gui.GetWindowRect(hwnd)
                if (l <= -5000 or t <= -5000) and not win32gui.IsIconic(hwnd):
                    return True
            except Exception:
                pass
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


def adjust_volume(direction: str = "down", step: int = 15) -> dict:
    """Relative volume — "turn it up", "be quieter". Nobody speaks in percentages;
    without this, 'be quieter' reached the model, which said "Understood." and
    changed nothing (2026-08-27)."""
    vol = _endpoint_volume()
    cur = round(vol.GetMasterVolumeLevelScalar() * 100)
    step = max(1, min(50, int(step)))
    tgt = cur + step if str(direction).lower().startswith("u") else cur - step
    tgt = max(0, min(100, tgt))
    vol.SetMasterVolumeLevelScalar(tgt / 100.0, None)
    if tgt > 0 and vol.GetMute():
        vol.SetMute(0, None)
    return {"volume_percent": tgt, "was": cur}


def show_desktop() -> dict:
    """Minimize every window (Explorer's own Show Desktop). 'minimize everything'
    used to reach the model, which once answered by setting a CPU alert."""
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    win32com.client.Dispatch("Shell.Application").MinimizeAll()
    return {"minimized_all": True}


def restore_windows() -> dict:
    """Undo Show Desktop."""
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    win32com.client.Dispatch("Shell.Application").UndoMinimizeALL()
    return {"restored": True}


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


def take_screenshot(monitor: int = 0, hide_self: bool = True,
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
            _t.sleep(0.28)  # let the desktop repaint
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
    """Open a website for the USER, in their own default browser (Brave). This is for
    things the user wants to use - YouTube, Netflix, a shop. JARVIS's hidden browser is
    only for his own reading (browser_open / fetch_page / web_search)."""
    import os
    import subprocess
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    # JARVIS keeps a hidden Brave for its own reading, and Chromium is single-instance per
    # profile — so os.startfile hands the URL to THAT instance and the tab opens inside the
    # hidden window. "Open YouTube" then does nothing visible at all. Ask for an explicit
    # new window, positioned on screen, and bring it forward: this one is for him to use.
    try:
        from search_brave_web import _brave_path
        exe = _brave_path()
    except Exception:
        exe = None
    if exe:
        try:
            subprocess.Popen([exe, "--new-window", "--window-position=120,80",
                              "--window-size=1360,880", url], close_fds=True)
            # Report what actually happened. _focus_newest_browser_window
            # swallows a SetForegroundWindow failure and returns, and this said
            # "opened" regardless - a tool lying about its own result is worse
            # than the failure it is hiding.
            focused = _focus_newest_browser_window()
            return {"opened": url, "where": "your browser",
                    "focused": bool(focused)}
        except Exception as e:
            log.warning("could not open %s in a new Brave window: %s", url, e)
    try:
        os.startfile(url)
        return {"opened": url, "where": "your browser"}
    except Exception as e:
        return {"error": f"could not open {url}: {e}"}


def play_media(query: str, service: str = "youtube", play: bool = False) -> dict:
    """Find something to WATCH or LISTEN to, in his own browser.

    His instruction, verbatim: *"Any media searches should be done in my actual
    brave app."* Asked for a YouTube video, JARVIS ran a web search, let the model
    read the results and recited a URL into the side panel — a link he then had to
    go and open himself. A video is something you watch, not something you are
    told about.

    THE VERB DECIDES whether it plays or shows the shelf. This used to always
    open the search page, on the grounds that "being confidently wrong about
    which video he wanted is worse than showing him the shelf" - which is true
    of "find me a video of a rocket launch" and false of "play a video of the
    northern lights". His words: "if I say play something I expect him to
    actually play it for me too."

    Resolving the first result can fail - YouTube can change its markup, the
    network can be slow - and when it does this falls back to the search page,
    which is what it always did. It can only be better than before, never worse.
    """
    # Clean here too, not only in the brain: the LLM path passes whatever the
    # model wrote, which is usually the raw utterance. Same reason the image
    # tools call their cleaner.
    from tools.query_clean import clean_video_query
    q = clean_video_query(query or "")
    if not q:
        return {"error": "nothing to search for"}
    import urllib.parse
    where = (service or "youtube").strip().lower()
    sites = {
        "youtube": "https://www.youtube.com/results?search_query=",
        "spotify": "https://open.spotify.com/search/",
        "netflix": "https://www.netflix.com/search?q=",
    }
    base = sites.get(where, sites["youtube"])

    # PLAY MEANS PLAY. Only for YouTube: Spotify and Netflix need an account
    # session and a deep link, and guessing at those is a different problem.
    if play and where == "youtube":
        vid = _first_youtube_id(q)
        if vid:
            res = open_url(f"https://www.youtube.com/watch?v={vid}")
            if not res.get("error"):
                return {"playing": q, "service": where, "where": "your browser",
                        "video": vid, "focused": res.get("focused", False)}
        log.info("could not resolve a video for %r; showing the results", q)

    res = open_url(base + urllib.parse.quote(q))
    if res.get("error"):
        return res
    return {"searched": q, "service": where, "where": "your browser",
            "focused": res.get("focused", False)}


def _first_youtube_id(query: str) -> str | None:
    """The first video on YouTube's results page, or None to show the shelf.

    Deliberately forgiving: any failure at all returns None and the caller
    opens the search page, which is the behaviour that existed before.
    """
    import re as _re
    import urllib.parse
    import urllib.request
    try:
        url = ("https://www.youtube.com/results?search_query="
               + urllib.parse.quote(query))
        req = urllib.request.Request(url, headers={
            # Without a normal UA YouTube serves a consent wall with no results.
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/125.0 Safari/537.36"),
            "Accept-Language": "en-US,en;q=0.9"})
        with urllib.request.urlopen(req, timeout=10) as r:
            # The results page is about 1.4 MB and the first videoId sits
            # PAST 600 KB - capping there found nothing at all and silently
            # fell back to the shelf every time.
            html = r.read(2_500_000).decode("utf-8", "replace")
        # The top result's id, from the embedded JSON or a watch link.
        m = (_re.search(r'"videoId":"([A-Za-z0-9_-]{11})"', html)
             or _re.search(r"/watch\?v=([A-Za-z0-9_-]{11})", html))
        return m.group(1) if m else None
    except Exception:
        log.debug("could not resolve a youtube video", exc_info=True)
        return None


def open_application_sync(name: str) -> dict:
    """Launch by name, synchronously — play_music is not async."""
    import subprocess as _sp
    try:
        _sp.Popen(["cmd", "/c", "start", "", f"{name}:"],
                  creationflags=getattr(_sp, "CREATE_NO_WINDOW", 0))
        return {"launched": name}
    except Exception as e:
        log.debug("could not launch %s", name, exc_info=True)
        return {"error": str(e)}


def play_music(what: str = "") -> dict:
    """Put music on — in Spotify, not in whatever tab last held the media keys.

    "Play some music" used to reach media_control, which presses the Windows
    play/pause key. That key goes to whatever app owns the media session, which
    was a paused YouTube tab, so he asked for music and got a video.

    Spotify is where he listens, so Spotify is what gets opened; the play key
    then lands on it. Resuming is exactly "the last thing I was listening to".

    A NAMED playlist is refused rather than approximated. Knowing his library
    needs the Spotify Web API, an app registered under his account and a
    consent only he can give — and quietly playing something else instead is
    how "play some music" became a YouTube video in the first place.
    """
    import time as _t
    want = (what or "").strip()
    if want and not re.fullmatch(r"(?:some\s+)?music|something|anything",
                                 want, re.I):
        return {"error": "I can start Spotify and play, sir, but I can't pick a "
                         "playlist out of your library yet — that needs a "
                         "Spotify login you'd have to authorise",
                "spoken": "I can put Spotify on, sir, but I can't reach your "
                          "playlists yet — that needs a login you'd have to "
                          "authorise. Shall I just play?"}

    running = False
    try:
        for pr in psutil.process_iter(["name"]):
            if (pr.info["name"] or "").lower() == "spotify.exe":
                running = True
                break
    except Exception:
        log.debug("could not check for spotify", exc_info=True)

    if not running:
        r = open_application_sync("spotify")
        if r.get("error"):
            return {"error": "I couldn't start Spotify, sir"}
        # It has to be up and holding the media session before the key lands.
        _t.sleep(4.0)
    else:
        focus_window("Spotify")
        _t.sleep(0.4)

    media_control("play_pause")
    return {"playing": "spotify", "launched": not running,
            "spoken": ("Spotify's on, sir." if not running
                       else "Playing, sir.")}


def search_in_browser(query: str, kind: str = "web") -> dict:
    """A search in HIS browser, because he asked for it there specifically.

    The default for "show me iron man" is the HUD's own media panel, and that is
    deliberate — his words: *"it should show it in the OS, in the application we
    built, because it's meant to be an OS."* This is the escape hatch for when he
    says "...in my browser", and ONLY then. A picture he did not ask to be
    elsewhere belongs inside the thing he is building.
    """
    from tools.query_clean import clean_image_query, clean_search_query
    raw = (query or "").strip()
    if not raw:
        return {"error": "nothing to search for"}
    import urllib.parse
    if (kind or "web").lower() in ("image", "images", "picture", "pictures"):
        q, _count = clean_image_query(raw)
        url = "https://search.brave.com/images?q=" + urllib.parse.quote(q)
        kind = "images"
    else:
        q = clean_search_query(raw)
        url = "https://search.brave.com/search?q=" + urllib.parse.quote(q)
        kind = "web"
    res = open_url(url)
    if res.get("error"):
        return res
    return {"searched": q, "kind": kind, "where": "your browser",
            "focused": res.get("focused", False)}


def _focus_newest_browser_window() -> bool:
    """Bring the window we just opened for him to the front, and only that one.

    Returns whether it actually managed it. It used to return None on every
    path - including the one where SetForegroundWindow raised and the failure
    was swallowed - so open_url reported success it had not achieved.
    """
    import time as _t
    from search_brave_web import hidden_hwnds
    for _ in range(12):
        _t.sleep(0.4)
        hidden = hidden_hwnds()
        found = []

        def cb(hwnd, _unused):
            if hwnd in hidden or not win32gui.IsWindowVisible(hwnd):
                return True
            title = win32gui.GetWindowText(hwnd) or ""
            if "brave" not in title.lower():
                return True
            found.append(hwnd)
            return True

        try:
            win32gui.EnumWindows(cb, None)
        except Exception:
            return False
        if not found:
            continue
        hwnd = found[-1]
        try:
            l, t, _r, _b = win32gui.GetWindowRect(hwnd)
            if l <= -5000 or t <= -5000:      # inherited the hidden window's position
                win32gui.SetWindowPos(hwnd, 0, 120, 80, 1360, 880, win32con.SWP_NOZORDER)
            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            ctypes.windll.user32.keybd_event(win32con.VK_MENU, 0, 0, 0)
            try:
                win32gui.SetForegroundWindow(hwnd)
            finally:
                ctypes.windll.user32.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
        except Exception:
            log.debug("could not focus the new browser window", exc_info=True)
            return False
        return True
    return False


def enter_sleep_mode() -> dict:
    """Put JARVIS himself out of the way: minimise his own window(s).

    Not the same thing as power_action("sleep"), which suspends the PC. The ears stay
    open the whole time — the mic loop lives in this process, not in the window — so the
    wake word still brings him back.
    """
    n = 0
    for hwnd in _our_windows():
        try:
            if not win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
                n += 1
        except Exception:
            continue
    # AND PUT THE CAMERA DOWN. Told to go to sleep, he minimised himself and
    # left the webcam running. The ears stay open on purpose - that is what the
    # wake word needs - but the eyes have no reason to.
    released = False
    try:
        from hand_control import control
        control.disarm("told to sleep")
    except Exception:
        log.debug("could not stand the hand tracker down", exc_info=True)
    try:
        from camera import camera
        if camera.is_on:
            camera.stop()
            released = True
    except Exception:
        log.debug("could not release the camera for sleep", exc_info=True)
    return {"sleeping": True, "minimized": n, "camera_released": released}


def monitor_blank_after() -> int | None:
    """How long Windows waits before blanking the screen.

    Returns seconds, or 0 for "never blanks", or None for "could not read it".
    Those last two are NOT the same answer and collapsing them into 0 made a
    machine set to never blank look like a machine whose screen was off.

    Read from the ACTIVE power scheme rather than assumed, so it follows his own
    setting instead of a number hard-coded here that would drift the moment he
    changed it.
    """
    import uuid as _uuid

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                    ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]

        @classmethod
        def of(cls, text: str) -> "GUID":
            u = _uuid.UUID(text)
            g = cls()
            g.Data1, g.Data2, g.Data3 = u.time_low, u.time_mid, u.time_hi_version
            rest = u.bytes[8:]
            g.Data4 = (ctypes.c_ubyte * 8)(*rest)
            return g

    VIDEO_SUBGROUP = GUID.of("7516b95f-f776-4464-8c53-06167f40cc99")
    VIDEO_POWERDOWN = GUID.of("3c0bc021-c8a8-4e07-a973-6b14cbcb2b7e")
    powrprof = ctypes.windll.powrprof
    active = ctypes.POINTER(GUID)()
    if powrprof.PowerGetActiveScheme(None, ctypes.byref(active)) != 0:
        return None
    try:
        value = ctypes.c_ulong(0)
        # AC first (his is a desktop); fall back to battery for a laptop.
        for reader in (powrprof.PowerReadACValueIndex,
                       powrprof.PowerReadDCValueIndex):
            if reader(None, active, ctypes.byref(VIDEO_SUBGROUP),
                      ctypes.byref(VIDEO_POWERDOWN), ctypes.byref(value)) == 0 \
                    and value.value:
                return int(value.value)
        return 0
    finally:
        ctypes.windll.kernel32.LocalFree(active)


def display_is_off() -> bool:
    """Is the monitor actually dark right now?

    He was explicit: wake the screen ONLY if the screen is asleep. Windows has no
    plain "is the panel lit" call - the honest one, GUID_CONSOLE_DISPLAY_STATE,
    needs a window handle and a running message pump - so this infers it from the
    two things it can read exactly: how long since he last touched the machine,
    and how long his own power plan waits before blanking.

    Talking to JARVIS is not input, so the idle clock keeps running while he
    speaks - which is right: a dark screen he has been talking to for a minute is
    still a dark screen.
    """
    blank_after = monitor_blank_after()
    if blank_after is None:         # could not read the plan: use his setting
        blank_after = int(config.get("presence", "display_off_after_seconds",
                                     default=300) or 0)
    if blank_after <= 0:            # never blanks: there is nothing to wake
        return False
    from delivery import user_idle_seconds
    return user_idle_seconds() >= blank_after


def wake_display() -> dict:
    """Light the monitor back up.

    His monitor blanks after five minutes; the PC itself never sleeps. So the
    wake-word detector really is listening the whole time - it is explicitly fed
    while SLEEPING - and he can say the name to a dark screen and be heard. What
    he could not do was SEE the answer: JARVIS came back to the front of a
    monitor that was still off.

    Two mechanisms, because on their own neither is reliable:

      * SetThreadExecutionState(ES_DISPLAY_REQUIRED) tells Windows the display is
        needed, which resets the blank timer. On a panel that is already dark it
        often is not enough on its own.
      * a zero-distance mouse move. It is real input as far as Windows is
        concerned, which is what actually lights the panel, and moving by (0, 0)
        cannot disturb a drag, a selection, or anything he is doing if the screen
        was on after all.

    This CANNOT wake a machine that is properly asleep (S3) - at that point the
    microphone is off too and nothing is listening. It is for a blanked monitor
    on a running PC, which is his setup.
    """
    ES_CONTINUOUS = 0x80000000
    ES_DISPLAY_REQUIRED = 0x00000002
    did = []
    try:
        ctypes.windll.kernel32.SetThreadExecutionState(
            ES_CONTINUOUS | ES_DISPLAY_REQUIRED)
        # Pulse, not hold: holding it would stop his monitor ever blanking again.
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
        did.append("display-required")
    except Exception:
        log.debug("SetThreadExecutionState failed", exc_info=True)
    try:
        MOUSEEVENTF_MOVE = 0x0001
        ctypes.windll.user32.mouse_event(MOUSEEVENTF_MOVE, 0, 0, 0, 0)
        did.append("input-nudge")
    except Exception:
        log.debug("input nudge failed", exc_info=True)
    return {"woke_display": bool(did), "how": did}


def exit_sleep_mode() -> dict:
    """Bring him back and put him in front, from minimised or merely buried."""
    # The screen first. Restoring the window to a monitor that is still off is
    # what he actually hit: heard, answered, and invisible.
    if config.get("presence", "wake_display", default=True):
        # ONLY if the screen is actually dark - his instruction, and the right
        # one: a synthetic input event sent to a monitor that is already on is a
        # small liberty taken with a machine he might be using.
        # Never let the screen cost him the window either. Waking the monitor is
        # the nicety; coming back to the front when he calls is the feature, and
        # a display driver having a bad day must not take that with it.
        try:
            if display_is_off():
                wake_display()
        except Exception:
            log.debug("could not wake the display", exc_info=True)
    found = 0
    for hwnd in _our_windows():
        try:
            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            else:
                win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
            # Windows refuses SetForegroundWindow to a process without input focus;
            # the synthetic ALT press is the standard nudge that grants it.
            ctypes.windll.user32.keybd_event(win32con.VK_MENU, 0, 0, 0)
            try:
                win32gui.SetForegroundWindow(hwnd)
            finally:
                ctypes.windll.user32.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
            found += 1
        except Exception:
            continue
    return {"sleeping": False, "restored": found}


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
        risk=Risk.LOW, handler=close_window))
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
        name="adjust_volume",
        description="Change volume RELATIVE to where it is: 'turn it up', 'a bit louder', "
                    "'be quieter', 'turn it down'. direction up|down, step in percent.",
        parameters={"type": "object", "properties": {
            "direction": {"type": "string", "enum": ["up", "down"]},
            "step": {"type": "integer", "minimum": 1, "maximum": 50}},
            "required": ["direction"]},
        risk=Risk.LOW, handler=adjust_volume))
    registry.register(T(
        name="show_desktop",
        description="Minimize every window to show the desktop ('minimize everything', "
                    "'hide all my windows', 'show me my desktop').",
        parameters={"type": "object", "properties": {}, "required": []},
        risk=Risk.LOW, handler=show_desktop))
    registry.register(T(
        name="restore_windows",
        description="Undo Show Desktop — bring the minimized windows back.",
        parameters={"type": "object", "properties": {}, "required": []},
        risk=Risk.LOW, handler=restore_windows))
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
            "monitor": {"type": "integer", "minimum": 0},
            "hide_self": {"type": "boolean", "description": "hide the JARVIS window from the shot (default true)"}}, "required": []},
        risk=Risk.LOW, handler=take_screenshot))
    registry.register(T(
        name="open_url",
        description="Open a website in the USER's own browser so they can use it (YouTube, "
                    "Netflix, shopping...). To read or inspect a page yourself, use browser_open "
                    "or fetch_page instead - never this.",
        parameters={"type": "object", "properties": {
            "url": {"type": "string"}}, "required": ["url"]},
        risk=Risk.LOW, handler=open_url))
    registry.register(T(
        name="play_music",
        description="Put MUSIC on, in Spotify. Use for 'play some music', 'put "
                    "some music on', 'play my music'. Launches or focuses the "
                    "Spotify desktop app and presses play, so the media key "
                    "lands on Spotify rather than on whatever browser tab last "
                    "held the media session. Does NOT know his library: a named "
                    "playlist is refused with a reason, never approximated.",
        parameters={"type": "object", "properties": {
            "what": {"type": "string",
                     "description": "leave empty for 'some music'; a named "
                                    "playlist is refused with an explanation"}},
            "required": []},
        risk=Risk.LOW, handler=play_music, timeout=20))
    registry.register(T(
        name="play_media",
        description="Find a VIDEO, song or film for the user to watch or listen to, opened "
                    "in their OWN browser. Use this for anything playable - 'find me a "
                    "youtube video of...', 'play some jazz', 'find the trailer for...'. "
                    "Set play=true when he said PLAY or put on or watch, and it opens "
                    "the first result instead of the results page. "
                    "NEVER answer a request for something to watch with web_search and a "
                    "recited link: he wants it open, not read out.",
        parameters={"type": "object", "properties": {
            "query": {"type": "string", "description": "what to find, e.g. 'iron man ps3 gameplay'"},
            "service": {"type": "string",
                        "description": "youtube (default) | spotify | netflix"},
            "play": {"type": "boolean",
                     "description": "true when he said PLAY/put on/watch — opens "
                                    "the first result directly. false when he "
                                    "said find/search/look for — shows the "
                                    "results page so he can choose."}},
            "required": ["query"]},
        risk=Risk.LOW, handler=play_media))
    registry.register(T(
        name="search_in_browser",
        description="Run a web or image search in the USER's own browser. ONLY when he asks "
                    "for it there — 'show me X in my browser', 'look that up in brave'. The "
                    "default home for pictures and search results is the HUD itself "
                    "(show_images / web_search); this is the explicit exception, never the "
                    "default.",
        parameters={"type": "object", "properties": {
            "query": {"type": "string"},
            "kind": {"type": "string", "description": "web (default) | images"}},
            "required": ["query"]},
        risk=Risk.LOW, handler=search_in_browser))
    registry.register(T(
        name="enter_sleep_mode",
        description="Dismiss JARVIS himself: minimise his window and stand by for the wake "
                    "word. ONLY for an explicit dismissal addressed to you - 'go to sleep', "
                    "'sleep mode', \"that's all for now\", 'stand down'. NEVER call this "
                    "merely because sleep was mentioned: a question ABOUT sleep, bedtime or "
                    "insomnia is a question to answer, not an instruction to go away. NOT for "
                    "suspending the PC - that is power_action('sleep').",
        parameters={"type": "object", "properties": {}, "required": []},
        risk=Risk.SAFE, handler=enter_sleep_mode))
    registry.register(T(
        name="exit_sleep_mode",
        description="Bring the JARVIS window back and put it in front.",
        parameters={"type": "object", "properties": {}, "required": []},
        risk=Risk.SAFE, handler=exit_sleep_mode))
    registry.register(T(
        name="lock_computer",
        description="Lock the Windows session immediately.",
        parameters={"type": "object", "properties": {}, "required": []},
        risk=Risk.LOW, handler=lock_computer))
    registry.register(T(
        name="power_action",
        description="Sleep, shut down, or restart the computer.",
        parameters={"type": "object", "properties": {
            "action": {"type": "string", "enum": ["sleep", "shutdown", "restart"]}},
            "required": ["action"]},
        risk=Risk.HIGH, handler=power_action))
