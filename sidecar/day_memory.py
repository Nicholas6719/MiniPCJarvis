"""What happened earlier today, in three sentences, for the turn note.

He remembers facts (memory) and the last twenty messages (history) and
nothing in between: by the afternoon "the outline we did this morning" was a
mystery. Every twenty idle minutes, if there is enough new conversation, a
side call condenses the day since 4 AM into at most three sentences, stored
in the volatile store under day:summary and read into every turn note while
it is under twelve hours old. The call waits for a quiet moment like every
other background call (2026-09-18).
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time

log = logging.getLogger("jarvis.day")

KEY = "day:summary"
EVERY = 20 * 60          # seconds between attempts
MIN_NEW_TURNS = 6        # new transcript rows since the last summary before it is worth a call
MAX_CHARS = 3500         # of transcript handed to the model
MAX_AGE_H = 12.0
PROMPT = (
    "Below is today's conversation between Nicholas and his assistant JARVIS, oldest first. "
    "Write at most THREE short sentences, in the third person, that JARVIS would want to remember "
    "later today: what he worked on, decided, asked for, or is planning. Concrete nouns, no filler, "
    "no times. If nothing worth remembering happened, reply with the single word NOTHING.\n\n{lines}"
)


def _day_start() -> float:
    now = dt.datetime.now()
    start = now.replace(hour=4, minute=0, second=0, microsecond=0)
    if now < start:
        start -= dt.timedelta(days=1)
    return start.timestamp()


def _rows_since(ts: float) -> list[tuple[float, str, str]]:
    from memory.store import memory
    return memory.db.execute(
        "SELECT ts, role, content FROM transcript WHERE ts >= ? AND role IN ('user','assistant') "
        "ORDER BY ts", (ts,)).fetchall()


def _lines(rows) -> str:
    out = []
    for _, role, content in rows:
        c = " ".join(str(content or "").split())
        if not c:
            continue
        out.append(("He: " if role == "user" else "JARVIS: ") + c[:240])
    text = "\n".join(out)
    return text[-MAX_CHARS:]


def line() -> str:
    """The sentence(s) for the turn note, or '' - never raises."""
    try:
        import volatile
        got = volatile.get(KEY)
        if not got or got["age_s"] > MAX_AGE_H * 3600:
            return ""
        if got["ts"] < _day_start():
            return ""
        s = str((got["value"] or {}).get("text") or "").strip()
        return f"\nEarlier today: {s}" if s else ""
    except Exception:
        log.debug("day memory read failed", exc_info=True)
        return ""


async def summarise(rows) -> str | None:
    """One side call; None when the model had nothing to say or failed."""
    from llm.provider import local_llm, wait_for_quiet
    lines = _lines(rows)
    if len(lines) < 200:
        return None
    await wait_for_quiet()
    out = ""
    try:
        async for ch in local_llm.stream([{"role": "user", "content": PROMPT.format(lines=lines)}],
                                         max_tokens=400, sampling={"temperature": 0.0}):
            out += ch.text or ""
    except Exception as e:
        log.info("day summary skipped: %s", e)
        return None
    text = " ".join(out.split()).strip()
    if not text or text.upper().startswith("NOTHING") or len(text) > 600:
        return None
    return text


async def refresh(force: bool = False) -> bool:
    """Summarise if enough has happened since the last summary. Returns True when stored."""
    import volatile
    got = volatile.get(KEY)
    since = float((got or {}).get("ts") or 0.0)
    rows = _rows_since(_day_start())
    new = [r for r in rows if r[0] > since]
    if not force and len(new) < MIN_NEW_TURNS:
        return False
    text = await summarise(rows)
    if not text:
        return False
    volatile.put(KEY, {"text": text, "turns": len(rows)}, source="day_memory")
    log.info("day memory: %s", text[:120])
    return True


async def loop() -> None:
    await asyncio.sleep(300)
    while True:
        try:
            await refresh()
        except Exception:
            log.debug("day memory loop", exc_info=True)
        await asyncio.sleep(EVERY)
