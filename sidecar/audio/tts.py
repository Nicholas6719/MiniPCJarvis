"""TTS providers: Kokoro (quality, default) and Piper (lowest latency).

Both are local/offline and expose the same interface:
    sample_rate, warmup(), synthesize_stream(text, cancel) -> float32 chunks.
"""
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

    def reload(self) -> None:
        """Pick up a changed voice config on next synthesis."""
        self._voice = None

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
                # DRAIN WHILE WAITING: the producer may be blocked in queue.put() on the
                # bounded queue. Awaiting the task before draining would deadlock (producer
                # waits for a slot, we wait for the producer). Keep pulling until it's done.
                while not task.done():
                    try:
                        await asyncio.wait_for(queue.get(), timeout=0.1)
                    except asyncio.TimeoutError:
                        pass
                    except Exception:
                        break
                await task
                if not cancel_was_set:
                    cancel.clear()
            while not queue.empty():
                queue.get_nowait()


KOKORO_DIR = VOICES_DIR / "kokoro"


class KokoroTTS:
    """Kokoro-82M ONNX — measured 3.4-3.8x realtime on this CPU."""

    def __init__(self) -> None:
        self._k = None
        self._sample_rate = 24000

    def _ensure(self):
        if self._k is None:
            from kokoro_onnx import Kokoro
            model = KOKORO_DIR / "kokoro-v1.0.onnx"
            voices = KOKORO_DIR / "voices-v1.0.bin"
            if not (model.exists() and voices.exists()):
                raise FileNotFoundError(f"kokoro model files missing in {KOKORO_DIR}")
            log.info("loading kokoro tts")
            self._k = Kokoro(str(model), str(voices))
        return self._k

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    def reload(self) -> None:
        pass  # voice is per-call; nothing cached to invalidate

    async def warmup(self) -> None:
        await asyncio.to_thread(self._ensure)

    async def synthesize_stream(self, text: str, cancel: asyncio.Event):
        k = await asyncio.to_thread(self._ensure)
        voice = config.get("tts", "voice", default="bm_george")
        if not voice.startswith(("af_", "am_", "bf_", "bm_")):
            voice = "bm_george"
        speed = float(config.get("tts", "rate", default=1.0))
        try:
            samples, sr = await asyncio.to_thread(
                k.create, text, voice=voice, speed=speed)
        except Exception as e:
            log.error("kokoro synth failed: %s", e)
            return
        self._sample_rate = sr
        audio = np.asarray(samples, dtype=np.float32)
        # emit in ~0.4s chunks so barge-in cancellation stays responsive
        step = int(sr * 0.4)
        for i in range(0, len(audio), step):
            if cancel.is_set():
                return
            yield audio[i:i + step]


class TTSRouter:
    """Selects the engine from config; falls back Kokoro -> Piper."""

    def __init__(self) -> None:
        self.kokoro = KokoroTTS()
        self.piper = PiperTTS()

    def _active(self):
        engine = config.get("tts", "engine", default="kokoro")
        voice = str(config.get("tts", "voice", default="bm_george"))
        # voice prefix wins over engine setting so a single dropdown works
        if voice.startswith("en_"):
            return self.piper
        if engine == "piper":
            return self.piper
        return self.kokoro

    @property
    def sample_rate(self) -> int:
        return self._active().sample_rate

    def reload(self) -> None:
        self.kokoro.reload()
        self.piper.reload()

    async def warmup(self) -> None:
        try:
            await self._active().warmup()
        except Exception as e:
            log.warning("preferred tts unavailable (%s) — falling back to piper", e)
            await self.piper.warmup()

    async def synthesize_stream(self, text: str, cancel: asyncio.Event):
        active = self._active()
        try:
            produced = False
            async for chunk in active.synthesize_stream(text, cancel):
                produced = True
                yield chunk
            if produced or cancel.is_set():
                return
        except Exception as e:
            log.warning("tts engine failed (%s) — falling back to piper", e)
        if active is not self.piper:  # graceful quality->latency fallback
            async for chunk in self.piper.synthesize_stream(text, cancel):
                yield chunk


tts = TTSRouter()
