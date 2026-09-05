"""Dictation is text, not conversation. Offline: no mic, no clipboard, no typing.
Run: python tests/test_dictation.py"""
import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "gate.db"))
from dictation import Dictation, clean_for_text  # noqa: E402
from state_machine import State  # noqa: E402

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


class FakeSM:
    def __init__(self, state):
        self.state = state


class FakeOrch:
    def __init__(self, state):
        self.sm = FakeSM(state)


def main() -> int:
    # --- spoken words become written text -----------------------------------
    check("spoken punctuation lands",
          clean_for_text("send it to bob comma then call me period")
          == "send it to bob, then call me.")
    check("new paragraph is a real break",
          clean_for_text("one new paragraph two") == "one\n\ntwo")
    check("no stray spaces around a break",
          "\n " not in clean_for_text("one new line two"))
    check("fillers are dropped", clean_for_text("um so it works") == "so it works")
    check("no space before punctuation",
          clean_for_text("hello   world  period") == "hello world.")
    check("empty stays empty", clean_for_text("   ") == "")
    check("plain speech is left alone",
          clean_for_text("the quarterly numbers look strong")
          == "the quarterly numbers look strong")

    # --- it must never fight a real turn for the microphone ------------------
    d = Dictation()
    for state in (State.LISTENING, State.SPEAKING, State.THINKING, State.EXECUTING):
        d.orchestrator = FakeOrch(state)
        r = asyncio.run(d.start())
        check(f"refuses while {state.value}", r.get("ok") is False and not d.active, r)

    # --- stop without start is an error, not a crash -------------------------
    d2 = Dictation()
    d2.orchestrator = FakeOrch(State.IDLE)
    check("stop before start is refused", asyncio.run(d2.stop()).get("ok") is False)
    check("nothing is left recording", d2.active is False)

    # --- how the words reach the document -----------------------------------
    # Typed for anything short, as Unicode key events: no clipboard borrowed,
    # nothing to give back, nothing to race. On 2026-09-05 the paste landed
    # AFTER the clipboard had been restored and a shell script went into
    # Notepad instead of the sentence, with "pasted: True".
    import dictation as dm
    import keys
    import win32clipboard
    clip = {"text": "his own copy", "sets": []}
    typed = []
    real = (keys.type_text, keys.press, win32clipboard.OpenClipboard, win32clipboard.CloseClipboard,
            win32clipboard.EmptyClipboard, win32clipboard.SetClipboardText,
            win32clipboard.GetClipboardData, win32clipboard.IsClipboardFormatAvailable, dm.time.sleep)
    keys.type_text = lambda t, per_char_delay=0.0: (typed.append(t), True)[1]
    keys.press = lambda vk, mods=(): True
    win32clipboard.OpenClipboard = lambda: None
    win32clipboard.CloseClipboard = lambda: None
    win32clipboard.EmptyClipboard = lambda: None
    win32clipboard.IsClipboardFormatAvailable = lambda f: True
    win32clipboard.GetClipboardData = lambda f: clip["text"]

    def _set(v, f=None):
        clip["text"] = v
        clip["sets"].append(v)
    win32clipboard.SetClipboardText = _set
    dm.time.sleep = lambda s: None
    try:
        # Pasted by default: one keystroke, atomic. (Typing from the busy
        # sidecar lost and repeated letters; the ALT-tap focus bug that made
        # the paste LOOK broken is fixed in windows_tools.bring_to_front.)
        ok = dm._paste("The quarterly numbers look strong.")
        check("a dictation is pasted, not typed", ok and typed == []
              and clip["sets"][:1] == ["The quarterly numbers look strong."], (typed, clip["sets"]))
        check("...and his own copy is given back", clip["text"] == "his own copy", clip["text"])
        # ...unless he asks for typing, for an app that refuses a paste
        from config import config as _cfg
        _cfg.data.setdefault("dictation", {})["prefer_typing"] = True
        clip["sets"].clear()
        ok = dm._paste("The quarterly numbers look strong.")
        check("with prefer_typing it is typed", ok and typed == ["The quarterly numbers look strong."], typed)
        check("...and the clipboard is never touched", clip["sets"] == [] and clip["text"] == "his own copy",
              clip)
        long = "word " * 200
        typed.clear()
        ok = dm._paste(long)
        check("a long one goes through the clipboard even then", ok and typed == [] and clip["sets"][:1] == [long],
              (typed, len(clip["sets"])))
        _cfg.data["dictation"]["prefer_typing"] = False
        check("...and his own copy is given back", clip["text"] == "his own copy", clip["text"])
        # he copied something else while the app was reading the clipboard
        clip["sets"].clear()
        win32clipboard.GetClipboardData = lambda f: "something he just copied" if clip["sets"] else "his own copy"
        ok = dm._paste(long)
        check("...but never over something he copied meanwhile",
              ok and clip["sets"] == [long], [x[:30] for x in clip["sets"]])
        win32clipboard.GetClipboardData = lambda f: clip["text"]
        # typing that does not fully deliver falls back to the clipboard
        keys.type_text = lambda t, per_char_delay=0.0: False
        clip["sets"].clear()
        clip["text"] = "his own copy"
        ok = dm._paste("short")
        check("typing that fails falls back to a paste", ok and clip["sets"][:1] == ["short"], clip["sets"])
    finally:
        (keys.type_text, keys.press, win32clipboard.OpenClipboard, win32clipboard.CloseClipboard,
         win32clipboard.EmptyClipboard, win32clipboard.SetClipboardText,
         win32clipboard.GetClipboardData, win32clipboard.IsClipboardFormatAvailable, dm.time.sleep) = real

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
