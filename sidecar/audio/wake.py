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

    # ONE FEEDER AT A TIME. The wake loop and the barge-in watcher both feed
    # this model from worker threads; on 2026-09-07 five threads were inside
    # `predict` at once, each call slower than the last as the model's own
    # streaming buffer grew under them, and the sidecar burned 2.3 cores while
    # "sleeping". A feeder that finds the model busy DROPS its block - 80 ms
    # of audio nobody will miss - rather than queueing behind it.
    _lock = threading.Lock()
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
