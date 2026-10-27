"""Tools that say what happened, not what they tried.

Five confident lies, each fixed and each pinned here:

  * a locked screen captures as one flat colour and "screenshot saved" was
    announced over a black rectangle;
  * a clipboard that could not be OPENED was reported as EMPTY;
  * "sleeping now" was spoken after spawning rundll32 blind, with no way of
    knowing whether Windows agreed; "Locking." ignored LockWorkStation's BOOL;
  * read_file resolved "desktop/x.txt" without saying under which root, and
    truncated silently.

Hermetic: the screen grabber and the clipboard are stand-ins, the power calls
are stubbed (the real ones would lock or sleep the build machine), and the
read roots point at a temp folder. Run: python tests/test_honest_tools.py
"""
import os
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "honest.db"))

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


# ------------------------------------------------------------ stand-ins

class _Img:
    def __init__(self, w, h, rgb_bytes):
        self.size = (w, h)
        self.rgb = rgb_bytes


class _FakeMss:
    def __init__(self, img):
        self.monitors = [{"left": 0, "top": 0, "width": img.size[0], "height": img.size[1]}]
        self._img = img

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def grab(self, mon):
        return self._img


def _install_fake_mss(img):
    mod = types.ModuleType("mss")
    tools = types.ModuleType("mss.tools")

    def to_png(rgb, size, output=None):
        Path(output).write_bytes(b"\x89PNG" + bytes(rgb[:64]))

    tools.to_png = to_png
    mod.mss = lambda: _FakeMss(img)
    mod.tools = tools
    saved = (sys.modules.get("mss"), sys.modules.get("mss.tools"))
    sys.modules["mss"], sys.modules["mss.tools"] = mod, tools
    return saved


def _restore_mss(saved):
    for name, m in zip(("mss", "mss.tools"), saved):
        if m is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = m


class _FakeClipboard:
    """win32clipboard that fails to open N times, then holds `text`."""
    CF_UNICODETEXT = 13

    def __init__(self, fail_times: int, text: str = ""):
        self.fail_times, self.text, self.opens, self.closes = fail_times, text, 0, 0

    def OpenClipboard(self):
        self.opens += 1
        if self.opens <= self.fail_times:
            raise OSError(0, "OpenClipboard", "The operation completed successfully.")

    def GetClipboardData(self, fmt):
        return self.text

    def CloseClipboard(self):
        self.closes += 1

    def EmptyClipboard(self):
        pass

    def SetClipboardText(self, text, fmt):
        self.text = text


def _with_clipboard(fake, fn):
    saved = sys.modules.get("win32clipboard")
    sys.modules["win32clipboard"] = fake
    try:
        return fn()
    finally:
        if saved is None:
            sys.modules.pop("win32clipboard", None)
        else:
            sys.modules["win32clipboard"] = saved


