"""Dictation: hold a key, speak, release, and the words land in whatever app has
focus. A local replacement for a paid cloud dictation subscription.

Deliberately NOT a conversation. Nothing here reaches the brain, the LLM, the
fact store or the transcript — he does not answer, does not speak, does not
remember. It is Parakeet plus the clipboard, and it must never steal the mic
from a real turn, so it refuses while he is listening or talking.

Insertion is clipboard + Ctrl+V rather than synthetic per-character typing:
typing a paragraph key by key takes seconds, drops characters in apps that
throttle input, and mangles anything with a keyboard layout of its own. The
previous clipboard contents are restored afterwards.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time

import numpy as np

from audio.io import mic
from audio.stt import stt
from config import config
from events import bus
from state_machine import State

log = logging.getLogger("jarvis.dictation")

MAX_SECONDS = 120           # a runaway hold must not record forever

# Spoken punctuation people actually use while dictating.
_SPOKEN = [
    (r"\bnew paragraph\b", "\n\n"), (r"\bnew line\b", "\n"),
    (r"\bfull stop\b", "."), (r"\bperiod\b", "."), (r"\bcomma\b", ","),
    (r"\bquestion mark\b", "?"), (r"\bexclamation (?:mark|point)\b", "!"),
    (r"\bcolon\b", ":"), (r"\bsemicolon\b", ";"), (r"\bopen paren(?:thesis)?\b", "("),
    (r"\bclose paren(?:thesis)?\b", ")"), (r"\bdash\b", "—"),
]
# Filler people say while thinking, which nobody wants in written text.
_FILLER = re.compile(r"\b(?:um|uh|erm|hmm|uhh|ahh)\b[,.]?\s*", re.I)


def clean_for_text(raw: str) -> str:
    """Spoken words -> written text: punctuation commands, no fillers, tidy spaces."""
    t = (raw or "").strip()
    if not t:
        return ""
    if config.get("dictation", "strip_fillers", default=True):
        t = _FILLER.sub("", t)
    if config.get("dictation", "spoken_punctuation", default=True):
        for pat, rep in _SPOKEN:
            t = re.sub(pat, rep, t, flags=re.I)
    t = re.sub(r"\s+([.,!?;:])", r"\1", t)          # no space before punctuation
    t = re.sub(r"[ \t]{2,}", " ", t)
    t = re.sub(r"[ \t]*\n[ \t]*", "\n", t)   # no stray spaces around a break
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


class Dictation:
    """One hold-to-talk session at a time."""

    def __init__(self) -> None:
        self.active = False
        self._q = None                    # our OWN mic subscription, see start()
        self._buf: list[np.ndarray] = []
        self._task: asyncio.Task | None = None
        self._started = 0.0
        self.orchestrator = None          # set at startup

    def _busy_reason(self) -> str | None:
        o = self.orchestrator
        if o is None:
            return None
        if o.sm.state in (State.LISTENING, State.PROCESSING, State.THINKING,
                          State.SEARCHING, State.EXECUTING, State.SPEAKING,
                          State.WAITING):
            return "JARVIS is mid-turn — dictation would fight him for the microphone"
        return None

    async def start(self) -> dict:
        if not config.get("dictation", "enabled", default=True):
            return {"ok": False, "error": "dictation is switched off in Settings"}
        if self.active:
            return {"ok": True, "already": True}
        busy = self._busy_reason()
        if busy:
            return {"ok": False, "error": busy}
        self.active = True
        self._buf = []
        self._started = time.time()
        # A private subscription, not mic.queue: the capture loop drinks from
        # that same queue, so a wake word inside the dictated speech would have
        # the two of them splitting the blocks between them and each getting
        # half a sentence.
        self._q = mic.subscribe()
        self._task = asyncio.create_task(self._record())
        await bus.emit("dictation", stage="listening")
        return {"ok": True}

    async def _record(self) -> None:
        try:
            while self.active and time.time() - self._started < MAX_SECONDS:
                try:
                    block = await asyncio.wait_for(self._q.get(), timeout=0.4)
                except asyncio.TimeoutError:
                    continue
                self._buf.append(block)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("dictation recording failed")
        if self.active:
            # THE DEADLINE ENDS THE SESSION, NOT JUST THE RECORDING. Hitting
            # MAX_SECONDS used to leave `active` True with two minutes of audio
            # in the buffer: the wake loop skips every block while dictation is
            # active, so "hey JARVIS" was ignored indefinitely — and the NEXT
            # hotkey release transcribed the whole two minutes and pasted them
            # into whatever had focus. The lost release (a UAC prompt, an
            # elevated window in front, a sidecar restart mid-hold) is not
            # rare enough for that.
            log.warning("dictation ran %ds with no release — ending it", MAX_SECONDS)
            self.active = False
            self._task = None
            if self._q is not None:
                mic.unsubscribe(self._q)
                self._q = None
            self._buf = []
            await bus.emit("dictation", stage="cancelled", reason="too long")

    async def stop(self) -> dict:
        """Release: transcribe what was said and paste it where the cursor is."""
        if not self.active:
            return {"ok": False, "error": "not dictating"}
        self.active = False
        if self._task:
            self._task.cancel()
            self._task = None
        if self._q is not None:
            mic.unsubscribe(self._q)
            self._q = None
        secs = time.time() - self._started
        if not self._buf or secs < 0.35:
            await bus.emit("dictation", stage="cancelled", reason="too short")
            return {"ok": False, "error": "nothing recorded"}
        audio = np.concatenate(self._buf)
        self._buf = []
        await bus.emit("dictation", stage="transcribing", seconds=round(secs, 1))
        try:
            raw = await stt.transcribe(audio)
        except Exception:
            log.exception("dictation transcribe failed")
            await bus.emit("dictation", stage="error")
            return {"ok": False, "error": "I couldn't make out the audio"}
        text = clean_for_text(raw or "")
        if not text:
            await bus.emit("dictation", stage="cancelled", reason="nothing heard")
            return {"ok": False, "error": "nothing heard"}
        pasted = await asyncio.to_thread(_paste, text)
        await bus.emit("dictation", stage="done", text=text,
                       seconds=round(secs, 1), pasted=pasted)
        log.info("dictation: %.1fs -> %d chars (%s)", secs, len(text),
                 "pasted" if pasted else "clipboard only")
        return {"ok": True, "text": text, "pasted": pasted,
                "seconds": round(secs, 1), "words": len(text.split())}


def _paste(text: str) -> bool:
    """Put the text where the cursor is, then give the clipboard back."""
    import win32api
    import win32clipboard
    import win32con

    def _get_clip() -> str | None:
        try:
            win32clipboard.OpenClipboard()
            try:
                if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                    return win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
            finally:
                win32clipboard.CloseClipboard()
        except Exception:
            return None
        return None

    def _set_clip(value: str) -> bool:
        for _ in range(5):                     # the clipboard is shared: retry briefly
            try:
                win32clipboard.OpenClipboard()
                try:
                    win32clipboard.EmptyClipboard()
                    win32clipboard.SetClipboardText(value, win32con.CF_UNICODETEXT)
                    return True
                finally:
                    win32clipboard.CloseClipboard()
            except Exception:
                time.sleep(0.05)
        return False

    previous = _get_clip()
    if not _set_clip(text):
        return False
    try:
        # THROUGH keys.press, NOT keybd_event. The hotkey is Ctrl+Shift+D held
        # down; the release fires when D comes up and the transcript is back
        # in ~140 ms, so his fingers are still on Ctrl and Shift when this
        # runs. keybd_event added a Ctrl of its own on top of the physical
        # ones and the app received Ctrl+Shift+V — paste-as-plain-text in
        # Chrome, "paste formatting" in Word, nothing at all in the document —
        # while this returned True because a keystroke had been sent. keys.press
        # waits for the physical modifiers to lift, sends a real scan code,
        # and puts the whole combination in one SendInput call.
        import keys
        if not keys.press(ord("V"), mods=(keys.VK_CONTROL,)):
            log.warning("paste keystroke was not delivered; text is on the clipboard")
            return False
    except Exception:
        log.warning("paste keystroke failed; text is on the clipboard", exc_info=True)
        return False
    if previous is not None and config.get("dictation", "restore_clipboard", default=True):
        # 1.5s, NOT 0.35. Ctrl+V does not paste anything — it tells the app to go
        # and read the clipboard, and the app does that whenever it gets round to
        # it. Putting the old contents back 350ms later beat modern Notepad to
        # it, so the dictated sentence went nowhere while this still returned
        # True, because the keystroke HAD been sent.
        #
        # Measured on his machine, same window, one trial each:
        #
        #     no restore     LANDED
        #     restore 0.35s  LOST      <- what was shipping
        #     restore 1.5s   LANDED
        #
        # This is why hands_e2e could not prove dictation reached the document:
        # it never did. The receipt was looking at the wrong property AND the
        # thing it was looking for was genuinely absent.
        #
        # The cost of the longer wait is that his clipboard is borrowed for a
        # second and a half instead of a third of one, on a background thread.
        # The cost of the shorter one was losing what he said.
        time.sleep(float(config.get("dictation", "clipboard_restore_delay_s",
                                    default=1.5)))
        _set_clip(previous)
    return True


dictation = Dictation()
