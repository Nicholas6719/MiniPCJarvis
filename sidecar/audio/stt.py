"""Speech-to-text via faster-whisper (CTranslate2, CPU int8)."""
from __future__ import annotations

import asyncio
import logging

import numpy as np

from config import config

log = logging.getLogger("jarvis.stt")


class STT:
    def __init__(self) -> None:
        self._model = None
        self._lock = asyncio.Lock()

    def _ensure(self):
        if self._model is None:
            from faster_whisper import WhisperModel
            name = config.get("stt", "model", default="small.en")
            log.info("loading whisper %s", name)
            self._model = WhisperModel(
                name,
                device=config.get("stt", "device", default="cpu"),
                compute_type=config.get("stt", "compute_type", default="int8"),
                cpu_threads=4,
            )
        return self._model

    async def warmup(self) -> None:
        async with self._lock:
            await asyncio.to_thread(self._ensure)

    def reload(self) -> None:
        """Pick up a changed model config on next transcription."""
        self._model = None

    async def transcribe(self, audio: np.ndarray) -> str:
        """audio: 16 kHz mono float32 in [-1, 1]."""
        async with self._lock:
            model = await asyncio.to_thread(self._ensure)

            def _run() -> str:
                segments, _info = model.transcribe(
                    audio, language="en", beam_size=1,
                    condition_on_previous_text=False,
                    vad_filter=False,
                )
                return " ".join(s.text.strip() for s in segments).strip()

            return await asyncio.to_thread(_run)


stt = STT()
