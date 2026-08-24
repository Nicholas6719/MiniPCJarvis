"""TTS providers: Kokoro (quality, default) and Piper (lowest latency).

Both are local/offline and expose the same interface:
    sample_rate, warmup(), synthesize_stream(text, cancel) -> float32 chunks.
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict
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


# Lines JARVIS says constantly. Synthesising one costs ~0.4-0.7 s every single time,
# which is most of the gap between "he decided" and "he started talking".
WARM_PHRASES = [
    "Yes?", "Done.", "Muted.", "Unmuted.", "Noted.", "Locking.", "Cancelled.", "Okay.",
    "Skipping.", "Going back.", "Stopped.", "Very good, sir.", "Right away, sir.",
    "Let me see.", "One moment.", "Let me think.", "Hmm, let me check.", "Just a second.",
    "Searching.", "Let me look that up.", "Checking the web.", "Let me dig into that.",
    "Researching.", "Finding pictures.", "Opening it.", "Loading the page.",
    "Let me read that page.", "Let me think back.", "Let me remember.",
    "Done, sir.", "Noted, sir.", "Muted, sir.", "Okay, sir.",
    "Unmuted, sir.", "Locking, sir.", "Stopped, sir.",
] + [f"Volume set to {n} percent{tail}" for n in range(0, 101, 5) for tail in (".", ", sir.")]


class TTSRouter:
    """Selects the engine from config; falls back Kokoro -> Piper.

    Caches synthesized audio by exact text: JARVIS repeats himself constantly
    ("Muted.", "One moment.", "Yes?"), and a cache hit turns a ~0.5 s synth into a
    memcpy — the difference between a reflex that feels instant and one that lags.
    """

    CACHE_MAX = 160

    def __init__(self) -> None:
        self.kokoro = KokoroTTS()
        self.piper = PiperTTS()
        self._cache: "OrderedDict[tuple, list]" = OrderedDict()

    def _key(self, text: str) -> tuple:
        return (config.get("tts", "engine", default="kokoro"),
                str(config.get("tts", "voice", default="bm_george")),
                float(config.get("tts", "rate", default=1.0)),
                text.strip())

    async def warm_phrases(self) -> None:
        """Pre-synthesize the fixed lines in the background after boot."""
        import asyncio as _a
        await _a.sleep(20)         # let the model server and the ears finish booting first
        for phrase in WARM_PHRASES:
            if self._key(phrase) in self._cache:
                continue
            try:
                cancel = _a.Event()
                async for _ in self.synthesize_stream(phrase, cancel):
                    pass
            except Exception as e:
                log.debug("phrase warm failed for %r: %s", phrase, e)
                return
            # deliberately unhurried: this is background polish, and a real turn
            # arriving mid-warm must not have to queue behind it
            await _a.sleep(0.3)
        log.info("tts cache warmed (%d phrases)", len(self._cache))

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
        self._cache.clear()          # voice/engine changed: cached audio is stale

    async def warmup(self) -> None:
        try:
            await self._active().warmup()
        except Exception as e:
            log.warning("preferred tts unavailable (%s) — falling back to piper", e)
            await self.piper.warmup()

    async def synthesize_stream(self, text: str, cancel: asyncio.Event):
        active = self._active()
        key = self._key(text)
        hit = self._cache.get(key)
        if hit is not None:
            self._cache.move_to_end(key)
            for chunk in hit:
                if cancel.is_set():
                    return
                yield chunk
            return
        collected: list = []
        try:
            produced = False
            async for chunk in active.synthesize_stream(text, cancel):
                produced = True
                collected.append(chunk)
                yield chunk
            if produced and not cancel.is_set() and len(text) <= 60:
                self._cache[key] = collected
                self._cache.move_to_end(key)
                while len(self._cache) > self.CACHE_MAX:
                    self._cache.popitem(last=False)
            if produced or cancel.is_set():
                return
        except Exception as e:
            log.warning("tts engine failed (%s) — falling back to piper", e)
        if active is not self.piper:  # graceful quality->latency fallback
            async for chunk in self.piper.synthesize_stream(text, cancel):
                yield chunk


tts = TTSRouter()
