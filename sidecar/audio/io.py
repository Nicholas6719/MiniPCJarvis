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


class Microphone:
    """Continuous 16 kHz mono capture pushing blocks onto an asyncio queue."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[np.ndarray] = asyncio.Queue(maxsize=200)
        self._stream: sd.InputStream | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def start(self) -> None:
        if self._stream is not None:
            return
        self._loop = asyncio.get_running_loop()
        device = config.get("audio", "input_device")

        def _cb(indata, frames, t, status):
            if status:
                log.debug("mic status: %s", status)
            block = indata[:, 0].copy()
            try:
                self._loop.call_soon_threadsafe(self._put, block)
            except RuntimeError:
                pass

        self._stream = sd.InputStream(
            samplerate=MIC_RATE, channels=1, dtype="float32",
            blocksize=MIC_BLOCK, device=device, callback=_cb)
        self._stream.start()
        log.info("microphone started")

    def _put(self, block: np.ndarray) -> None:
        try:
            self.queue.put_nowait(block)
        except asyncio.QueueFull:
            try:
                self.queue.get_nowait()
                self.queue.put_nowait(block)
            except asyncio.QueueEmpty:
                pass

    def drain(self) -> None:
        while not self.queue.empty():
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None


class Speaker:
    """Plays float32 chunks; cancellable mid-stream for barge-in."""

    def __init__(self) -> None:
        self._stream: sd.OutputStream | None = None
        self._rate: int | None = None

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
        await asyncio.to_thread(stream.write, chunk.reshape(-1, 1))

    def abort(self) -> None:
        """Immediately stop output (drops buffered audio)."""
        if self._stream is not None:
            try:
                self._stream.abort()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
            self._rate = None

    def close(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None


mic = Microphone()
speaker = Speaker()
