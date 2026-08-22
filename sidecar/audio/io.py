"""Microphone capture and speaker playback via sounddevice, asyncio-friendly."""
from __future__ import annotations

import asyncio
import logging

import numpy as np
import sounddevice as sd

from config import config

log = logging.getLogger("jarvis.audio")

MIC_RATE = 16000
MIC_BLOCK = 1024  # 64 ms


def refresh_devices() -> None:
    """PortAudio caches the device list at init; re-init so hot-plugged
    devices (the webcam mic) show up. Only call with no streams open."""
    try:
        sd._terminate()
        sd._initialize()
    except Exception as e:
        log.debug("portaudio re-init failed: %s", e)


def resolve_input_device() -> tuple[int | None, str, bool]:
    """Pick the microphone.

    Priority: explicit user choice (audio.input_device) > a device whose name
    matches audio.preferred_input_names (the webcam) > system default.
    Returns (device_index_or_None, human_name, is_preferred).
    """
    explicit = config.get("audio", "input_device")
    devs = sd.query_devices()
    if explicit is not None:
        try:
            return int(explicit), devs[int(explicit)]["name"], False
        except Exception:
            pass
    patterns = [str(x).lower() for x in
                config.get("audio", "preferred_input_names",
                           default=["C920", "Webcam", "Logitech"])]
    # ONLY shared-mode host APIs. WDM-KS opens the device EXCLUSIVELY and
    # silences it for every other app (Wispr Flow, browser mic tests...).
    apis = {h["name"]: idx for idx, h in enumerate(sd.query_hostapis())}
    allowed = {apis.get("MME"): 0, apis.get("Windows WASAPI"): 1}
    allowed.pop(None, None)
    candidates = []
    for i, d in enumerate(devs):
        if d["max_input_channels"] <= 0 or d["hostapi"] not in allowed:
            continue
        name = d["name"].lower()
        if any(pat in name for pat in patterns):
            candidates.append((allowed[d["hostapi"]], i, d["name"]))
    if candidates:
        candidates.sort()
        _, idx, name = candidates[0]
        return idx, name, True
    try:
        di = sd.default.device[0]
        return None, devs[di]["name"], False
    except Exception:
        return None, "system default", False


class Microphone:
    """Continuous 16 kHz mono capture broadcast to any number of subscribers.

    Each consumer (utterance capture, wake-word watcher, barge-in watcher) gets
    its own queue so they never steal frames from each other.
    """

    def __init__(self) -> None:
        self._subs: set[asyncio.Queue] = set()
        self._stream: sd.InputStream | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self.device_name: str = "not started"
        self.using_preferred: bool = False
        # legacy single-consumer queue, kept for existing call sites
        self.queue: asyncio.Queue[np.ndarray] = self.subscribe()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    def start(self) -> None:
        if self._stream is not None:
            return
        self._loop = asyncio.get_running_loop()
        device, name, preferred = resolve_input_device()

        def _cb(indata, frames, t, status):
            if status:
                log.debug("mic status: %s", status)
            block = indata[:, 0].copy()
            try:
                self._loop.call_soon_threadsafe(self._put, block)
            except RuntimeError:
                pass

        def _open(dev):
            st = sd.InputStream(samplerate=MIC_RATE, channels=1, dtype="float32",
                                blocksize=MIC_BLOCK, device=dev, callback=_cb)
            st.start()
            return st

        try:
            self._stream = _open(device)
            api = (sd.query_hostapis()[sd.query_devices()[device]["hostapi"]]["name"]
                   if device is not None else "default")
            self.device_name, self.using_preferred = name, preferred
            log.info("microphone: %s via %s%s", name, api,
                     " (preferred webcam mic)" if preferred else "")
        except Exception as e:
            # never leave the assistant deaf: fall back to the system default
            log.warning("could not open %s (%s) — falling back to system default", name, e)
            self._stream = _open(None)
            self.device_name, self.using_preferred = "system default", False
        log.info("microphone started")

    def _put(self, block: np.ndarray) -> None:
        for q in list(self._subs):
            try:
                q.put_nowait(block)
            except asyncio.QueueFull:
                try:
                    q.get_nowait()
                    q.put_nowait(block)
                except asyncio.QueueEmpty:
                    pass

    @staticmethod
    def drain_queue(q: asyncio.Queue) -> None:
        while not q.empty():
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                break

    def drain(self) -> None:
        self.drain_queue(self.queue)

    def restart(self) -> None:
        """Apply a changed input device."""
        self.stop()
        self.start()

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None


class Speaker:
    """Plays float32 chunks; cancellable mid-stream for barge-in.

    PortAudio safety: closing a stream while another thread is blocked inside
    write() crashes natively. abort() first unblocks the writer, then the
    close happens under the write lock so the two can never overlap.
    """

    def __init__(self) -> None:
        import threading
        self._stream: sd.OutputStream | None = None
        self._rate: int | None = None
        self._wlock = threading.Lock()

    def _ensure(self, rate: int) -> sd.OutputStream:
        if self._stream is None or self._rate != rate:
            self.close()
            device = config.get("audio", "output_device")
            self._stream = sd.OutputStream(
                samplerate=rate, channels=1, dtype="float32", device=device)
            self._stream.start()
            self._rate = rate
        return self._stream

    async def play_chunk(self, chunk: np.ndarray, rate: int) -> None:
        stream = self._ensure(rate)

        def _write() -> None:
            with self._wlock:
                if stream.closed:
                    return
                try:
                    stream.write(chunk.reshape(-1, 1))
                except Exception as e:
                    log.debug("write after abort: %s", e)

        await asyncio.to_thread(_write)

    def abort(self) -> None:
        """Immediately stop output (drops buffered audio)."""
        stream = self._stream
        if stream is None:
            return
        try:
            stream.abort()  # unblocks any in-flight write
        except Exception:
            pass
        with self._wlock:  # writer has returned; safe to close
            try:
                stream.close()
            except Exception:
                pass
            if self._stream is stream:
                self._stream = None
                self._rate = None

    def close(self) -> None:
        stream = self._stream
        if stream is None:
            return
        try:
            stream.stop()
        except Exception:
            pass
        with self._wlock:
            try:
                stream.close()
            except Exception:
                pass
            if self._stream is stream:
                self._stream = None
                self._rate = None


mic = Microphone()
speaker = Speaker()
