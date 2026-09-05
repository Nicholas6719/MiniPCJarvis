"""TTS providers: Kokoro (quality, default) and Piper (lowest latency).

Both are local/offline and expose the same interface:
    sample_rate, warmup(), synthesize_stream(text, cancel) -> float32 chunks.
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict
import logging

import json
import re

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


KOKORO_PREFIXES = ("af_", "am_", "bf_", "bm_")
DEFAULT_VOICE = "bm_george"


def parse_voice(spec: str) -> list[tuple[str, float]]:
    """A voice spec into [(voice, weight)], weights summing to 1.

    One name ("bm_george"), or a BLEND: "bm_george+bm_daniel" (equal parts)
    or "bm_george:0.7+bm_daniel:0.3". Kokoro voices are style vectors, and a
    weighted sum of two is a voice that exists between them — which is how a
    voice that sounds like nobody in the pack gets made. Anything unparseable
    is the default, never an exception: a typo in Settings must not cost him
    his voice.
    """
    parts: list[tuple[str, float]] = []
    for piece in (spec or "").replace(" ", "").split("+"):
        if not piece:
            continue
        name, _, w = piece.partition(":")
        if not name.startswith(KOKORO_PREFIXES):
            continue
        try:
            weight = float(w) if w else 1.0
        except ValueError:
            weight = 1.0
        if weight > 0:
            parts.append((name, weight))
    if not parts:
        return [(DEFAULT_VOICE, 1.0)]
    total = sum(w for _, w in parts)
    return [(n, w / total) for n, w in parts]


def kokoro_lang(parts: list[tuple[str, float]]) -> str:
    """British voices get British phonemes. They were being phonemised as
    American — "schedule", "privacy", every "r" — and sounded like an
    Englishman doing an impression."""
    british = sum(w for n, w in parts if n.startswith(("bf_", "bm_")))
    return "en-gb" if british >= 0.5 else "en-us"


class KokoroTTS:
    """Kokoro-82M ONNX — measured 3.4-3.8x realtime on this CPU."""

    def __init__(self) -> None:
        self._k = None
        self._sample_rate = 24000
        self._styles: dict[str, object] = {}     # blended style vectors, by spec

    def _style(self, k, spec: str):
        """The style vector for a spec — a pack voice by name, or a blend."""
        parts = parse_voice(spec)
        if len(parts) == 1:
            return parts[0][0]
        key = "+".join(f"{n}:{w:.3f}" for n, w in parts)
        st = self._styles.get(key)
        if st is None:
            st = sum(w * k.get_voice_style(n) for n, w in parts)
            st = np.asarray(st, dtype=np.float32)
            self._styles[key] = st
        return st

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
        spec = str(config.get("tts", "voice", default=DEFAULT_VOICE))
        parts = parse_voice(spec)
        speed = float(config.get("tts", "rate", default=1.0))
        # Measured delivery: the pause after a sentence, in seconds. JARVIS
        # does not rush from one sentence into the next.
        pause = float(config.get("tts", "sentence_pause", default=0.3))
        try:
            voice = await asyncio.to_thread(self._style, k, spec)
            samples, sr = await asyncio.to_thread(
                k.create, text, voice=voice, speed=speed,
                lang=kokoro_lang(parts), sentence_pause=pause)
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


POCKET_VOICES = ["george", "paul", "michael", "charles", "jean", "marius",
                 "cosette", "anna", "alba"]


class PocketTTS:
    """Kyutai Pocket TTS, run as a worker process under its own interpreter
    (audio/pocket_worker.py explains why and how). Streams: the first 80 ms of
    audio arrives ~100 ms after the request, so JARVIS starts speaking before
    the sentence is synthesised."""

    def __init__(self) -> None:
        self._proc = None
        self._port = 0
        self._sample_rate = 24000
        self._lock = asyncio.Lock()
        self._down_until = 0.0          # after a failed start: no retry before this

    @staticmethod
    def worker_path():
        """The worker script on disk: beside this module in the repo, under
        the bundle's `audio/` when frozen (shipped as a data file by the
        spec). None when it is nowhere — the caller must not spawn."""
        import sys
        from pathlib import Path
        candidates = [Path(__file__).with_name("pocket_worker.py")]
        base = getattr(sys, "_MEIPASS", None)
        if base:
            candidates.append(Path(base) / "audio" / "pocket_worker.py")
        for c in candidates:
            if c.exists():
                return c
        return None

    @staticmethod
    def python() -> str:
        return str(config.get("tts", "pocket_python",
                              default=r"C:\AI\tts\pocket\Scripts\python.exe"))

    @classmethod
    def installed(cls) -> bool:
        import os
        return os.path.exists(cls.python())

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    def reload(self) -> None:
        # The temperature is baked into the loaded model; a change restarts
        # the worker on the next synthesis. Voice/tempo/polish are per call.
        pass

    async def _ensure(self) -> int:
        async with self._lock:
            if self._proc is not None and self._proc.returncode is None and self._port:
                return self._port
            import os
            import subprocess
            from pathlib import Path
            if not self.installed():
                raise FileNotFoundError(f"pocket tts is not installed at {self.python()}")
            import time as _t
            if _t.time() < self._down_until:
                raise RuntimeError("pocket tts worker is down; not retrying yet")
            worker = self.worker_path()
            if worker is None:
                self._down_until = _t.time() + 60
                raise FileNotFoundError("pocket_worker.py is not in the bundle")
            temp = float(config.get("tts", "pocket_temp", default=0.5))
            log.info("starting pocket tts worker")
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            self._proc = await asyncio.create_subprocess_exec(
                self.python(), str(worker), "--temp", str(temp),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, creationflags=flags,
                env={**os.environ, "PYTHONIOENCODING": "utf-8"})
            try:
                line = await asyncio.wait_for(self._proc.stdout.readline(), timeout=120)
            except asyncio.TimeoutError:
                self._proc.kill()
                self._proc = None
                self._down_until = _t.time() + 60
                raise RuntimeError("pocket tts worker did not come up in 120 s")
            text = line.decode("utf-8", "replace").strip()
            if not text.startswith("PORT "):
                # SAY WHY. The first version threw stderr away and retried on
                # every sentence: 1,077 spawns of a python that could not open
                # the worker file, each logged as "worker said ''". Now the
                # reason is in the log and the next try is a minute away.
                err = b""
                try:
                    err = await asyncio.wait_for(self._proc.stderr.read(4000), timeout=2)
                except Exception:
                    pass
                self._proc.kill()
                self._proc = None
                self._down_until = _t.time() + 60
                why = err.decode("utf-8", "replace").strip().splitlines()
                raise RuntimeError(f"pocket tts worker said {text!r}"
                                   + (f": {why[-1]}" if why else ""))
            self._port = int(text.split()[1])
            log.info("pocket tts ready on :%d", self._port)
            return self._port

    async def warmup(self) -> None:
        await self._ensure()

    def close(self) -> None:
        p, self._proc, self._port = self._proc, None, 0
        if p is not None and p.returncode is None:
            try:
                p.kill()
            except Exception:
                pass

    async def synthesize_stream(self, text: str, cancel: asyncio.Event):
        port = await self._ensure()
        voice = str(config.get("tts", "voice", default="george"))
        if voice not in POCKET_VOICES:
            voice = "george"
        # Words he has ruled on. The engine samples pronunciation with the rest
        # of the delivery; a respelling here is how a ruling sticks.
        for word, said in (config.get("tts", "pronounce", default={}) or {}).items():
            text = re.sub(rf"\b{re.escape(str(word))}\b", str(said), text, flags=re.I)
        req = {"text": text, "voice": voice,
               "tempo": float(config.get("tts", "tempo", default=0.97)),
               "polish": bool(config.get("tts", "polish", default=False)),
               "seed": int(config.get("tts", "seed", default=2))}
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            writer.write((json.dumps(req) + "\n").encode("utf-8"))
            await writer.drain()
            while True:
                head = await reader.readexactly(4)
                n = int.from_bytes(head, "little")
                if n == 0:
                    return
                pcm = await reader.readexactly(n)
                if cancel.is_set():
                    return                    # closing the socket cancels the worker
                yield np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32767.0
        finally:
            try:
                writer.close()
            except Exception:
                pass


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
        self.pocket = PocketTTS()
        self._cache: "OrderedDict[tuple, list]" = OrderedDict()
        # Kokoro is one ONNX session; two concurrent create() calls are not something to
        # rely on, and the background phrase warm made that a real possibility.
        self._synth_lock = asyncio.Lock()
        # Cleared while JARVIS is actually answering, so warming never competes with him.
        self.idle = asyncio.Event()
        self.idle.set()

    def _key(self, text: str) -> tuple:
        return (config.get("tts", "engine", default="kokoro"),
                str(config.get("tts", "voice", default="bm_george")),
                float(config.get("tts", "rate", default=1.0)),
                float(config.get("tts", "tempo", default=0.97)),
                bool(config.get("tts", "polish", default=False)),
                text.strip())

    async def warm_phrases(self) -> None:
        """Pre-synthesize the fixed lines in the background, only while he is idle.

        This is background polish and must never cost a real turn anything. It waits for
        the idle gate before every phrase, so a question arriving mid-warm queues behind
        at most one in-flight phrase instead of the whole list.
        """
        import asyncio as _a
        await _a.sleep(20)         # let the model server and the ears finish booting first
        # UNDER POCKET, ONLY THE SHORT ACKNOWLEDGEMENTS. Pocket streams its
        # first frame in ~100 ms, so a cache hit buys a tenth of a second —
        # while a turn arriving mid-phrase waits for that phrase behind
        # _synth_lock (~0.4 s). Sixty phrases of that after every boot is the
        # 0.6-0.9 s reflex floor the early benches measured. Kokoro keeps the
        # full list: there a hit saves half a second.
        phrases = WARM_PHRASES
        gap = 0.3
        if self._active() is self.pocket:
            phrases = [p for p in WARM_PHRASES if len(p) <= 14][:16]
            gap = 1.0
        for phrase in phrases:
            await self.idle.wait()
            if self._key(phrase) in self._cache:
                continue
            try:
                cancel = _a.Event()
                async for _ in self.synthesize_stream(phrase, cancel):
                    pass
            except Exception as e:
                log.debug("phrase warm failed for %r: %s", phrase, e)
                return
            await _a.sleep(gap)
        log.info("tts cache warmed (%d phrases)", len(self._cache))

    def _active(self):
        engine = config.get("tts", "engine", default="kokoro")
        voice = str(config.get("tts", "voice", default="bm_george"))
        # voice prefix wins over engine setting so a single dropdown works
        if voice.startswith("en_"):
            return self.piper
        if voice.startswith(KOKORO_PREFIXES):
            return self.kokoro
        if engine == "piper":
            return self.piper
        if engine == "pocket" or voice in POCKET_VOICES:
            if self.pocket.installed():
                return self.pocket
            log.warning("pocket tts is not installed; using kokoro")
        return self.kokoro

    @property
    def sample_rate(self) -> int:
        return self._active().sample_rate

    def reload(self) -> None:
        self.kokoro.reload()
        self.piper.reload()
        self.pocket.reload()
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
            async with self._synth_lock:      # one synthesis at a time (cache hits skip this)
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
            log.warning("tts engine failed (%s) — falling back", e)
        # Graceful fallback, best first: pocket -> kokoro -> piper.
        if active is self.pocket:
            try:
                async for chunk in self.kokoro.synthesize_stream(text, cancel):
                    yield chunk
                return
            except Exception as e:
                log.warning("kokoro failed too (%s) — falling back to piper", e)
        if active is not self.piper:
            async for chunk in self.piper.synthesize_stream(text, cancel):
                yield chunk


tts = TTSRouter()