def main() -> int:
    from tools import windows_tools as W
    from brain.skills import say_clipboard, say_lock, say_screenshot

    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    print("\n-- a flat frame is a blank capture, not a screenshot --")
    w, h = 320, 200
    black = _Img(w, h, bytes(3 * w * h))
    check("_uniform_frame: all black is uniform", W._uniform_frame(black.rgb))
    grey = _Img(w, h, bytes([90, 90, 90]) * (w * h))
    check("...and so is a flat grey (a powered-down display)", W._uniform_frame(grey.rgb))
    busy = bytearray(bytes(3 * w * h))
    busy[3 * (w * 150 + 77) + 1] = 200           # one green pixel, on the sample grid
    check("one pixel that differs is enough: not uniform",
          not W._uniform_frame(bytes(busy), step=1))
    check("an empty frame counts as blank", W._uniform_frame(b""))

    tmp = Path(tempfile.mkdtemp(prefix="jarvis-shot-"))
    saved = _install_fake_mss(black)
    try:
        res = W.take_screenshot(hide_self=False, destination=str(tmp), filename="locked")
    finally:
        _restore_mss(saved)
    check("a black grab returns an error, not a path",
          "error" in res and "path" not in res, str(res))
    check("...that says the screen is locked or off",
          res.get("error") == W.BLANK_CAPTURE, str(res.get("error")))
    check("...names the folder it would have used", res.get("folder") == str(tmp))
    check("...and writes nothing", not (tmp / "locked.png").exists())
    line = say_screenshot({}, res)
    check("the skill speaks the reason, not 'I couldn't'",
          "blank" in line and "locked" in line, line)

    noise = bytearray(bytes(3 * w * h))
    for i in range(0, len(noise), 7):
        noise[i] = (i * 31) % 251
    saved = _install_fake_mss(_Img(w, h, bytes(noise)))
    try:
        res = W.take_screenshot(hide_self=False, destination=str(tmp), filename="real")
    finally:
        _restore_mss(saved)
    check("a real frame is saved and confirmed from disk",
          res.get("path") and Path(res["path"]).exists() and res.get("bytes", 0) > 0, str(res))
    check("...and reports the real folder", res.get("folder") == str(tmp), str(res.get("folder")))
    check("...and the skill stays silent about it (the picture says it)",
          say_screenshot({}, res) == "")

    tg = open(os.path.join(here, "remote_telegram.py"), encoding="utf-8").read()
    check("the Telegram photo path skips a result with an error",
          'got.get("error")' in tg and 'got.get("path")' in tg)

    print("\n-- the clipboard: unreachable is not empty --")
    fake = _FakeClipboard(fail_times=99)
    res = _with_clipboard(fake, W.get_clipboard)
    check("three tries before giving up", fake.opens == 3, str(fake.opens))
    check("the result is an error, not an empty text",
          "error" in res and "text" not in res, str(res))
    check("...in words he can hear, not a WinError",
          res["error"] == W.CLIPBOARD_UNREACHABLE and "WinError" not in res["error"],
          str(res["error"]))
    check("...and the skill says so instead of 'empty'",
          say_clipboard({}, res) == "I can't reach the clipboard; the screen may be locked.",
          say_clipboard({}, res))
    fake = _FakeClipboard(fail_times=1, text="hello there")
    res = _with_clipboard(fake, W.get_clipboard)
    check("one transient failure is retried and the text comes back",
          res.get("text") == "hello there" and fake.closes == 1, str(res))
    check("...with its real length", res.get("length") == 11)
    check("empty really is empty",
          say_clipboard({}, {"text": "", "truncated": False}) == "Your clipboard is empty.")
    fake = _FakeClipboard(fail_times=99)
    res = _with_clipboard(fake, lambda: W.set_clipboard("x"))
    check("set_clipboard reports the same unreachable error",
          res.get("error") == W.CLIPBOARD_UNREACHABLE, str(res))

    print("\n-- power: report what Windows said, never what was asked --")
    src = open(os.path.join(here, "tools", "windows_tools.py"), encoding="utf-8").read()
    check("no more rundll32 for sleep", "rundll32.exe" not in src and "subprocess.Popen" in src)
    check("SetSuspendState is called directly", "powrprof.SetSuspendState(0, 1, 0)" in src)
    real_ctypes = W.ctypes
    calls = []

    def _stub(ret):
        def f(*a):
            calls.append(a)
            return ret
        return f

    try:
        W.ctypes = types.SimpleNamespace(
            windll=types.SimpleNamespace(user32=types.SimpleNamespace(LockWorkStation=_stub(0)),
                                         powrprof=types.SimpleNamespace(SetSuspendState=_stub(0))),
            get_last_error=lambda: 5)
        res = W.lock_computer()
        check("LockWorkStation FALSE -> an error and locked=False",
              "error" in res and res.get("locked") is False, str(res))
        check("...and the skill does not say 'Locking.'", say_lock({}, res) != "Locking.")
        res = W.power_action("sleep")
        check("SetSuspendState FALSE -> an error, not 'sleeping now'",
              "error" in res and "refused" in res["error"], str(res))
        W.ctypes = types.SimpleNamespace(
            windll=types.SimpleNamespace(user32=types.SimpleNamespace(LockWorkStation=_stub(1)),
                                         powrprof=types.SimpleNamespace(SetSuspendState=_stub(1))),
            get_last_error=lambda: 0)
        check("LockWorkStation TRUE -> locked", W.lock_computer() == {"locked": True})
        res = W.power_action("sleep")
        check("SetSuspendState TRUE -> accepted", res.get("accepted") is True and "error" not in res,
              str(res))
        check("...with the (0, 1, 0) arguments: suspend, forced, no wake events",
              calls[-1] == (0, 1, 0), str(calls[-1]))
    finally:
        W.ctypes = real_ctypes
    check("an unknown action is still refused", "error" in W.power_action("dance"))

    print("\n-- read_file: which root, and how much of it --")
    from config import config
    from tools import builtin
    saved_folders = dict(config.data.get("folders") or {})
    root = Path(tempfile.mkdtemp(prefix="jarvis-read-"))
    try:
        config.data["folders"] = {**saved_folders, "desktop": str(root)}
        big = root / "big.txt"
        big.write_text("x" * 10_000, "utf-8")
        res = builtin.read_file("desktop/big.txt", max_chars=4000)
        check("'desktop/big.txt' resolves under the desktop root and says so",
              res.get("root") == "desktop" and res.get("path") == str(big.resolve()), str(res)[:160])
        check("...and reports first 4,000 of 10,000 characters",
              res.get("note") == "first 4,000 of 10,000 characters"
              and res.get("shown") == 4000 and res.get("chars") == 10_000
              and res.get("truncated") is True and len(res["content"]) == 4000, str(res.get("note")))
        res = builtin.read_file("big.txt", max_chars=20_000)
        check("a bare name is searched across the roots and the match is named",
              res.get("root") == "desktop" and "note" not in res and res.get("truncated") is False,
              str(res)[:160])
        res = builtin.read_file("desktop\\nothere.txt")
        check("a missing file says where it looked",
              "file not found" in res.get("error", "") and "desktop" in res.get("error", ""),
              str(res))
        outside = Path(tempfile.mkdtemp()) / "secret.txt"
        outside.write_text("no", "utf-8")
        res = builtin.read_file(str(outside))
        check("outside the roots is refused, and the allowed roots are listed",
              "not permitted" in res.get("error", "") and "desktop (" in res.get("error", ""),
              str(res)[:200])
    finally:
        config.data["folders"] = saved_folders

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
