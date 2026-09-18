"""Is he finished, by the SOUND of it: Smart Turn v3 (pipecat-ai, BSD-2).

The semantic endpoint reads the partial transcript; this reads the waveform.
An 8M-parameter Whisper-Tiny encoder with a linear head, int8 ONNX, ~10-60 ms
on a CPU, trained on 23 languages to tell a finished turn ("what's the
weather") from a mid-thought pause ("what's the weather in... ") by prosody.
Pipecat runs it after its VAD hears a pause and treats probability > 0.5 as
"complete"; the same here (2026-09-18).

Features are Whisper's log-mel spectrogram (80 mels, n_fft 400, hop 160) over
the last eight seconds, zero-padded on the LEFT, exactly as the HF
WhisperFeatureExtractor(chunk_length=8) produces them - computed with librosa
and numpy so the 2.5 GB `transformers` never enters the bundle.

Absent model file = absent signal: predict() returns None and the endpoint
behaves as it did before.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

import numpy as np

from config import APP_DIR, config

log = logging.getLogger("jarvis.turn")

SR = 16000
N_FFT = 400
HOP = 160
N_MELS = 80
WINDOW_S = 8
N_SAMPLES = SR * WINDOW_S            # 128,000
N_FRAMES = N_SAMPLES // HOP          # 800
DEFAULT_NAME = "smart-turn-v3.2-cpu.onnx"

_lock = threading.Lock()
_session = None
_input_name = None
_mel_filters = None
_missing_logged = False


def model_path() -> Path:
    p = str(config.get("wake", "turn_model_path", default="") or "")
    if p:
        return Path(p)
    return APP_DIR / "models" / DEFAULT_NAME


def available() -> bool:
    return bool(config.get("wake", "turn_model", default=True)) and model_path().exists()


def _filters():
    global _mel_filters
    if _mel_filters is None:
        import librosa
        # whisper's mel_filters.npz was generated with exactly this call
        _mel_filters = librosa.filters.mel(sr=SR, n_fft=N_FFT, n_mels=N_MELS).astype(np.float32)
    return _mel_filters


def features(audio: np.ndarray) -> np.ndarray:
    """Whisper log-mel input_features for the last 8 s: shape (1, 80, 800)."""
    import librosa
    a = np.asarray(audio, dtype=np.float32).reshape(-1)
    if a.size > N_SAMPLES:
        a = a[-N_SAMPLES:]
    if a.size < N_SAMPLES:
        a = np.concatenate([np.zeros(N_SAMPLES - a.size, dtype=np.float32), a])
    stft = librosa.stft(a, n_fft=N_FFT, hop_length=HOP, window="hann", center=True)
    mag = (np.abs(stft[:, :-1]) ** 2).astype(np.float32)        # drop the last frame, as whisper does
    mel = _filters() @ mag
    log_spec = np.log10(np.maximum(mel, 1e-10))
    log_spec = np.maximum(log_spec, log_spec.max() - 8.0)
    log_spec = (log_spec + 4.0) / 4.0
    return log_spec[np.newaxis, :, :].astype(np.float32)


def _ensure_session():
    global _session, _input_name, _missing_logged
    if _session is not None:
        return _session
    with _lock:
        if _session is not None:
            return _session
        p = model_path()
        if not p.exists():
            if not _missing_logged:
                log.info("turn model not installed (%s): endpointing by transcript only", p)
                _missing_logged = True
            return None
        import onnxruntime as ort
        so = ort.SessionOptions()
        so.intra_op_num_threads = 1
        so.inter_op_num_threads = 1
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        sess = ort.InferenceSession(str(p), sess_options=so, providers=["CPUExecutionProvider"])
        _input_name = sess.get_inputs()[0].name
        _session = sess
        log.info("turn model loaded: %s (input %s)", p.name, _input_name)
        return sess


def predict(audio: np.ndarray) -> float | None:
    """Probability that the turn is COMPLETE, or None when the model is absent
    or fails. Synchronous, ~10-60 ms: call it off the loop."""
    if not config.get("wake", "turn_model", default=True):
        return None
    try:
        sess = _ensure_session()
        if sess is None:
            return None
        t0 = time.time()
        out = sess.run(None, {_input_name: features(audio)})
        raw = np.asarray(out[0], dtype=np.float32).reshape(-1)[0]
        # the exported graph ends in a sigmoid; a logit is tolerated anyway
        p = float(raw) if 0.0 <= raw <= 1.0 else float(1.0 / (1.0 + np.exp(-raw)))
        log.debug("turn model: %.2f in %.0f ms", p, (time.time() - t0) * 1000)
        return p
    except Exception:
        log.debug("turn model failed", exc_info=True)
        return None


def warmup() -> None:
    if available():
        predict(np.zeros(SR, dtype=np.float32))
