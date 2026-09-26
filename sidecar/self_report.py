"""How he did yesterday, in one sentence, for the morning brief.

His ask (2026-09-18): "a morning self-report: his own latency, misroutes and
false wakes, so regressions surface without me reading the log." The numbers
come from turn_stats (time to the first word, stored since the same day), the
brain's learned examples (each a correction he made) and the wake counter.

One spoken sentence; the phone gets the same as short lines.
"""
from __future__ import annotations

import logging
import time

log = logging.getLogger("jarvis.self_report")


def _tenths(ms: int | None) -> str:
    """160 -> 'a fifth of a second', 4300 -> 'four seconds'."""
    if ms is None:
        return ""
    s = ms / 1000.0
    if s < 0.35:
        return "under half a second"
    if s < 0.75:
        return "half a second"
    if s < 1.5:
        return "about a second"
    return f"about {round(s)} seconds"


def numbers(hours: float = 24) -> dict:
    from memory.store import memory
    rep = memory.turn_stats_report(hours)
    try:
        since = time.time() - hours * 3600
        rep["corrections"] = int(memory.db.execute(
            "SELECT COUNT(*) FROM brain_examples WHERE source = 'user' AND ts >= ?",
            (since,)).fetchone()[0])
    except Exception:
        rep["corrections"] = None
    try:
        # the module-level singleton; never "import main", which would execute
        # the entry module a second time inside the running app
        from orchestrator import orchestrator as _orch
        rep["false_wakes"] = int(getattr(_orch, "wakes_rejected", 0) or 0)
    except Exception:
        rep["false_wakes"] = None
    return rep


def status_text() -> str:
    """/status on Telegram: how JARVIS is doing right now, in one message he
    can read on his phone (2026-09-26)."""
    parts: list[str] = []
    try:
        import os
        import psutil
        p = psutil.Process(os.getpid())
        up = time.time() - p.create_time()
        d, h = int(up // 86400), int((up % 86400) // 3600)
        parts.append(f"Up {d}d {h}h" if d else f"Up {h}h {int((up % 3600) // 60)}m")
        parts.append(f"sidecar {p.memory_info().rss / 1e6:.0f} MB")
    except Exception:
        pass
    try:
        from llm.llama_server import llama
        from orchestrator import orchestrator as _o
        name = llama.model_name or "no model"
        probe = getattr(_o, "_probe_at", 0.0)
        fails = int(getattr(_o, "_probe_failures", 0) or 0)
        ago = f"{int((time.time() - probe) / 60)} min ago" if probe else "not yet"
        parts.append(f"{name}: probe {ago}" + (f", {fails} failing" if fails else ", answering"))
        parts.append(f"state {_o.sm.state.value}")
        parts.append(f"false wakes rejected {int(getattr(_o, 'wakes_rejected', 0) or 0)}")
    except Exception:
        pass
    try:
        r = numbers(24)
        if r.get("turns"):
            parts.append(f"{r['turns']} turns in 24h, first word {r.get('reflex_first_ms') or '-'} ms reflex / {r.get('model_first_ms') or '-'} ms model")
    except Exception:
        pass
    try:
        from tools import phone as _phone
        st = _phone.status()
        if st and isinstance(st.get("battery"), int):
            parts.append(f"phone {st['battery']}%{' charging' if st.get('charging') else ''}, {int(st.get('age_minutes') or 0)} min ago")
        else:
            parts.append("phone: no status yet")
    except Exception:
        pass
    try:
        from delivery import delivery
        briefs = [e for e in delivery.entries(time.time() - 86400) if str(e.get("subject", "")).startswith("brief:")]
        parts.append(f"{len(briefs)} briefs sent today" if briefs else "no brief yet today")
    except Exception:
        pass
    try:
        import shutil
        parts.append(f"disk {shutil.disk_usage('C:' + chr(92)).free / 1e9:.0f} GB free")
    except Exception:
        pass
    return "JARVIS status - " + "; ".join(parts) + "."


def lines(hours: float = 24) -> list[tuple[str, str]]:
    """[(spoken, written)] - empty when there is nothing to say."""
    try:
        r = numbers(hours)
    except Exception:
        log.debug("self report failed", exc_info=True)
        return []
    if not r.get("turns"):
        return []
    said = [f"Yesterday we spoke {r['turns']} times"]
    if r.get("reflex_first_ms") is not None:
        said.append(f"my first word came in {_tenths(r['reflex_first_ms'])} on reflexes")
    if r.get("model_first_ms") is not None:
        said.append(f"and {_tenths(r['model_first_ms'])} when I had to think")
    spoken = ", ".join(said) + "."
    if r.get("corrections"):
        n = r["corrections"]
        spoken += f" You corrected me {'once' if n == 1 else str(n) + ' times'} and I learned from it."
    if r.get("false_wakes"):
        spoken += f" I ignored {r['false_wakes']} false wake{'s' if r['false_wakes'] != 1 else ''}."
    written = [f"{r['turns']} turns ({r['reflex_turns']} reflex, {r['model_turns']} model)"]
    if r.get("reflex_first_ms") is not None:
        written.append(f"first word: reflex {r['reflex_first_ms']} ms")
    if r.get("model_first_ms") is not None:
        written.append(f"model {r['model_first_ms']} ms"
                       + (f", {r['model_gen_tokens']} tokens median" if r.get("model_gen_tokens") else ""))
    if r.get("slowest"):
        s = r["slowest"]
        written.append(f"slowest: {s['skill'] or s['path']} {round((s['ms'] or 0) / 1000, 1)} s")
    if r.get("corrections"):
        written.append(f"corrections learned: {r['corrections']}")
    if r.get("false_wakes"):
        written.append(f"false wakes rejected: {r['false_wakes']}")
    return [(spoken, " · ".join(written))]
