"""Whatever arrived, as the 16 kHz mono float32 the recogniser expects.

A voice note from Telegram is OGG/Opus at 48 kHz — nothing else in JARVIS
produces or reads that. PyAV is used rather than shelling out to ffmpeg: it is
already bundled, and depending on a binary being on PATH is exactly the kind of
thing that works on this machine and fails on an installed copy.
"""
from __future__ import annotations

import io
import logging

import numpy as np

log = logging.getLogger("jarvis.decode")

RATE = 16000
MAX_SECONDS = 300          # a five minute voice note is already unreasonable


class Undecodable(Exception):
    """The bytes were not audio we can read."""


def to_pcm16k(data: bytes) -> np.ndarray:
    """Decode any container PyAV knows into mono 16 kHz float32 in [-1, 1]."""
    if not data:
        raise Undecodable("empty")
    import av                                   # bundled; imported late to keep boot fast

    try:
        with av.open(io.BytesIO(data)) as container:
            stream = next((s for s in container.streams if s.type == "audio"), None)
            if stream is None:
                raise Undecodable("no audio stream")
            resampler = av.AudioResampler(format="flt", layout="mono", rate=RATE)
            chunks: list[np.ndarray] = []
            total = 0
            capped = False
            for frame in container.decode(stream):
                for out in resampler.resample(frame):
                    arr = out.to_ndarray().reshape(-1).astype(np.float32)
                    chunks.append(arr)
                    total += len(arr)
                    if total > RATE * MAX_SECONDS:
                        capped = True
                        break
                if capped:
                    # the break above only left the INNER loop, so the cap was
                    # no cap at all: 20 MB of Opus is about an hour of audio,
                    # and an hour at 16 kHz float32 is a quarter of a gigabyte
                    log.warning("voice note longer than %ds - taking the first part",
                                MAX_SECONDS)
                    break
            # the resampler holds a tail back until it is flushed
            for out in resampler.resample(None):
                chunks.append(out.to_ndarray().reshape(-1).astype(np.float32))
    except Undecodable:
        raise
    except Exception as e:                       # noqa: BLE001 - any codec failure
        raise Undecodable(f"{type(e).__name__}: {e}") from e

    if not chunks:
        raise Undecodable("decoded to nothing")
    audio = np.concatenate(chunks)
    peak = float(np.abs(audio).max()) if len(audio) else 0.0
    if peak < 1e-4:
        raise Undecodable("silence")
    # Phone microphones vary wildly; the recogniser is happier with a consistent
    # level, and quiet notes were coming back empty.
    if peak < 0.5:
        audio = audio * (0.7 / peak)
    return np.clip(audio, -1.0, 1.0).astype(np.float32)
