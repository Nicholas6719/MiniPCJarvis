"""Streaming Silero VAD (ONNX, via faster-whisper 1.2's bundled model).

faster_whisper.vad.SileroVADModel is stateless per call and takes a 1-D float32
buffer whose length is a multiple of 512 (16 kHz). We buffer incoming audio and
run whole frames per feed; each mic block covers several frames per call, which
is accurate enough for barge-in and end-of-speech detection.
"""
from __future__ import annotations

import logging

import numpy as np

log = logging.getLogger("jarvis.vad")


class StreamingVAD:
    FRAME = 512  # 32 ms @ 16 kHz

    def __init__(self, threshold: float = 0.5):
        from faster_whisper.vad import get_vad_model
        self._model = get_vad_model()
        self.threshold = threshold
        self._buf = np.zeros(0, dtype=np.float32)

    def reset(self) -> None:
        self._buf = np.zeros(0, dtype=np.float32)

    def feed(self, audio: np.ndarray) -> list[float]:
        """Feed arbitrary-length 16kHz mono float32 audio → speech prob per 32ms frame."""
        self._buf = np.concatenate([self._buf, audio.astype(np.float32).ravel()])
        n_frames = len(self._buf) // self.FRAME
        if n_frames == 0:
            return []
        chunk = self._buf[: n_frames * self.FRAME]
        self._buf = self._buf[n_frames * self.FRAME:]
        out = self._model(chunk)
        return [float(p) for p in np.asarray(out).ravel()]

    def is_speech(self, audio: np.ndarray) -> bool:
        return any(p >= self.threshold for p in self.feed(audio))
