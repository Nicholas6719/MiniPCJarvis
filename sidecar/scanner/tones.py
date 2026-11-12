"""Paging tones in a recorded transmission.

A fire department "tones out" a station before it speaks the dispatch: one
steady audio tone for about a second and a second for about three (Motorola
Quick Call II, "two-tone sequential"), or a single long tone for a group. A
tone-out is the dispatcher's own act - it does not depend on a machine
understanding weak speech - so it is evidence that what follows is a
dispatch and not chatter (the research of 2026-11-07: confirmation has to
come from the radio itself).

This finds steady tones in a clip and reads them as a page. It measures; it
decides nothing: what a page means for a call is the desk's business.

Method: 64 ms Hann frames every 32 ms; in each, the strongest bin between 280
and 3200 Hz (below is CTCSS, above is nothing a pager uses), refined by
parabolic interpolation. A frame is "tonal" when that one peak holds most of
the band's energy. Consecutive tonal frames within 1.5 % of each other are one
tone. Speech never holds a single pure frequency that long; a siren sweeps;
DTMF is two peaks, which fails the purity test.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

RATE = 16000
FRAME = 1024            # 64 ms
HOP = 512               # 32 ms
LOW_HZ, HIGH_HZ = 280.0, 3200.0
PURITY = 0.55           # share of band energy within +/-2 bins of the peak
SAME = 0.015            # frames within 1.5 % are the same tone
MIN_TONE_S = 0.45       # shorter is a beep, a chirp or a vowel
A_MIN_S, B_MIN_S = 0.6, 1.5      # two-tone: A about 1 s, B about 3 s
LONG_S = 4.0            # one tone this long is a group page
GAP_MAX_S = 0.4         # silence allowed between A and B


@dataclass(frozen=True)
class Tone:
    start: float        # seconds into the clip
    seconds: float
    hz: float


@dataclass(frozen=True)
class Page:
    kind: str           # "two-tone" | "long-tone"
    start: float
    a_hz: float
    b_hz: float         # 0 for a long tone
    seconds: float

    @property
    def key(self) -> str:
        """Stable name for this page: its tones to the nearest 5 Hz."""
        r = lambda f: int(round(f / 5.0) * 5)
        return f"{r(self.a_hz)}/{r(self.b_hz)}" if self.b_hz else f"{r(self.a_hz)}"


def tones(x: np.ndarray, rate: int = RATE) -> list[Tone]:
    """Steady tones of at least MIN_TONE_S in a mono float clip."""
    if x is None or len(x) < FRAME * 4:
        return []
    x = np.asarray(x, dtype=np.float32)
    x = x - float(np.mean(x))
    n = 1 + (len(x) - FRAME) // HOP
    idx = np.arange(FRAME)[None, :] + HOP * np.arange(n)[:, None]
    spec = np.abs(np.fft.rfft(x[idx] * np.hanning(FRAME)[None, :], axis=1)) ** 2
    lo, hi = int(LOW_HZ * FRAME / rate), int(HIGH_HZ * FRAME / rate)
    band = spec[:, lo:hi]
    total = band.sum(axis=1) + 1e-20
    k = band.argmax(axis=1)
    # the clip's own loudness sets the floor: a tone is among its louder frames
    loud = total >= 0.05 * float(np.percentile(total, 95))
    freqs = np.zeros(n)
    tonal = np.zeros(n, dtype=bool)
    for i in range(n):
        if not loud[i]:
            continue
        p = int(k[i])
        a, b = max(0, p - 2), min(band.shape[1], p + 3)
        if band[i, a:b].sum() / total[i] < PURITY:
            continue
        y0 = band[i, p - 1] if p > 0 else band[i, p]
        y1 = band[i, p]
        y2 = band[i, p + 1] if p + 1 < band.shape[1] else band[i, p]
        y0, y1, y2 = (np.log(v + 1e-20) for v in (y0, y1, y2))
        d = (y0 - y2) / (2 * (y0 - 2 * y1 + y2)) if (y0 - 2 * y1 + y2) != 0 else 0.0
        freqs[i] = (lo + p + float(np.clip(d, -0.5, 0.5))) * rate / FRAME
        tonal[i] = True
    out: list[Tone] = []
    i = 0
    while i < n:
        if not tonal[i]:
            i += 1
            continue
        j, ref, misses = i + 1, [freqs[i]], 0
        while j < n and misses <= 1:
            if tonal[j] and abs(freqs[j] - float(np.median(ref))) <= SAME * float(np.median(ref)):
                ref.append(freqs[j])
                misses = 0
            else:
                misses += 1
            j += 1
        end = j - misses
        secs = ((end - i) * HOP + (FRAME - HOP)) / rate
        if secs >= MIN_TONE_S:
            out.append(Tone(round(i * HOP / rate, 2), round(secs, 2), round(float(np.median(ref)), 1)))
        i = max(end, i + 1)
    return out


def pages(x: np.ndarray, rate: int = RATE) -> list[Page]:
    """The pages in a clip: two different tones back to back (A then a longer
    B), or one tone held for LONG_S."""
    ts = tones(x, rate)
    out: list[Page] = []
    used = set()
    for n, a in enumerate(ts[:-1]):
        b = ts[n + 1]
        if n in used:
            continue
        gap = b.start - (a.start + a.seconds)
        if (a.seconds >= A_MIN_S and b.seconds >= B_MIN_S and gap <= GAP_MAX_S
                and abs(a.hz - b.hz) > 2 * SAME * a.hz):
            out.append(Page("two-tone", a.start, a.hz, b.hz, round(b.start + b.seconds - a.start, 2)))
            used.update((n, n + 1))
    for n, t in enumerate(ts):
        if n not in used and t.seconds >= LONG_S:
            out.append(Page("long-tone", t.start, t.hz, 0.0, t.seconds))
    out.sort(key=lambda p: p.start)
    return out
