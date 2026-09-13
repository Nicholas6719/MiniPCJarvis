"""Wake-word detection — openWakeWord's pretrained "hey jarvis" (ONNX, CPU).

Measured on this machine: ~1.8 ms per 80 ms chunk (~2% of one core).
"""
from __future__ import annotations

import logging
import re
import threading

import numpy as np

from config import config

log = logging.getLogger("jarvis.wake")

CHUNK = 1280  # 80 ms @ 16 kHz — openWakeWord's recommended frame


class WakeWord:
    def __init__(self) -> None:
        self._model = None
        self._buf = np.zeros(0, dtype=np.float32)
        self._lock = threading.Lock()   # per instance: see feed()
        self.dropped = 0

    def _ensure(self):
        if self._model is None:
            from openwakeword.model import Model
            log.info("loading hey_jarvis wake model")
            self._model = Model(wakeword_models=["hey_jarvis"],
                                inference_framework="onnx")
        return self._model

    def warmup(self) -> None:
        self._ensure()

    @property
    def threshold(self) -> float:
        return float(config.get("wake", "threshold", default=0.45))

    def reset(self) -> None:
        self._buf = np.zeros(0, dtype=np.float32)
        if self._model is not None:
            try:
                self._model.reset()
            except Exception:
                # If the model will not clear, JARVIS's own name can linger in
                # its window and the next "hey JARVIS" is swallowed. Being
                # unable to hear him is the failure he minds most, and it has
                # already happened twice — it must never be silent as well.
                log.debug("wake model reset failed", exc_info=True)

    # ONE FEEDER PER INSTANCE. openWakeWord keeps a streaming buffer inside the
    # model, so two callers feeding one instance interleave their audio into
    # each other's window - and on 2026-09-07 five threads were inside
    # `predict` at once, the sidecar burning 2.3 cores while "sleeping".
    #
    # The lock is per-instance and non-blocking: a second feeder DROPS its
    # 80 ms rather than queueing. That is right when the second feeder is a
    # duplicate, and wrong when it is a different listener - which is why the
    # barge-in watcher has its own instance (`barge`) rather than sharing this
    # one. Sharing cost the barge-in entirely: the wake loop won every block
    # during speech and the watcher heard nothing (bargein_e2e, 2026-09-08).
    dropped = 0

    def feed(self, audio_f32: np.ndarray) -> float:
        """Feed float32 [-1,1] 16 kHz audio; returns max hey_jarvis score seen."""
        if not self._lock.acquire(blocking=False):
            self.dropped += 1
            return 0.0
        try:
            model = self._ensure()
            self._buf = np.concatenate([self._buf, audio_f32.ravel()])
            # A feeder that fell behind must not replay seconds of stale
            # audio: keep the newest quarter second and let the rest go.
            if len(self._buf) > 4 * CHUNK:
                self._buf = self._buf[-3 * CHUNK:]
            best = 0.0
            while len(self._buf) >= CHUNK:
                frame = self._buf[:CHUNK]
                self._buf = self._buf[CHUNK:]
                int16 = (np.clip(frame, -1, 1) * 32767).astype(np.int16)
                scores = model.predict(int16)
                best = max(best, float(scores.get("hey_jarvis", 0.0)))
            return best
        finally:
            self._lock.release()


# --- THE NAME, AS THE RECOGNISER HEARS IT ------------------------------------
# The model above fires on the SOUND of "hey jarvis" - and on television: in
# the five days after release 63 it fired 22 times, at 0.60 to 1.00, and almost
# none of them were him ("Yeah. But Daniel, he isn't happy" scored 1.00). No
# threshold separates those from his voice, because the scores overlap
# completely. The WORDS separate them: after the model fires, Parakeet is
# asked whether the name is in the pre-roll, and only then does the screen
# come on. 48 of 48 synthetic clips in 93 ms median (2026-09-13); every miss
# in his log is one edit away ("Travis", "Jovis"), so those are spelled out.
NAME = "jarvis"
NAME_TOKENS = frozenset({
    "jarvis", "jarves", "jarvus", "jovis", "jervis", "javis", "jarvi", "jarvas",
    "jarbis", "jarvez", "jarvie", "jarvys", "jarvish", "jarvess", "jarviss",
    "charvis", "garvis", "harvis", "darvis", "travis",
})


def _dl(a: str, b: str) -> int:
    """Damerau-Levenshtein (optimal string alignment): one edit for a
    substitution, an insertion, a deletion, or swapping two neighbours."""
    la, lb = len(a), len(b)
    d = [[0] * (lb + 1) for _ in range(la + 1)]
    for i in range(la + 1):
        d[i][0] = i
    for j in range(lb + 1):
        d[0][j] = j
    for i in range(1, la + 1):
        for j in range(1, lb + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[la][lb]


def _is_name(word: str, loose: bool = True) -> bool:
    """One token. `loose` admits the recogniser's known mishearings ("travis");
    strict admits only the name and a one-edit slip of it ("jarvi", "jarvus")."""
    w = word.lower()
    if w == NAME:
        return True
    if loose and w in NAME_TOKENS:
        return True
    # ONE edit, not two: two admits "harris", "marvin", "elvis" - names the
    # television says. The mishearings two edits away are listed above.
    return 5 <= len(w) <= 7 and _dl(w, NAME) <= 1


def name_in(text: str) -> bool:
    """Is his name anywhere in this transcript, as Parakeet tends to write it?"""
    return any(_is_name(w) for w in re.findall(r"[a-z]+", (text or "").lower()))


def strip_wake_name(text: str) -> str:
    """The transcript with the wake phrase taken out, wherever the name landed.

    The old pattern was anchored at the start of the sentence, and the
    pre-roll is 2.5 s of whatever preceded the name - so "Thought you did me.
    Hey Jarvis, what time is it?" reached the brain with the television still
    attached. Anything before the name in the first few words is the room.
    A name at the END ("what time is it, Jarvis?") is dropped and the sentence
    kept. Beyond the opening, only the name itself or a one-edit slip counts:
    "remind me to call Travis tomorrow" must survive intact.
    """
    s = text or ""
    for i, m in enumerate(re.finditer(r"[A-Za-z]+", s)):
        if i >= 8:
            break
        if not _is_name(m.group(0), loose=(i < 3)):
            continue
        after = re.sub(r"^[\s,.!?:;-]+", "", s[m.end():]).strip()
        if after:
            return after                                  # the request follows the name
        before = s[:m.start()].rstrip(" ,.!?;:-").strip()
        # The name at the END of an opening ("Hey Jarvis", "um, Jarvis") is
        # a bare wake, whatever the room said first. The name at the end of
        # a sentence ("what time is it, Jarvis?") is the sentence.
        lead = before.split()[-1].lower() if before else ""
        if i < 3 or lead in ("hey", "hi", "ok", "okay", "yo", "so", "um", "uh"):
            return ""
        return before
    return s.strip()


wake = WakeWord()
# THE BARGE-IN LISTENS ON ITS OWN. It runs at the same time as the loop above,
# on the same microphone, while JARVIS is speaking - and one shared model
# meant one of them got every block and the other got none. Two instances,
# two streaming buffers, no contention. A few megabytes, and the difference
# between being able to interrupt him and not.
barge = WakeWord()
