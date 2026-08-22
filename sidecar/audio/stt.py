"""Speech-to-text.

Engines (config stt.engine):
- "parakeet": NVIDIA Parakeet TDT 0.6B v3 via onnx-asr, int8 on CPU. Bake-off
  2026-08-22 (tests/stt_ab2.py, 45 synthesized command clips + noise): 139 ms median,
  0.6% WER vs faster-whisper base.en 450 ms / 5.1%. Default.
- "whisper": faster-whisper (CTranslate2, CPU int8). Fallback if Parakeet can't load.
"""
from __future__ import annotations

import asyncio
import logging
import re

import numpy as np

from config import config

log = logging.getLogger("jarvis.stt")

_GLUE_CAMEL = re.compile(r"(?<=[a-z])(?=[A-Z])")        # "OpenYoutube" -> "Open Youtube"
_GLUE_DIGIT = re.compile(r"(?<=[a-z])(?=[$\d])")         # "under$1,500" -> "under $1,500"


def _tidy(text: str) -> str:
    text = _GLUE_CAMEL.sub(" ", text)
    text = _GLUE_DIGIT.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


class STT:
    def __init__(self) -> None:
        self._model = None
        self._engine: str | None = None
        self._lock = asyncio.Lock()

    @property
    def engine(self) -> str | None:
        return self._engine

    @property
    def label(self) -> str:
        if self._engine == "parakeet":
            return "Parakeet TDT 0.6B v3 (int8)"
        if self._engine == "whisper":
            return f"faster-whisper {config.get('stt', 'model', default='base.en')}"
        return "not loaded"

    def _load_parakeet(self):
        import onnx_asr
        q = config.get("stt", "parakeet_quant", default="int8")
        log.info("loading parakeet tdt 0.6b v3 (%s)", q)
        m = onnx_asr.load_model("nemo-parakeet-tdt-0.6b-v3", quantization=q or None)
        self._engine = "parakeet"
        return m

    def _load_whisper(self):
        from faster_whisper import WhisperModel
        name = config.get("stt", "model", default="base.en")
        log.info("loading whisper %s", name)
        m = WhisperModel(name, device=config.get("stt", "device", default="cpu"),
                         compute_type=config.get("stt", "compute_type", default="int8"), cpu_threads=4)
        self._engine = "whisper"
        return m

    def _ensure(self):
        if self._model is None:
            want = config.get("stt", "engine", default="parakeet")
            if want == "parakeet":
                try:
                    self._model = self._load_parakeet()
                except Exception:
                    log.exception("parakeet unavailable - falling back to whisper")
                    self._model = self._load_whisper()
            else:
                self._model = self._load_whisper()
        return self._model

    async def warmup(self) -> None:
        async with self._lock:
            model = await asyncio.to_thread(self._ensure)
            # first inference allocates buffers: do it now, not on the user's first word
            await asyncio.to_thread(self._run, model, np.zeros(16000, dtype=np.float32))

    def reload(self) -> None:
        """Pick up a changed model config on next transcription."""
        self._model = None
        self._engine = None

    def _run(self, model, audio: np.ndarray) -> str:
        if self._engine == "parakeet":
            return _tidy(model.recognize(audio.astype(np.float32), sample_rate=16000) or "")
        segments, _info = model.transcribe(audio, language="en", beam_size=1,
                                           condition_on_previous_text=False, vad_filter=False)
        return " ".join(s.text.strip() for s in segments).strip()

    async def transcribe(self, audio: np.ndarray) -> str:
        """audio: 16 kHz mono float32 in [-1, 1]."""
        async with self._lock:
            model = await asyncio.to_thread(self._ensure)
            return await asyncio.to_thread(self._run, model, audio)


stt = STT()
