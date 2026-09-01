"""Microphone capture and speaker playback via sounddevice, asyncio-friendly."""
from __future__ import annotations

import asyncio
import time
import logging

import concurrent.futures as cf

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
    hostapis = sd.query_hostapis()
    mme = next((i for i, h in enumerate(hostapis) if "MME" in h["name"].upper()), None)
    _pats = [str(x).lower() for x in config.get("audio", "preferred_input_names",
                                                default=["C920", "Webcam", "Logitech"])]
    if explicit is not None:
        try:
            chosen = devs[int(explicit)]
            # NEVER open via WDM-KS/DirectSound: remap to the MME entry of the same
            # name (shared mode). A WDM-KS open takes the device exclusively and
            # every other app gets "Device in use".
            if chosen["hostapi"] != mme:
                for i, d in enumerate(devs):
                    if d["name"] == chosen["name"] and d["hostapi"] == mme and d["max_input_channels"] > 0:
                        log.info("remapped explicit mic choice to shared-mode entry %s", i)
                        return i, d["name"], any(pat in d["name"].lower() for pat in _pats)
                # no MME twin: ignore the explicit choice rather than go exclusive
                log.warning("explicit mic %s is not a shared-mode device — ignoring", explicit)
            else:
                return int(explicit), chosen["name"], any(pat in chosen["name"].lower() for pat in _pats)
        except Exception:
            pass
    patterns = [str(x).lower() for x in
                config.get("audio", "preferred_input_names",
                           default=["C920", "Webcam", "Logitech"])]
    # ONLY shared-mode host APIs. WDM-KS opens the device EXCLUSIVELY and
    # silences it for every other app (Wispr Flow, browser mic tests...).
    allowed = {}
    for idx, h in enumerate(sd.query_hostapis()):
        n = h["name"].upper()
        if "MME" in n:
            allowed[idx] = 0
        elif "WASAPI" in n:
            allowed[idx] = 1
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
        dname = devs[di]["name"]
        # the system default IS the webcam mic — that's still "preferred"
        return None, dname, any(pat in dname.lower() for pat in patterns)
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
        self.last_frame_at: float = 0.0
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
        import time as _t
        self.last_frame_at = _t.time()
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


# --- the writer's own threads ---------------------------------------------------
# A PortAudio write that never returns leaks the thread that made it, for good.
# asyncio.to_thread uses the DEFAULT executor, which is also where all 58 of the
# sync tool handlers run - so a handful of dead output devices over a long
# session would quietly eat that pool until nothing could open a file or read the
# clipboard, and nobody would connect the two.
#
# So the audio writer gets its own small pool. When a write hangs, the worker is
# written off; once they are all written off, the pool is replaced (the old one
# is never shut down - its threads are still stuck inside the driver, and joining
# them is exactly the wait we are refusing to make).
_WRITER_THREADS = 2
_writer_pool: "cf.ThreadPoolExecutor | None" = None
_writers_lost = 0


def _writer_executor() -> "cf.ThreadPoolExecutor":
    global _writer_pool, _writers_lost
    if _writer_pool is None or _writers_lost >= _WRITER_THREADS:
        if _writer_pool is not None:
            log.warning("audio: all %d writer threads are stuck - starting a fresh "
                        "pool (the old one is abandoned, not joined)", _WRITER_THREADS)
            _ORPHAN_POOLS.append(_writer_pool)
        _writer_pool = cf.ThreadPoolExecutor(
            max_workers=_WRITER_THREADS, thread_name_prefix="jarvis-audio-write")
        _writers_lost = 0
    return _writer_pool


def _writer_lost() -> None:
    global _writers_lost
    _writers_lost += 1
    log.warning("audio: writer thread %d of %d is stuck in the driver",
                _writers_lost, _WRITER_THREADS)


_ORPHAN_POOLS: list = []


# Output streams whose writer thread never came back. They are kept - not
# closed, not dropped - because a stream freed while a PortAudio thread is
# still inside it takes the process down with no traceback. A handful of
# leaked handles over a session is the cheapest possible price for that.
_ORPHANS: list = []


