"""Wake-word detection — openWakeWord's pretrained "hey jarvis" (ONNX, CPU).

Measured on this machine: ~1.8 ms per 80 ms chunk (~2% of one core).
"""
from __future__ import annotations

import logging

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
                pass

    def feed(self, audio_f32: np.ndarray) -> float:
        """Feed float32 [-1,1] 16 kHz audio; returns max hey_jarvis score seen."""
        model = self._ensure()
        self._buf = np.concatenate([self._buf, audio_f32.ravel()])
        best = 0.0
        while len(self._buf) >= CHUNK:
            frame = self._buf[:CHUNK]
            self._buf = self._buf[CHUNK:]
            int16 = (np.clip(frame, -1, 1) * 32767).astype(np.int16)
            scores = model.predict(int16)
            best = max(best, float(scores.get("hey_jarvis", 0.0)))
        return best


wake = WakeWord()
