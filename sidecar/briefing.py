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
        self._last_brief: str = ""           # kept for anything still reading it
        # Slots already delivered, as "2026-08-31 12:30". A set rather than one
        # value: two slots can come due close together after a restart, and the
        # single value let the second overwrite the first.
        self._done_slots: set[str] = self._load_state("done_slots")
        # Priming exists so a restart does not dump the whole feed at him as
        # breaking news. Kept on DISK, because a process restarting more often
        # than the 5-minute lane gap primed for ever and silently sent nothing.
        self._seen.update(self._load_state("seen_keys", as_dict=True))
        self._last_watch: float = 0.0
        # Two clocks: emergencies are checked more often than prices are.
        self._last_news: float = 0.0
        self._last_market: float = 0.0
        # Consecutive failed news sweeps, used to back off rather than hammer a
        # feed that is refusing us.
        self._feed_fails: int = 0
        # "Breaking" means new since he last looked, not everything in the feed
        # right now. On the first pass after a restart the watch only LEARNS what
        # is already out there — otherwise every restart dumps the day's news at
        # him as though it had all just happened.
        self._primed = False

    # ---------- what survives a restart ----------
    #
    # `_done_slots` and `_seen` were memory only, so a restart at 07:40 saw the
    # 07:30 slot ten minutes late, inside the grace window, and sent the brief a
    # second time - and every story already sent became new again.

    @staticmethod
    def _state_path():
        # Beside the DATABASE, not in AppData: JARVIS_DB is what the gates
        # redirect, and a state file outside it meant the tests read and wrote
        # his real briefing state - one run's saved slots made the next run's
        # "a missed brief is still delivered" check fail against nothing.
        from config import DB_PATH
        return DB_PATH.parent / "briefing_state.json"

    # The same 400 that is written to disk, applied in memory too. Without it
    # this dict grew for as long as JARVIS stayed up: every headline judged, all
    # day, kept forever, while only the newest 400 were ever persisted. Small,
    # but it is a leak, and the cap already existed one line away.
    _SEEN_MAX = 400

    def _remember_seen(self, key: str) -> None:
        self._seen[key] = time.time()
        if len(self._seen) > self._SEEN_MAX * 2:
            keep = sorted(self._seen.items(), key=lambda kv: kv[1])[-self._SEEN_MAX:]
            self._seen = dict(keep)

    def _load_state(self, key: str, as_dict: bool = False):
        try:
            import json
            raw = json.loads(self._state_path().read_text(encoding="utf-8"))
            today = _now().strftime("%Y-%m-%d")
            if raw.get("day") != today:
                return {} if as_dict else set()     # yesterday is not today
            val = raw.get(key) or ({} if as_dict else [])
            return dict(val) if as_dict else set(val)
        except Exception:
            return {} if as_dict else set()

    def _save_state(self) -> None:
        try:
            import json
            self._state_path().write_text(json.dumps({
                "day": _now().strftime("%Y-%m-%d"),
                "done_slots": sorted(self._done_slots),
                "seen_keys": {k: v for k, v in list(self._seen.items())[-400:]},
            }), encoding="utf-8")
        except Exception:
            log.debug("could not save briefing state", exc_info=True)

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
            # Sleep to the next minute BOUNDARY. A flat sleep(60) after work
            # that takes 30-60s drifts, and a drifting clock is what stopped his
            # 12:30 brief being noticed at all.
            now_t = time.time()
            await asyncio.sleep(max(5.0, TICK_S - (now_t % TICK_S)))

    async def _maybe_brief(self) -> None:
        """Has a brief come due since the last look?

        NOT "is the clock reading exactly 12:30". That was the old test, and on
        2026-08-31 his 12:30 brief never arrived: the loop was alive - alerts
        went out at 12:35 and 12:46 - but it never OBSERVED the minute. The tick
        does its work and then sleeps 60s, and once the watch started reading and
        summarising articles that work took 30-60s, so the real period became 90
        to 120 seconds and every other minute went unseen. A schedule that has to
        be looked at during the right sixty seconds is a schedule that will
        eventually be missed.

        So a slot fires if it is PAST and has not been done today. `grace_minutes`
        bounds how late is still worth sending - a 12:30 brief at 12:47 is fine,
        at four o'clock it is history.
        """
        now = _now()
        times = config.get("briefing", "times", default=DEFAULT_TIMES) or DEFAULT_TIMES
        grace = float(config.get("briefing", "grace_minutes", default=25) or 25)
        today = now.strftime("%Y-%m-%d")
        due = None
        for slot in times:
            stamp = f"{today} {slot}"
            if stamp in self._done_slots:
                continue
            try:
                at = dt.datetime.combine(
                    now.date(), dt.datetime.strptime(str(slot), "%H:%M").time())
            except ValueError:
                continue
            late = (now - at).total_seconds() / 60.0
            if 0 <= late <= grace:
                due = (stamp, slot, late)
                break
            if late > grace:
                # missed it entirely (asleep, restarting, a long outage). Mark it
                # done so it cannot ambush him hours later.
                self._done_slots.add(stamp)
        if due is None:
            return
        stamp, slot, late = due
        self._done_slots.add(stamp)
        self._save_state()
        if len(self._done_slots) > 40:
            self._done_slots = set(list(self._done_slots)[-20:])
        if late > 1.5:
            log.info("brief for %s is %.0f min late - sending anyway", slot, late)
        if _in_quiet_hours(now):
            log.info("brief due at %s but it is quiet hours - holding", stamp)
            return
        # Composed once, shaped twice: sentences for the ear, bullets for the eye.
        sections = await self._sections(final=self.is_final_slot(slot, times),
                                        first=self.is_first_slot(slot, times))
        text = await self.compose_brief(sections)
        written = await self.compose_brief_written(sections)
        if text:
            # ON SCREEN as well as in his ear. A brief read aloud at 07:30 is
            # gone by 07:31; the written form went only to his phone until
            # 2026-09-05. The HUD shows the same sections he would get there.
            try:
                from events import bus
                hour = now.hour
                title = ("Morning brief" if hour < 11 else "Midday brief" if hour < 15
                         else "Afternoon brief" if hour < 18 else "Evening brief")
                await bus.emit("brief", title=title, eyebrow="THE BRIEF",
                               sections=[{"title": t, "lines": [w for _s, w in lines if w]}
                                         for t, lines in sections if lines],
                               gauges=self._gauges_of(sections))
            except Exception:
                log.debug("could not put the brief on screen", exc_info=True)
            await delivery.deliver(text, tier=BRIEF, key=f"brief:{stamp}",
                                   written=written)

    async def _maybe_watch(self) -> None:
        """Two lanes, two clocks. Emergencies are not the same job as prices.

        He asked whether emergencies could be checked every five minutes without
        everything else running that often. They can, because the halves cost
        different things: the news sweep is 7 keyless RSS fetches (~6s), while the
        market half is Finnhub, which is rate-limited to 60 calls a minute and is
        pointless to poll between prints. Splitting them makes emergencies twice
        as fresh AND cuts the market calls in half.
        """
        now = time.time()
        news_gap = float(config.get("briefing", "emergency_minutes", default=5)) * 60
        # If the feed starts refusing us, slow down instead of hammering it. A
        # silent throttle would look exactly like a quiet news day, which is the
        # worst possible failure for something whose job is to notice emergencies.
        news_gap *= 1 + min(self._feed_fails, 5)
        market_gap = float(config.get("briefing", "watch_minutes", default=10)) * 60

        found: list[tuple[str, str, str]] = []
        ran = False
        if now - self._last_news >= news_gap:
            self._last_news = now
            ran = True
            found += await self.scan(news=True, market=False)
        if now - self._last_market >= market_gap:
            self._last_market = now
            ran = True
            found += await self.scan(news=False, market=True)
        if not ran:
            return

        if not self._primed:
            self._primed = True
            log.info("watch primed with %d item(s) already out there - none sent",
                     len(found))
            return
        for text, tier, key in found:
            await delivery.deliver(text, tier=tier, key=key)

    # ---------- the watch ----------

    async def scan(self, *, news: bool = True,
                   market: bool = True) -> list[tuple[str, str, str]]:
        """Everything that will not wait, as (text, tier, key).

        The halves are separable so the two lanes above can run at their own
        pace; called with no arguments it still does both, which is what
        /debug/brief and the gates use.
        """
        out: list[tuple[str, str, str]] = []
        if not news:
            return list(await self._market_moves()) if market else out
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
            # Exact key first, then the same event in different words. He was
            # told about one death twice: "Teen dies trying to rescue companion
            # after fall at Massachusetts Bay Transportation Authority train
            # station" in the afternoon brief, then two hours later "Massachusetts
            # teen who died trying to rescue girlfriend remembered for putting
            # others before himself". One event, two newsrooms, two pings.
            # _same_story already knew how to see that; it was only ever being
            # asked WITHIN a single sweep, never across them.
            if key in self._seen or self._seen_before(head):
                continue
            self._remember_seen(key)
            if tier in (ALERT, URGENT):
                # Read the piece and say what happened. He asked for exactly
                # this: "is Jarvis reading through these like news articles and
                # then summarizing them into 1-2 sentences". If it cannot be
                # read, the headline still goes - an alert is never dropped for
                # want of a summary.
                from newsroom import spoken_line, summarize
                said = await summarize(story)
                self._remember(said)
                out.append((spoken_line(said), tier, key))
            else:
                # NOTABLE means "waits for the brief", not "belongs in the brief".
                # The first real brief rolled up a Manchester United tribute to an
                # officer killed on the A66 and a UMass football staff hire. Both
                # are correctly NOTABLE - neither is worth his morning. A held item
                # has to be near him or carry national weight; a distant one-off
                # death stays classified so a direct question still answers, but it
                # does not get to crowd the roll-up.
                from significance import NATIONAL_WEIGHT, is_local
                near, _town = is_local(story)
                if near or NATIONAL_WEIGHT.search(head):
                    self._held.append({"kind": "news", "text": head, "why": why})
        if market:
            out.extend(await self._market_moves())
        self._forget_old()
        self._save_state()
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
        # The national wire is ALWAYS read, even on local-only. An earlier version
        # skipped it to save the call, which quietly closed the one door he asked
        # to keep open: "the absolute emergencies from other places". A national
        # emergency he never fetches is one he never hears about. The classifier
        # throws away everything that is not one - which, measured on two live
        # wires, was all 110 stories.
        try:
            national = await get_breaking_news(count=8)
            stories += (national.get("items") or national.get("latest") or [])
        except Exception:
            log.debug("national scan failed", exc_info=True)
        # DIRECT publisher feeds, not a Google News search. This is not a
        # preference: a search feed returns news.google.com redirect links, which
        # are JavaScript interstitials - fetching one gives 0 characters. Nothing
        # can be read, summarised, or opened from them, so every local story
        # arrived as a bare headline no matter what the summariser did.
        for topic in config.get("briefing", "local_topics",
                                default=["local", "towns"]) or []:
            try:
                local = await get_news(topic=str(topic), count=8)
                # Provenance beats parsing. A story off WCVB or Natick Patch IS
                # local, whatever words it happens to contain - and a place name
                # alone is poor evidence, since the national wire is BBC and Sky
                # and England has a Boston, a Cambridge and a Worcester too.
                for it in (local.get("items") or []):
                    it["_local_feed"] = True
                stories += (local.get("items") or [])
            except Exception:
                log.debug("local scan failed for %s", topic, exc_info=True)
        # A named-subject search still catches anything the desks missed; its
        # links are unreadable, so these can only ever be headlines.
        for place in config.get("briefing", "local_places", default=[]) or []:
            try:
                found = await get_news(query=str(place), count=3)
                stories += (found.get("items") or [])
            except Exception:
                log.debug("local search failed for %s", place, exc_info=True)

        # A sweep that returns nothing is a sweep that found nothing OR a feed
        # that has started refusing us, and from here those look identical. Both
        # are answered the same way - check less often for a while - because
        # hammering a throttling feed every five minutes is how a throttle
        # becomes a block, and a blocked feed reads as a permanently quiet world.
        if stories:
            if self._feed_fails:
                log.info("news feed answering again after %d empty sweep(s)",
                         self._feed_fails)
            self._feed_fails = 0
        else:
            self._feed_fails += 1
            log.warning("news sweep came back empty (%d in a row) - backing off",
                        self._feed_fails)
        return self._dedupe(stories)

    @staticmethod
    def _market_open() -> bool:
        """Is the US market actually trading?

        Finnhub keeps the last session's percent after the close, so a Friday
        -6% on NVDA looked like a fresh -6% every hour until Monday - about 48
        Telegram alerts across a weekend, and ALERT is not suppressed by quiet
        hours. The key also carried the hour, so each one looked new.
        """
        now = _now()
        if now.weekday() >= 5:                 # Saturday, Sunday
            return False
        minutes = now.hour * 60 + now.minute   # his machine runs on ET
        return 9 * 60 + 30 <= minutes <= 16 * 60

    async def _market_moves(self) -> list[tuple[str, str, str]]:
        if not self._market_open():
            return []          # a closed market has no news, only stale numbers
        from tools.market_tools import get_market_movers, get_watchlist
        out: list[tuple[str, str, str]] = []
        held = set(config.get("markets", "watchlist", default=[]) or [])
        try:
            mine = await get_watchlist()
            for row in (mine.get("stocks") or []):
                tier, why = classify_market(symbol=row["symbol"],
                                            percent=row.get("percent") or 0,
                                            held=row["symbol"] in held)
                key = f"move:{row['symbol']}:{_now():%Y-%m-%d}"
                if tier == NONE or key in self._seen:
                    continue
                self._remember_seen(key)
                from analyst import display_name
                name = display_name(row.get("symbol"), row.get("name"))
                pct = abs(row.get("percent") or 0)
                way = "up" if (row.get("percent") or 0) > 0 else "down"
                line = f"{name} is {way} {pct:.2f}% at {row.get('price')} dollars."
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
                key = f"index:{m['symbol']}:{_now():%Y-%m-%d}"
                if tier in (ALERT, URGENT) and key not in self._seen:
                    self._remember_seen(key)
                    pct = m.get("percent") or 0
                    way = "up" if pct >= 0 else "down"
                    out.append((f"{m['name']} is {way} {abs(pct):.2f}% today.",
                                tier, key))
        except Exception:
            log.debug("index scan failed", exc_info=True)
        return out

    def _forget_old(self) -> None:
        cutoff = time.time() - 12 * 3600
        self._seen = {k: v for k, v in self._seen.items() if v > cutoff}

    # ---------- the brief ----------

    # ---------- the brief ----------
    #
    # Built ONCE as sections, rendered TWICE. He got a 300-word paragraph on his
    # phone and said: "way too cluttered and not easy to read at all. I should get
    # clear and concise bullet points!" He was right, and the cause was structural:
    # one string was written to be SPOKEN and then sent verbatim to Telegram.
    # Speech wants flowing sentences; a phone wants short lines you can scan. The
    # same text cannot be both, so it is no longer asked to be.

    def _seen_before(self, headline: str) -> bool:
        """Has he already been told this, however it was worded last time?"""
        for key in self._seen:
            if not key.startswith("news:"):
                continue
            if self._same_story(headline, key[5:]):
                return True
        return False

    @staticmethod
    def _remember_all(items: list) -> None:
        """Remember every story in the brief, in the order he heard them."""
        rows = [{"headline": i.get("headline"), "url": i.get("url"),
                 "source": i.get("source"), "when": i.get("when"),
                 "age_minutes": i.get("age_minutes")}
                for i in items if i.get("url")]
        if not rows:
            return
        try:
            from lastseen import last_seen
            last_seen.note_result({"items": rows})
        except Exception:
            log.debug("could not remember the brief's links", exc_info=True)

    @staticmethod
    def _remember(said: dict) -> None:
        """Keep the link behind a proactive item, so "give me the article" works.

        Everything he is told unprompted is a thing he can ask about next, and
        without this the subject of "it" is whatever he last ASKED for - never
        what JARVIS last volunteered.
        """
        url = str(said.get("url") or "")
        if not url:
            return
        try:
            from lastseen import last_seen
            last_seen.note_result({"items": [{
                "headline": said.get("headline"), "url": url,
                "source": said.get("source"), "when": said.get("when"),
                "age_minutes": said.get("age_minutes")}]})
        except Exception:
            log.debug("could not remember the link", exc_info=True)

    @staticmethod
    def _tidy(headline: str) -> str:
        """A headline as it should appear, not as the CMS emitted it.

        Real examples from his brief: a poll headline ending "Democratic Primary -"
        rendered as "Primary -;" once punctuation was appended, and several arrive
        with trailing dashes, pipes or the outlet's own name.
        """
        # One implementation of "how a headline should read", shared with the
        # summariser. Aggregated feeds append the outlet - "...found guilty? |
        # Hindustan Times" - and then we appended it again, so the roll-up was
        # telling him the source twice in one line.
        from newsroom import _clean_headline
        return _clean_headline(headline)

    @staticmethod
    def _pct(value) -> tuple[str, str]:
        """(spoken, written) for one percentage.

        They differ on purpose. A screen wants "-4.58%", which is scannable in a
        column; a voice reading that says "minus four point five eight percent"
        and sounds like a machine. The first version of this shared one string
        and the spoken brief inherited the screen's minus signs and separators.
        """
        v = float(value or 0)
        return (f"{'up' if v >= 0 else 'down'} {abs(v):.2f}%",
                f"{'+' if v >= 0 else '-'}{abs(v):.2f}%")

    async def _sections(self, final: bool = False,
                        first: bool = False) -> list[tuple[str, list[tuple[str, str]]]]:
        """The brief as titled groups, each line held as (spoken, written).

        `final` is the last brief of the day, the only one that carries news;
        `first` is the morning one, the only one that carries the week's earnings.

        The single source of truth for both renderings, so the phone and the
        voice can never disagree, and so the market and news calls happen once.
        """
        from analyst import display_name
        from tools.market_tools import get_market_movers, get_watchlist
        out: list[tuple[str, list[tuple[str, str]]]] = []

        try:
            movers = await get_market_movers()
            rows = movers.get("markets") or []
            if rows:
                said, shown = [], []
                for m in rows:
                    name = display_name(m.get("symbol"), m.get("name"))
                    spoken, written = self._pct(m.get("percent"))
                    said.append(f"{name} {spoken}")
                    shown.append(f"{name} {written}")
                gap = movers.get("missing") or []
                if gap:
                    # Say what is absent. Reporting one index as though it were
                    # the market is how a brief becomes misleading rather than
                    # merely short.
                    said.append(f"no data for {', '.join(gap)}")
                    shown.append(f"(no data: {', '.join(gap)})")
                out.append(("Markets", [("; ".join(said), " · ".join(shown))]))
                # the numbers themselves, for the HUD's gauges row
                self._last_gauges = [{"name": display_name(m.get("symbol"), m.get("name")),
                                      "percent": float(m.get("percent") or 0)} for m in rows]
        except Exception:
            log.debug("brief: markets failed", exc_info=True)

        try:
            mine = await get_watchlist()
            rows = mine.get("stocks") or []
            if rows:
                lines = []
                for r in rows[:5]:
                    name = display_name(r.get("symbol"), r.get("name"))
                    spoken, written = self._pct(r.get("percent"))
                    lines.append((f"{name} {spoken}", f"{name} {written}"))
                gap = mine.get("missing") or []
                if gap:
                    note = ("couldn't reach " + ", ".join(gap))
                    lines.append((note, f"({note})"))
                out.append(("Yours", lines))
        except Exception:
            log.debug("brief: watchlist failed", exc_info=True)

        # The judgement, which is the half he actually asked for. Already prose,
        # so it reads and speaks the same way.
        try:
            from analyst import analyst
            rows = await analyst.take()
            if rows:
                out.append(("Worth a look", [(r["line"], r["line"]) for r in rows[:2]]))
        except Exception:
            log.debug("brief: market take failed", exc_info=True)

        # THE INTELLIGENT HALF. His instruction, 2026-09-05: "he mentions stocks
        # now but I need more intelligent info". What is moving the market and
        # why, from the desks he would trust; what the experts are saying; and
        # what reports this week - that last one in the first brief of the day
        # only, because the week's calendar does not change by the afternoon.
        try:
            from market_intel import intel
            for title, lines in await intel.brief_sections():
                if title == "Ahead" and not first:
                    continue
                out.append((title, lines))
        except Exception:
            log.debug("brief: market intelligence failed", exc_info=True)

        # NEWS BELONGS TO THE LAST BRIEF OF THE DAY. His instruction, 2026-09-05:
        # by day he wants only local and national emergencies, and "for his
        # nightly brief (last of the day) he can tell me general news so that
        # I'm still informed that way". So the midday briefs carry markets and
        # nothing else, and the night brief carries the news of the day -
        # GENERAL news, ranked, not only what would have interrupted him.
        mode = str(config.get("briefing", "news_in_briefs", default="last")).lower()
        want_news = mode == "all" or (mode == "last" and final)
        if not want_news:
            return out
        count = int(config.get("briefing", "night_news_count", default=5)) if final else 3
        picked = self.rank_for_brief(await self._fresh_stories(), count)
        # The brief says what happened, not what was printed. Read in parallel:
        # articles one after another would put ~15s into a brief nobody is
        # waiting on, and they have nothing to do with each other.
        news: list[tuple[str, str]] = []
        if picked:
            from newsroom import summarize_all
            said_all = await summarize_all(picked, limit=count)
            # ONE call with all of them. note_result REPLACES the link list, so
            # calling it per story in a loop left only the last one - and "give
            # me the article" opened story three whatever he asked for.
            self._remember_all(said_all)
            for said in said_all:
                body = said.get("summary") or said.get("headline") or ""
                src = said.get("source") or "the news"
                line = f"{body.rstrip(' .')} ({src})"
                news.append((line, line))
        if news:
            out.append(("News", news))

        if self._held:
            extra = [self._tidy(h["text"]) for h in self._held[-2:]]
            self._held.clear()
            out.append(("Also", [(e, e) for e in extra if e]))
        return out

    def _gauges_of(self, sections) -> list[dict]:
        """The index moves as numbers, for the HUD - recorded when the Markets
        section was built, so the screen and the voice cannot disagree."""
        titles = {t for t, _l in (sections or [])}
        return list(getattr(self, "_last_gauges", []) or []) if "Markets" in titles else []

    @staticmethod
    def is_final_slot(slot, times) -> bool:
        """Is this the last brief of the day - the one that carries the news?"""
        def minutes(s) -> int:
            try:
                t = dt.datetime.strptime(str(s), "%H:%M").time()
                return t.hour * 60 + t.minute
            except ValueError:
                return -1
        valid = [s for s in (times or []) if minutes(s) >= 0]
        return bool(valid) and str(slot) == str(max(valid, key=minutes))

    @staticmethod
    def is_first_slot(slot, times) -> bool:
        """Is this the morning brief - the one that carries the week ahead?"""
        def minutes(s) -> int:
            try:
                t = dt.datetime.strptime(str(s), "%H:%M").time()
                return t.hour * 60 + t.minute
            except ValueError:
                return -1
        valid = [s for s in (times or []) if minutes(s) >= 0]
        return bool(valid) and str(slot) == str(min(valid, key=minutes))

    def rank_for_brief(self, stories: list[dict], count: int) -> list[dict]:
        """The day's news, in the order he would want to hear it.

        Emergencies first, then what happened near him, then national news
        of weight, then the rest of the wire - the general news he asked to
        stay informed of. This deliberately uses the FULL classifier, not the
        emergencies-only gate: that gate decides what may interrupt him by day,
        and the night brief is the opposite of an interruption.
        """
        from significance import (NATIONAL_WEIGHT, _classify_news_full, _is_obituary, _text_of,
                                  is_local)
        # Five buckets, then an ORDER that mixes them. The first live night
        # brief (release 27) was five Massachusetts-desk items - penguin vests
        # off WCVB among them - and nothing from the wire, because "near him"
        # outranked everything that was not an emergency. Informed means both:
        # what happened in the country, and what happened near him, the
        # emergencies first and the fluff last.
        buckets: dict[str, list[dict]] = {k: [] for k in ("alarm", "national", "local", "wire", "colour")}
        for st in stories:
            tier, why = _classify_news_full(st)
            near, _town = is_local(st)
            if tier in (URGENT, ALERT):
                buckets["alarm"].append(st)
            elif NATIONAL_WEIGHT.search(_text_of(st)):
                buckets["national"].append(st)
            elif "death" in why or _is_obituary(_text_of(st)):
                # an obituary, an illness, a far-off death reprinted by a local
                # desk - a 'Blue Bloods' actor's cancer reached the first night
                # brief as Massachusetts news. Colour, not what happened today.
                buckets["colour"].append(st)
            elif near and tier != NONE and why not in ("local routine", "local colour"):
                buckets["local"].append(st)
            elif not near:
                buckets["wire"].append(st)
            else:
                buckets["colour"].append(st)
        ordered: list[dict] = list(buckets["alarm"])
        # national and local, turn and turn about, the country first
        a, b = buckets["national"], buckets["local"]
        for i in range(max(len(a), len(b))):
            if i < len(a):
                ordered.append(a[i])
            if i < len(b):
                ordered.append(b[i])
        ordered += buckets["wire"] + buckets["colour"]
        seen: set[str] = set()
        picked: list[dict] = []
        for st in ordered:
            head = self._tidy(st.get("headline"))
            if not head or head.lower() in seen:
                continue
            if any(self._same_story(head, h) for h in seen):
                continue
            seen.add(head.lower())
            picked.append(st)
            if len(picked) >= max(1, count):
                break
        return picked

    async def compose_brief(self, sections=None) -> str:
        """The spoken brief: flowing sentences, because he is hearing it.

        Pass `sections` to render a brief already built. Building it twice would
        double every market and news call AND silently disagree with itself: the
        held roll-up is consumed on the first build, so the second rendering would
        be missing the "Also" group he had just been read.
        """
        sections = await self._sections() if sections is None else sections
        if not sections:
            return "Nothing worth reporting, sir."
        parts = []
        for title, lines in sections:
            if not lines:
                continue
            body = "; ".join(spoken.rstrip(" .") for spoken, _written in lines)
            parts.append(f"{title}: {body}.")
        import re as _re
        return _re.sub(r"\.\.+", ".", " ".join(parts)) or "Nothing worth reporting, sir."

    async def compose_brief_written(self, sections=None) -> str:
        """The same brief for a screen: headed groups of one-line bullets.

        Deliberately plain text - no Markdown. Telegram would need every dash,
        dot and parenthesis in a news headline escaped, and one missed character
        makes the whole message fail to send rather than merely look wrong.
        """
        sections = await self._sections() if sections is None else sections
        if not sections:
            return "Nothing worth reporting, sir."
        blocks = []
        for title, lines in sections:
            if not lines:
                continue
            body = "\n".join(f"\u2022 {written.rstrip(' .')}"
                             for _spoken, written in lines)
            blocks.append(f"{title.upper()}\n{body}")
        return "\n\n".join(blocks)


briefing = Briefing()
