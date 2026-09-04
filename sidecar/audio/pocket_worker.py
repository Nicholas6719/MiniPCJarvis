"""Pocket TTS worker: Kyutai's ~100M streaming TTS, in its own process.

WHY A SEPARATE PROCESS. Pocket TTS is PyTorch, and PyTorch is not bundled in
the sidecar (980 MB already; torch is another 250+). It lives under
C:\\AI\\tts\\pocket with its own interpreter, exactly as the 3D models live under
C:\\AI\\model3d — a missing install degrades to Kokoro with a clear line in the
log, never to a broken build.

WHY POCKET. He listened to eight Kokoro voices and blends, then to five of
Pocket's, and picked Pocket's George. It also streams: measured on this CPU,
the first 80 ms of audio is ready 80-125 ms after the request — Kokoro
synthesises the whole sentence before the first sample, 0.5-1 s — so JARVIS
starts speaking sooner as well as sounding better.

PROTOCOL. One TCP connection per utterance on loopback. The client sends a
single JSON line {text, voice, temp, tempo, polish}; the worker answers with
length-prefixed frames of int16 PCM at 24 kHz (uint32 little-endian length,
then bytes) and a zero-length frame at the end. Closing the connection cancels:
the generator is abandoned at the next chunk. The listening port is printed on
stdout as one line, `PORT <n>`, once the model is loaded and warm.

`--fake` runs the same protocol with a tone generator and no model, so the
framing, the cancel path and the post-processing are gated by the build on a
machine with no Pocket install.

This file is run by the C:\\AI interpreter: it must import nothing from the
sidecar.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import struct
import sys
import time

import numpy as np

try:                      # scipy is only for the optional polish
    from scipy.signal import butter, sosfilt, sosfilt_zi
except Exception:         # pragma: no cover
    butter = sosfilt = sosfilt_zi = None

SAMPLE_RATE = 24000
_FRAME = struct.Struct("<I")


# ---------------------------------------------------------------- post-processing
def _resample(x: np.ndarray, factor: float) -> np.ndarray:
    """factor > 1: slower AND deeper, together — the tape-speed effect. It is
    the one post-process that costs no quality, because it is only
    interpolation; a formant-preserving pitch shift smears the consonants."""
    if abs(factor - 1.0) < 1e-3 or len(x) < 2:
        return x
    n = max(1, int(round(len(x) * factor)))
    idx = np.linspace(0, len(x) - 1, n)
    return np.interp(idx, np.arange(len(x)), x).astype(np.float32)


class _Polish:
    """A touch of the comms room: high-pass below the voice, a small presence
    lift, gentle levelling. Stateful across chunks so a stream has no seams."""

    def __init__(self, sr: int) -> None:
        if butter is None:
            raise RuntimeError("scipy is not available for polish")
        self.hp = butter(2, 90, "hp", fs=sr, output="sos")
        self.pres = butter(2, [2000, 4500], "bandpass", fs=sr, output="sos")
        self.zi_hp = sosfilt_zi(self.hp) * 0.0
        self.zi_pres = sosfilt_zi(self.pres) * 0.0
        self.env = 0.0
        self.alpha = 1.0 / (sr * 0.01)

    def __call__(self, x: np.ndarray) -> np.ndarray:
        y, self.zi_hp = sosfilt(self.hp, x, zi=self.zi_hp)
        p, self.zi_pres = sosfilt(self.pres, y, zi=self.zi_pres)
        y = y + 0.18 * p
        out = np.empty_like(y)
        env = self.env
        a = self.alpha
        for i, v in enumerate(y):
            env += a * (abs(v) - env)
            out[i] = v * ((0.25 / env) ** 0.5 if env > 0.25 else 1.0)
        self.env = env
        return np.clip(out * 1.1, -1.0, 1.0).astype(np.float32)


# ---------------------------------------------------------------- engines
class _Fake:
    sample_rate = SAMPLE_RATE

    def state(self, voice: str):
        return voice

    def stream(self, state, text: str, seed: int | None = None):
        # 80 ms chunks of a tone whose pitch depends on the voice name, for
        # about 60 ms per character — enough to test cancel mid-stream.
        f = 100 + (sum(map(ord, state)) % 80)
        n = max(3, int(len(text) * 0.06 / 0.08))
        t = np.arange(int(SAMPLE_RATE * 0.08)) / SAMPLE_RATE
        for i in range(n):
            yield (0.3 * np.sin(2 * np.pi * f * (t + i * 0.08))).astype(np.float32)
            time.sleep(0.01)


class _Pocket:
    def __init__(self, temp: float) -> None:
        from pocket_tts import TTSModel
        self.temp = temp
        self.model = TTSModel.load_model(temp=temp)
        self.sample_rate = int(self.model.sample_rate)
        self._states: dict[str, object] = {}

    def state(self, voice: str):
        st = self._states.get(voice)
        if st is None:
            st = self.model.get_state_for_audio_prompt(voice)
            self._states[voice] = st
        return st

    def stream(self, state, text: str, seed: int | None = None):
        # SEEDED. Sampling decides pronunciation as well as prosody: the same
        # sentence said "scheduling" two different ways on two runs, and he
        # noticed. With a seed a sentence is repeatable, so a pronunciation
        # he approved stays approved.
        if seed is not None:
            import torch
            torch.manual_seed(int(seed))
        for chunk in self.model.generate_audio_stream(state, text):
            yield np.asarray(chunk.detach().cpu().numpy(), dtype=np.float32).reshape(-1)


# ---------------------------------------------------------------- server
async def _serve(engine, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        line = await asyncio.wait_for(reader.readline(), timeout=10)
        req = json.loads(line.decode("utf-8"))
        text = str(req.get("text") or "").strip()
        voice = str(req.get("voice") or "george")
        tempo = float(req.get("tempo") or 1.0)
        polish = _Polish(engine.sample_rate) if req.get("polish") else None
        if not text:
            writer.write(_FRAME.pack(0))
            await writer.drain()
            return
        state = engine.state(voice)
        loop = asyncio.get_running_loop()
        seed = req.get("seed")
        gen = engine.stream(state, text, int(seed) if seed is not None else None)

        def _next():
            try:
                return next(gen)
            except StopIteration:
                return None

        while True:
            chunk = await loop.run_in_executor(None, _next)
            if chunk is None:
                break
            chunk = _resample(chunk, tempo)
            if polish is not None:
                chunk = polish(chunk)
            pcm = (np.clip(chunk, -1.0, 1.0) * 32767).astype("<i2").tobytes()
            writer.write(_FRAME.pack(len(pcm)) + pcm)
            try:
                await writer.drain()
            except (ConnectionError, OSError):
                return                    # the client hung up: cancelled
            if reader.at_eof():
                return
        writer.write(_FRAME.pack(0))
        await writer.drain()
    except (ConnectionError, OSError, asyncio.TimeoutError):
        pass
    except Exception as e:                # a bad request must not kill the worker
        sys.stderr.write(f"pocket worker: {e!r}\n")
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fake", action="store_true")
    ap.add_argument("--temp", type=float, default=0.5)
    ap.add_argument("--warm", default="george")
    a = ap.parse_args()
    engine = _Fake() if a.fake else _Pocket(a.temp)
    # Warm: the first prompt encode and the first generation are the slow ones.
    for _ in engine.stream(engine.state(a.warm), "Yes?"):
        pass
    server = await asyncio.start_server(lambda r, w: _serve(engine, r, w), "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    sys.stdout.write(f"PORT {port}\n")
    sys.stdout.flush()
    async with server:
        # Die with the parent: stdin closes when the sidecar goes.
        await asyncio.get_running_loop().run_in_executor(None, sys.stdin.read)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
