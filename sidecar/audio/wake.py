"""Wake-word detection — openWakeWord's pretrained "hey jarvis" (ONNX, CPU).

Measured on this machine: ~1.8 ms per 80 ms chunk (~2% of one core).
"""
from __future__ import annotations

import logging
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


wake = WakeWord()
# THE BARGE-IN LISTENS ON ITS OWN. It runs at the same time as the loop above,
# on the same microphone, while JARVIS is speaking - and one shared model
# meant one of them got every block and the other got none. Two instances,
# two streaming buffers, no contention. A few megabytes, and the difference
# between being able to interrupt him and not.
barge = WakeWord()
