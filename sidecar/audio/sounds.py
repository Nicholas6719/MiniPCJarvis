"""Synthesized UI sounds — nothing sampled or downloaded, fully deterministic.

One instrument for the whole machine: sine partials, perfect intervals,
gentle roll-off. Levels are capped well below speech so cues never startle.
"""
from __future__ import annotations

import numpy as np

RATE = 24000


def _tone(freq: float, dur: float, rate: int = RATE, partials=(1.0, 0.35, 0.12)) -> np.ndarray:
    t = np.linspace(0, dur, int(rate * dur), endpoint=False)
    sig = np.zeros_like(t)
    for i, amp in enumerate(partials, start=1):
        sig += amp * np.sin(2 * np.pi * freq * i * t)
    # soft attack / exponential release
    env = np.minimum(1.0, t / 0.012) * np.exp(-t * (4.5 / max(dur, 0.05)))
    return (sig * env).astype(np.float32)


def _norm(sig: np.ndarray, peak: float) -> np.ndarray:
    m = float(np.max(np.abs(sig))) or 1.0
    return (sig / m * peak).astype(np.float32)


def make_chime(rate: int = RATE) -> np.ndarray:
    """Wake chime: understated two-tone rise (660 -> 990 Hz, a perfect fifth).
    The one sound that means 'listening now' — used for wake AND barge-in."""
    a = _tone(660, 0.16, rate)
    b = _tone(990, 0.22, rate)
    gap = np.zeros(int(rate * 0.02), dtype=np.float32)
    return _norm(np.concatenate([a, gap, b]), 0.22)


def make_attention(rate: int = RATE) -> np.ndarray:
    """Short rising fifth before an unprompted remark: 'this wasn't asked for'."""
    a = _tone(523.25, 0.12, rate)
    b = _tone(783.99, 0.18, rate)
    return _norm(np.concatenate([a, b]), 0.18)


def make_done(rate: int = RATE) -> np.ndarray:
    """Settled falling fourth."""
    a = _tone(783.99, 0.12, rate)
    b = _tone(587.33, 0.2, rate)
    return _norm(np.concatenate([a, b]), 0.18)


def make_error(rate: int = RATE) -> np.ndarray:
    """Low, deliberately unresolved pair — a problem without sounding like an alarm."""
    a = _tone(311.13, 0.16, rate, partials=(1.0, 0.2))
    b = _tone(293.66, 0.24, rate, partials=(1.0, 0.2))
    return _norm(np.concatenate([a, b]), 0.16)


def make_boot(rate: int = RATE) -> np.ndarray:
    """Arc-reactor power-up, ~2.2 s: sub-bass swell, rising harmonic sweep,
    charging whine, and a perfect-fifth resolve that reads as 'online'."""
    dur = 2.2
    t = np.linspace(0, dur, int(rate * dur), endpoint=False)
    swell = 0.5 * np.sin(2 * np.pi * 55 * t) * np.minimum(1.0, t / 1.2) * np.exp(-np.maximum(0, t - 1.6) * 3)
    f_sweep = 110 * (2 ** (t / dur * 3))  # three octaves up
    sweep = 0.35 * np.sin(2 * np.pi * np.cumsum(f_sweep) / rate) * np.minimum(1.0, t / 0.3) * np.exp(-np.maximum(0, t - 1.5) * 4)
    whine = 0.12 * np.sin(2 * np.pi * (1800 + 900 * t / dur) * t) * np.minimum(1.0, t / 0.8) * np.exp(-np.maximum(0, t - 1.4) * 6)
    rng = np.random.default_rng(7)
    shimmer = 0.05 * rng.standard_normal(len(t)) * np.minimum(1.0, t / 1.0) * np.exp(-np.maximum(0, t - 1.3) * 5)
    resolve = np.zeros_like(t)
    r0 = int(rate * 1.55)
    tr = t[r0:] - t[r0]
    resolve[r0:] = (0.45 * np.sin(2 * np.pi * 440 * tr) + 0.3 * np.sin(2 * np.pi * 660 * tr)) * np.exp(-tr * 2.2)
    sig = swell + sweep + whine + shimmer + resolve
    return _norm(sig, 0.34)


PALETTE = {
    "chime": make_chime,
    "attention": make_attention,
    "done": make_done,
    "error": make_error,
    "boot": make_boot,
}
