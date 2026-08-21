"""TTSProvider interface + local Piper implementation (streaming, interruptible)."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import numpy as np

from config import APP_DIR, config

log = logging.getLogger("jarvis.tts")

VOICES_DIR = APP_DIR / "voices"


class PiperTTS:
    def __init__(self) -> None:
        self._voice = None
        self._sample_rate = 22050

    def _ensure(self):
        if self._voice is None:
            from piper import PiperVoice
            name = config.get("tts", "voice", default="en_GB-alan-medium")
            path = VOICES_DIR / f"{name}.onnx"
            if not path.exists():
                raise FileNotFoundError(f"voice model missing: {path}")
            log.info("loading piper voice %s", name)
            self._voice = PiperVoice.load(path)
            self._sample_rate = self._voice.config.sample_rate
        return self._voice

    @property
    def sample_rate(self) -> int:
        self._ensure()
        return self._sample_rate

    async def warmup(self) -> None:
        await asyncio.to_thread(self._ensure)

    async def synthesize_stream(self, text: str, cancel: asyncio.Event):
        """Yield float32 numpy chunks at self.sample_rate. Stops fast on cancel."""
        voice = await asyncio.to_thread(self._ensure)
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue(maxsize=8)

        done = object()

        def _produce():
            try:
                for chunk in voice.synthesize(text):
                    if cancel.is_set():
                        break
                    audio = np.frombuffer(chunk.audio_int16_bytes, dtype=np.int16)
                    f32 = audio.astype(np.float32) / 32768.0
                    asyncio.run_coroutine_threadsafe(queue.put(f32), loop).result()
            except Exception as e:
                log.error("piper synth failed: %s", e)
            finally:
                asyncio.run_coroutine_threadsafe(queue.put(done), loop).result()

        task = loop.run_in_executor(None, _produce)
        try:
            while True:
                item = await queue.get()
                if item is done or cancel.is_set():
                    break
                yield item
        finally:
            # let the producer thread exit promptly without treating normal
            # completion as a cancellation
            if not task.done():
                cancel_was_set = cancel.is_set()
                cancel.set()
                await task
                if not cancel_was_set:
                    cancel.clear()
            while not queue.empty():
                queue.get_nowait()


tts = PiperTTS()
