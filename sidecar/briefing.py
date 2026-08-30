"""The shift JARVIS works while nobody is asking him anything.

Four briefs a day at fixed times, and between them a quiet watch on the things
that would not wait: breaking news near home, a large move in something he owns,
a market that has fallen out of bed.

Two rules govern the whole thing.

**Quiet hours suppress BRIEFS, never alerts.** He was explicit: "if there is
breaking news in the quiet hours, he messages me on Telegram... if there is
breaking national news at 3 a.m., I want to know about it." A digest can wait
until morning. An emergency cannot.

**Nothing here decides where to send anything.** delivery.py owns that, and its
answer is always the same question — is he there? This module decides only what
is worth saying at all, using significance.py, and hands it over.

Anything judged NOTABLE is not sent on its own; it is kept and folded into the
next brief, which is what makes the briefs worth reading and the alerts worth
noticing.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time

from config import config
from delivery import ALERT, BRIEF, URGENT, delivery
from significance import NONE, classify_market, classify_news

log = logging.getLogger("jarvis.briefing")

TICK_S = 60                    # how often the clock is checked
DEFAULT_TIMES = ["07:30", "12:30", "16:15", "20:00"]


def _now() -> dt.datetime:
    return dt.datetime.now()


def _in_quiet_hours(now: dt.datetime | None = None) -> bool:
    """The window in which a DIGEST would be an intrusion.

    Briefing keeps its own window rather than borrowing the one the system
    alerts use. Those end at 08:00, and his first brief is at 07:30 — sharing
    the setting meant the morning brief was suppressed every single day, which
    the gate caught and I had not.
    """
    now = now or _now()
    try:
        start = dt.datetime.strptime(
            str(config.get("briefing", "quiet_start", default="22:00")), "%H:%M").time()
        end = dt.datetime.strptime(
            str(config.get("briefing", "quiet_end", default="07:00")), "%H:%M").time()
    except ValueError:
        return False
    t = now.time()
    return (start <= t or t < end) if start > end else (start <= t < end)


class Briefing:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._held: list[dict] = []          # NOTABLE items awaiting a brief
        self._seen: dict[str, float] = {}    # headline -> when it was judged
        self._last_brief: str = ""           # "2026-08-30 07:30", so it fires once
        self._last_watch: float = 0.0
        # "Breaking" means new since he last looked, not everything in the feed
        # right now. On the first pass after a restart the watch only LEARNS what
        # is already out there — otherwise every restart dumps the day's news at
        # him as though it had all just happened.
        self._primed = False

    # ---------- lifecycle ----------

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="briefing")

    def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None

    @property
    def enabled(self) -> bool:
        return bool(config.get("briefing", "enabled", default=True))

    # ---------- the loop ----------

    async def _loop(self) -> None:
        await asyncio.sleep(30)              # let the app finish waking up
        while True:
            try:
                if self.enabled:
                    await self._maybe_brief()
                    await self._maybe_watch()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("briefing tick failed")
            await asyncio.sleep(TICK_S)

    async def _maybe_brief(self) -> None:
        now = _now()
        stamp = now.strftime("%Y-%m-%d %H:%M")
        times = config.get("briefing", "times", default=DEFAULT_TIMES) or DEFAULT_TIMES
        if now.strftime("%H:%M") not in times or self._last_brief == stamp:
            return
        self._last_brief = stamp
        if _in_quiet_hours(now):
            log.info("brief due at %s but it is quiet hours - holding", stamp)
            return
        text = await self.compose_brief()
        if text:
            await delivery.deliver(text, tier=BRIEF, key=f"brief:{stamp}")

    async def _maybe_watch(self) -> None:
        gap = float(config.get("briefing", "watch_minutes", default=10)) * 60
        if time.time() - self._last_watch < gap:
            return
        self._last_watch = time.time()
        found = await self.scan()
        if not self._primed:
            self._primed = True
            log.info("watch primed with %d item(s) already out there - none sent",
                     len(found))
            return
        for text, tier, key in found:
            await delivery.deliver(text, tier=tier, key=key)

    # ---------- the watch ----------

    async def scan(self) -> list[tuple[str, str, str]]:
        """Everything that will not wait, as (text, tier, key)."""
        out: list[tuple[str, str, str]] = []
        max_age = float(config.get("briefing", "alert_max_age_minutes", default=180))
        for story in await self._fresh_stories():
            tier, why = classify_news(story)
            if tier == NONE:
                continue
            # A three-day-old story appearing in a feed is not breaking. Without
            # this, anything the feed happens to carry can wake him.
            age = story.get("age_minutes")
            if age is not None and age > max_age:
                continue
            head = str(story.get("headline") or "").strip()
            key = f"news:{head[:80].lower()}"
            if key in self._seen:
                continue
            self._seen[key] = time.time()
            if tier in (ALERT, URGENT):
                src = story.get("source") or "the news"
                out.append((f"{head} — {src}.", tier, key))
            else:
                self._held.append({"kind": "news", "text": head, "why": why})
        out.extend(await self._market_moves())
        self._forget_old()
        return out

    @staticmethod
    def _same_story(a: str, b: str) -> bool:
        """One event, four newsrooms.

        A killing in Framingham came back from MetroWest, NBC Boston, WHDH and
        WCVB, all worded differently, and filled the brief on its own. Compare
        the significant words rather than the wording.
        """
        import re as _re
        stop = {"after", "with", "from", "that", "this", "says", "said", "over",
                "into", "amid", "police", "report", "reports", "man", "woman"}
        def words(t):
            return {w for w in _re.sub(r"[^a-z0-9 ]", " ", t.lower()).split()
                    if len(w) > 3 and w not in stop}
        wa, wb = words(a), words(b)
        if not wa or not wb:
            return False
        if len(wa & wb) / max(1, min(len(wa), len(wb))) >= 0.5:
            return True

        # Wording alone is not enough. "Man held without bail after deadly
        # assault in Framingham" and "Man admits to killing 33-year-old woman in
        # Framingham" share exactly one significant word, and are the same
        # killing. So: the same town plus a death on the same day is treated as
        # one story. Two unrelated deaths in Natick on one day is rare enough
        # that conflating them occasionally beats printing one event four times.
        from significance import FATALITY, HOME_TOWNS
        towns_a = {t for t in HOME_TOWNS if t in a.lower()}
        towns_b = {t for t in HOME_TOWNS if t in b.lower()}
        if towns_a & towns_b and FATALITY.search(a) and FATALITY.search(b):
            return True
        return False

    def _dedupe(self, stories: list[dict]) -> list[dict]:
        kept: list[dict] = []
        for s in stories:
            head = str(s.get("headline") or "")
            if not head:
                continue
            if any(self._same_story(head, str(k.get("headline") or "")) for k in kept):
                continue
            kept.append(s)
        return kept

    async def _fresh_stories(self) -> list[dict]:
        from tools.news_tools import get_breaking_news, get_news
        stories: list[dict] = []
        try:
            national = await get_breaking_news(count=8)
            stories += (national.get("items") or national.get("latest") or [])
        except Exception:
            log.debug("national scan failed", exc_info=True)
        for place in config.get("briefing", "local_places",
                                default=["Massachusetts"]) or []:
            try:
                local = await get_news(query=str(place), count=4)
                stories += (local.get("items") or [])
            except Exception:
                log.debug("local scan failed for %s", place, exc_info=True)
        return self._dedupe(stories)

    async def _market_moves(self) -> list[tuple[str, str, str]]:
        from tools.market_tools import get_market_movers, get_watchlist
        out: list[tuple[str, str, str]] = []
        held = set(config.get("markets", "watchlist", default=[]) or [])
        try:
            mine = await get_watchlist()
            for row in (mine.get("stocks") or []):
                tier, why = classify_market(symbol=row["symbol"],
                                            percent=row.get("percent") or 0,
                                            held=row["symbol"] in held)
                key = f"move:{row['symbol']}:{_now():%Y-%m-%d-%H}"
                if tier == NONE or key in self._seen:
                    continue
                self._seen[key] = time.time()
                line = (f"{row['name']} is {'up' if (row.get('percent') or 0) > 0 else 'down'} "
                        f"{abs(row.get('percent') or 0)}% at {row.get('price')} dollars.")
                if tier in (ALERT, URGENT):
                    out.append((line, tier, key))
                # a smaller move is NOT held: every brief already lists his
                # holdings, so holding it printed the same line twice
        except Exception:
            log.debug("watchlist scan failed", exc_info=True)
        try:
            movers = await get_market_movers()
            for m in (movers.get("markets") or []):
                tier, _ = classify_market(symbol=m["symbol"],
                                          percent=m.get("percent") or 0, is_index=True)
                key = f"index:{m['symbol']}:{_now():%Y-%m-%d-%H}"
                if tier in (ALERT, URGENT) and key not in self._seen:
                    self._seen[key] = time.time()
                    out.append((f"{m['name']} is {m.get('percent')}% today.", tier, key))
        except Exception:
            log.debug("index scan failed", exc_info=True)
        return out

    def _forget_old(self) -> None:
        cutoff = time.time() - 12 * 3600
        self._seen = {k: v for k, v in self._seen.items() if v > cutoff}

    # ---------- the brief ----------

    async def compose_brief(self) -> str:
        """What he gets at 07:30. Short, and honest about a quiet day."""
        parts: list[str] = []

        from tools.market_tools import get_market_movers, get_watchlist
        try:
            movers = await get_market_movers()
            rows = movers.get("markets") or []
            if rows:
                parts.append("Markets: " + "; ".join(
                    f"{m['name']} {'up' if (m.get('percent') or 0) >= 0 else 'down'} "
                    f"{abs(m.get('percent') or 0)}%" for m in rows) + ".")
        except Exception:
            log.debug("brief: markets failed", exc_info=True)
        try:
            mine = await get_watchlist()
            rows = mine.get("stocks") or []
            if rows:
                parts.append("Yours: " + "; ".join(
                    f"{r['name']} {'up' if (r.get('percent') or 0) >= 0 else 'down'} "
                    f"{abs(r.get('percent') or 0)}%" for r in rows[:5]) + ".")
        except Exception:
            log.debug("brief: watchlist failed", exc_info=True)

        stories = await self._fresh_stories()
        ranked: list[tuple[str, dict]] = []
        for s in stories:
            tier, _ = classify_news(s)
            if tier != NONE:
                ranked.append((tier, s))
        order = {URGENT: 0, ALERT: 1}
        ranked.sort(key=lambda p: order.get(p[0], 2))
        seen_heads: set[str] = set()
        lines = []
        for _tier, s in ranked[:4]:
            head = str(s.get("headline") or "").strip()
            if not head or head.lower() in seen_heads:
                continue
            seen_heads.add(head.lower())
            lines.append(f"{head} ({s.get('source') or 'the news'})")
        if lines:
            parts.append("News: " + "; ".join(lines) + ".")

        if self._held:
            extra = [h["text"] for h in self._held[-3:]]
            self._held.clear()
            parts.append("Also: " + "; ".join(extra) + ".")

        if not parts:
            return "Nothing worth reporting, sir."
        text = " ".join(p.rstrip() for p in parts)
        import re as _re
        return _re.sub(r"\.\.+", ".", text)


briefing = Briefing()