class SpeakerStalled(RuntimeError):
    """The output device stopped accepting audio (unplugged / slept / switched)."""


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
        # When we last put sound into the room. The room-audio check reads the
        # OUTPUT device, which hears JARVIS too — without this he would take his
        # own voice for a television and start demanding his name back.
        self.last_write_at = 0.0

    def _ensure(self, rate: int) -> sd.OutputStream:
        if self._stream is None or self._rate != rate:
            self.close()
            device = config.get("audio", "output_device")
            self._stream = sd.OutputStream(
                samplerate=rate, channels=1, dtype="float32", device=device)
            self._stream.start()
            self._rate = rate
        return self._stream

    silent_until: float = 0.0   # self-test mode: synthesize but don't play (no 3 AM chatter)

    # How long the output is presumed unusable after a write hangs. Long enough
    # that a whole reply is routed to the phone rather than dribbling out one
    # stalled chunk at a time; short enough that plugging headphones back in is
    # noticed within a minute.
    _DEAF_OUTPUT_S = 60.0
    _deaf_output_until: float = 0.0

    async def play_chunk(self, chunk: np.ndarray, rate: int) -> None:
        """Play one chunk. NEVER blocks the turn forever: PortAudio's write is a
        blocking call, and when the output device disappears mid-sentence (a
        headset disconnects, the monitor speakers sleep, the default device
        changes) it never returns — on 2026-08-27 that wedged a whole turn in
        SPEAKING for 90 minutes and JARVIS answered nothing, on any channel,
        until he was restarted. A write that overruns its own audio duration by
        a wide margin means the device is gone: abort it and self-heal."""
        if time.time() < self.silent_until:
            await asyncio.sleep(len(chunk) / float(rate) * 0.25)   # keep timing roughly real
            return
        # A device that just refused to accept audio will refuse the next chunk
        # too. Without this, every sentence pays the full write budget again to
        # rediscover the same sleeping monitor - twelve seconds a chunk, with
        # the turn stalled behind it. Fail immediately instead, so the caller
        # can fall back to the phone while the speakers are unavailable.
        if time.time() < self._deaf_output_until:
            raise SpeakerStalled("the audio output device is not accepting data")
        stream = self._ensure(rate)

        def _write() -> None:
            with self._wlock:
                if stream.closed:
                    return
                try:
                    self.last_write_at = time.time()
                    stream.write(chunk.reshape(-1, 1))
                    self.last_write_at = time.time()
                except Exception as e:
                    log.debug("write after abort: %s", e)

        secs = len(chunk) / float(rate)
        budget = max(5.0, secs * 4 + 3.0)   # generous: only a dead device exceeds this
        try:
            loop = asyncio.get_running_loop()
            await asyncio.wait_for(
                loop.run_in_executor(_writer_executor(), _write), timeout=budget)
        except asyncio.TimeoutError:
            _writer_lost()      # that thread is not coming back
            self._deaf_output_until = time.time() + self._DEAF_OUTPUT_S
            log.error("audio write hung (%.1fs of audio, %.0fs budget) — output device "
                      "is not accepting data; aborting, and not trying again for %.0fs",
                      secs, budget, self._DEAF_OUTPUT_S)
            self.abort()          # unblocks the stuck writer thread, closes the stream
            self._stream = None   # next chunk reopens against the CURRENT default device
            self._rate = None
            raise SpeakerStalled("the audio output device stopped responding")

    # How long either of these will wait for the writer thread before deciding
    # it is never coming back. Short on purpose: this runs on the event loop.
    _LOCK_WAIT_S = 0.75

    def _release(self, stream, how: str) -> None:
        """Close a stream without ever blocking the caller.

        THIS IS THE ONE THAT FROZE HIM. Both of these used `with self._wlock:`,
        guarded by the comment "writer has returned; safe to close" - and on
        2026-08-30 at 19:40 the writer had NOT returned. A dead output device
        left PortAudio's blocking write stuck while holding the lock; the
        timeout handler then tried to take that same lock from the EVENT LOOP
        THREAD, and the whole assistant stopped: no speech, no HTTP, no wake
        word, for forty minutes, with the process still running so the
        supervisor never noticed.

        `stream.abort()` is supposed to unblock the writer. When the device is
        gone it does not always manage it, so waiting on the lock can never be
        unbounded. A leaked stream object costs a handle; a blocked event loop
        costs the entire assistant.
        """
        got = self._wlock.acquire(timeout=self._LOCK_WAIT_S)
        try:
            if got:
                # The writer is out. Ours to close.
                try:
                    stream.close()
                except Exception:
                    pass
            else:
                # ABANDONED, and that has to mean it. The first version of this
                # said "abandoning" in the log and then called stream.close()
                # anyway, on both paths - closing a PortAudio stream while
                # another thread is still blocked inside write() on it frees a C
                # resource out from under that thread. On 2026-08-31 at 07:01 the
                # device died again, this fix held the event loop open exactly as
                # intended, and then the process died anyway with no traceback,
                # twenty seconds later. Not closing costs a handle and a thread.
                # Closing costs the process.
                _ORPHANS.append(stream)   # and keep it alive: letting the GC
                # finalise it would close it just as surely, only later and less
                # predictably, with the writer still inside.
                log.warning("audio: writer thread still stuck after %s; abandoning "
                            "the stream (%d orphaned so far) rather than blocking "
                            "the loop or closing it underneath the writer",
                            how, len(_ORPHANS))
            if self._stream is stream:
                self._stream = None
                self._rate = None
        finally:
            if got:
                self._wlock.release()

    def abort(self) -> None:
        """Immediately stop output (drops buffered audio). Never blocks."""
        stream = self._stream
        if stream is None:
            return
        try:
            stream.abort()  # asks PortAudio to unblock any in-flight write
        except Exception:
            pass
        self._release(stream, "abort")

    def close(self) -> None:
        stream = self._stream
        if stream is None:
            return
        try:
            stream.stop()
        except Exception:
            pass
        self._release(stream, "close")


mic = Microphone()
speaker = Speaker()
