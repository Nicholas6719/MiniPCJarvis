"""The market as a person who follows it would describe it, not as a quote.

His instruction, 2026-09-05: *"improve Jarvis understanding of stocks and the
stock market. Let him compile data from finnhub but also verified and trusted
news sources about the state of the market and what experts are saying, and
that can be included in his briefs as well. He mentions stocks now but I need
more intelligent info."*

So four things, each from a source that is named when it is spoken:

    the gauges     S&P, Nasdaq, Dow (index ETFs), small caps, a volatility
                   proxy, and whether the market is open - Finnhub
    the story      what is moving the market today and why, in two spoken
                   sentences written from the headlines of the market desks he
                   would trust (the Journal, MarketWatch, CNBC, Reuters), with
                   the desk named
    the experts    what strategists and analysts are saying - the desks'
                   own "Goldman sees..." headlines, plus the analyst consensus
                   in analyst.py for the names in the conversation
    what's ahead   earnings this week for his names and for the companies
                   whose results move the whole market

And for one company: where it sits in its 52-week range, what it trades at
relative to earnings, how volatile it is, whether it beat last quarter, what
insiders are doing, and when it next reports.

Rules that hold throughout. Nothing here recommends; the model writes ONLY
the story, from headlines it is given, at temperature zero, and every other
sentence is templated from numbers. A source is always named. Anything that
fails is left out rather than guessed, and the caller sees a shorter answer,
never a wrong one.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import re
import time

from config import config

log = logging.getLogger("jarvis.market_intel")

# The desks. Verified live on 2026-09-05: each answers in under a second
# with real article links, which is what makes a headline attributable.
MARKET_FEEDS: tuple[tuple[str, str], ...] = (
    ("the Wall Street Journal", "https://feeds.a.dj.com/rss/RSSMarketsMain.xml"),
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_marketpulse"),
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
    ("CNBC", "https://www.cnbc.com/id/20910258/device/rss/rss.html"),
    ("CNBC", "https://www.cnbc.com/id/15839069/device/rss/rss.html"),
)

# A headline about the MARKET, rather than one company's product launch.
STATE_WORDS = re.compile(
    r"\b(?:stocks?|wall street|s&p|s and p|nasdaq|dow|the market|markets|equities|"
    r"fed|federal reserve|powell|rate cut|rate hike|interest rates?|treasur\w+|"
    r"yields?|bond market|inflation|cpi|jobs report|payrolls|unemployment|"
    r"earnings season|tariffs?|trade war|recession|rally|rallies|sell-?off|"
    r"rout|record high|correction|bear market|bull market|volatility|vix)\b", re.I)

# An expert weighing in, rather than a reporter reporting.
EXPERT_WORDS = re.compile(
    r"\b(?:strategists?|analysts?|economists?|expects?|sees|forecasts?|warns?|"
    r"predicts?|upgrades?|downgrades?|price target|top picks?|"
    r"goldman(?: sachs)?|morgan stanley|jpmorgan|jp morgan|bank of america|"
    r"citi(?:group)?|ubs|wells fargo|barclays|deutsche bank|evercore|wedbush|"
    r"bernstein|jefferies|piper sandler|raymond james|oppenheimer|"
    r"bofa|blackrock|vanguard|fidelity)\b", re.I)

# Not the market: the desks also run sports betting, promotions and personal
# finance listicles, none of which is the state of the market.
NOISE = re.compile(
    r"\b(?:nfl|nba|mlb|sportsbook|fantasy football|horoscope|best (?:cd|savings)"
    r" rates?|mortgage rates? today|credit card|sweepstakes|"
    r"here'?s how much|subscribe)\b", re.I)

# Companies whose results move the whole market, so their earnings are worth
# a line in the brief even though he does not own them.
BIG_NAMES: dict[str, str] = {
    "AAPL": "Apple", "MSFT": "Microsoft", "NVDA": "Nvidia", "AMZN": "Amazon",
    "GOOGL": "Alphabet", "GOOG": "Alphabet", "META": "Meta", "TSLA": "Tesla",
    "BRK.B": "Berkshire Hathaway", "JPM": "JPMorgan", "BAC": "Bank of America",
    "GS": "Goldman Sachs", "MS": "Morgan Stanley", "WMT": "Walmart",
    "COST": "Costco", "HD": "Home Depot", "XOM": "Exxon", "CVX": "Chevron",
    "UNH": "UnitedHealth", "JNJ": "Johnson and Johnson", "PFE": "Pfizer",
    "LLY": "Eli Lilly", "V": "Visa", "MA": "Mastercard", "NFLX": "Netflix",
    "DIS": "Disney", "INTC": "Intel", "AMD": "AMD", "AVGO": "Broadcom",
    "ORCL": "Oracle", "CRM": "Salesforce", "ADBE": "Adobe", "QCOM": "Qualcomm",
    "TSM": "TSMC", "BA": "Boeing", "CAT": "Caterpillar", "NKE": "Nike",
    "SBUX": "Starbucks", "MCD": "McDonald's", "KO": "Coca-Cola", "PEP": "Pepsi",
    "PG": "Procter and Gamble", "FDX": "FedEx", "UPS": "UPS", "DAL": "Delta",
    "UBER": "Uber", "PLTR": "Palantir", "COIN": "Coinbase", "HOOD": "Robinhood",
}

MAX_AGE_MIN = 18 * 60          # overnight is still today's story; last week is not
STORY_HEADLINES = 8            # what the model is shown
CACHE_S = 600.0                # the story does not change by the minute

STORY_PROMPT = (
    "You are the market desk editor for a short spoken briefing. From the "
    "headlines below, write exactly TWO short sentences on the state of the US "
    "stock market today and what is driving it. Say only what the headlines "
    "say; if they point different ways, say so. Name at least one desk in "
    "words, for example 'according to the Journal' or 'CNBC reports'. No "
    "advice, no numbers that are not in the headlines, no markdown, no "
    "preamble.\n\nHeadlines:\n{lines}\n\nTwo sentences:"
)


# --- pure helpers, gated offline ---------------------------------------------------

def _text(story: dict) -> str:
    return " ".join(str(story.get(k) or "") for k in ("headline", "summary"))


def relevant(story: dict, max_age_min: float = MAX_AGE_MIN) -> bool:
    """Is this headline about the state of the market, and from today?"""
    age = story.get("age_minutes")
    if age is not None and age > max_age_min:
        return False
    t = _text(story)
    return bool(STATE_WORDS.search(t)) and not NOISE.search(t)


def expert(story: dict) -> bool:
    """Is this an expert saying something, rather than a price being reported?"""
    return bool(EXPERT_WORDS.search(str(story.get("headline") or "")))


_WORD = re.compile(r"[a-z0-9]+")
_STOP = {"the", "a", "an", "of", "to", "in", "on", "as", "and", "for", "is",
         "are", "at", "by", "with", "after", "amid", "from", "its", "it", "s",
         "stocks", "stock", "market", "markets", "wall", "street", "today"}


def _sig(head: str) -> set[str]:
    return {w for w in _WORD.findall(head.lower()) if w not in _STOP and len(w) > 2}


def same_story(a: str, b: str) -> bool:
    """Two desks, one event. Half the meaningful words shared is the same story."""
    sa, sb = _sig(a), _sig(b)
    if not sa or not sb:
        return False
    common = len(sa & sb)
    return common >= 3 and common >= 0.5 * min(len(sa), len(sb))


def dedupe(stories: list[dict]) -> list[dict]:
    out: list[dict] = []
    for st in stories:
        head = str(st.get("headline") or "").strip()
        if not head:
            continue
        if any(same_story(head, str(o.get("headline") or "")) for o in out):
            continue
        out.append(st)
    return out


def move_words(pct: float | None) -> str:
    p = float(pct or 0)
    if abs(p) < 0.05:
        return "flat"
    return f"{'up' if p > 0 else 'down'} {abs(p):.1f} percent"


def gauges_line(markets: list[dict], small: dict | None, vol: dict | None,
                status: dict | None) -> str:
    """The numbers as one spoken sentence, with the market's state of play."""
    parts = [f"{m.get('name')} {move_words(m.get('percent'))}" for m in markets
             if m.get("name")]
    if small and small.get("percent") is not None:
        parts.append(f"small caps {move_words(small.get('percent'))}")
    line = ", ".join(parts)
    if vol and vol.get("percent") is not None and abs(float(vol["percent"])) >= 3:
        v = float(vol["percent"])
        line += (f"; volatility is {'up' if v > 0 else 'down'} "
                 f"{abs(v):.0f} percent" if line else
                 f"volatility is {'up' if v > 0 else 'down'} {abs(v):.0f} percent")
    if status:
        if status.get("holiday"):
            line += f". The market is closed for {status['holiday']}"
        elif status.get("isOpen") is False:
            line += ". The market is closed"
    return line.strip()


def earnings_line(rows: list[dict], today: dt.date | None = None) -> str:
    """'Earnings ahead: Apple on Thursday after the close; Nvidia on Wednesday.'"""
    today = today or dt.date.today()
    said = []
    for r in rows[:4]:
        try:
            day = dt.date.fromisoformat(str(r.get("date")))
        except (TypeError, ValueError):
            continue
        when = ("today" if day == today else "tomorrow" if day == today + dt.timedelta(days=1)
                else "on " + day.strftime("%A"))
        hour = {"bmo": " before the open", "amc": " after the close"}.get(str(r.get("hour") or ""), "")
        mine = ", which you own" if r.get("held") else ""
        said.append(f"{r.get('name') or r.get('symbol')} {when}{hour}{mine}")
    return ("Earnings ahead: " + "; ".join(said)) if said else ""


def context_line(ctx: dict) -> str:
    """One company, as a person who follows it would put it. Numbers, then
    the analysts, then the calendar. Nothing here is advice and it says so."""
    name = ctx.get("name") or ctx.get("symbol")
    bits = []
    price, pct = ctx.get("price"), ctx.get("percent")
    hi, lo = ctx.get("high_52w"), ctx.get("low_52w")
    where = ""
    if price and hi and lo and hi > lo:
        pos = (float(price) - float(lo)) / (float(hi) - float(lo))
        where = ("near its 52-week high" if pos >= 0.9 else
                 "near its 52-week low" if pos <= 0.1 else
                 f"about {int(round(pos * 100))} percent of the way up its 52-week range")
    if price is not None:
        bits.append(f"{name} is at {price} dollars, {move_words(pct)} today"
                    + (f", {where}" if where else ""))
    elif where:
        bits.append(f"{name} is {where}")
    ytd = ctx.get("ytd_percent")
    if ytd is not None:
        bits.append(f"{move_words(ytd)} this year")
    pe = ctx.get("pe")
    if pe:
        bits.append(f"it trades at {float(pe):.0f} times earnings")
    beta = ctx.get("beta")
    if beta:
        b = float(beta)
        bits.append("it is a volatile name" if b >= 1.5 else
                    "it is steadier than the market" if b <= 0.7 else "")
    sur = ctx.get("last_surprise_percent")
    if sur is not None:
        s = float(sur)
        bits.append(f"last quarter it {'beat' if s > 0 else 'missed'} estimates by "
                    f"{abs(s):.1f} percent" if abs(s) >= 0.5 else
                    "last quarter it landed on estimates")
    cons = ctx.get("consensus")
    if cons and ctx.get("analysts"):
        bits.append(f"{ctx.get('buy', 0)} of {ctx['analysts']} analysts say buy"
                    if cons == "buy" else
                    f"{ctx.get('sell', 0)} of {ctx['analysts']} analysts say sell"
                    if cons == "sell" else
                    f"analysts are split, {ctx.get('buy', 0)} buy and {ctx.get('hold', 0)} hold")
    ins = ctx.get("insider_mspr")
    if ins is not None and abs(float(ins)) >= 20:
        bits.append("insiders have been " + ("buying" if float(ins) > 0 else "selling"))
    nxt = ctx.get("next_earnings")
    if nxt:
        try:
            day = dt.date.fromisoformat(str(nxt))
            bits.append(f"it reports next on {day.strftime('%B')} {day.day}")
        except ValueError:
            pass
    bits = [b for b in bits if b]
    if not bits:
        return f"I couldn't get a read on {name} just now."
    line = ". ".join(b[0].upper() + b[1:] for b in bits) + "."
    return line + " That is the picture, not advice."


# --- the live half -------------------------------------------------------------

class MarketIntel:
    def __init__(self) -> None:
        self._story: tuple[float, dict] | None = None
        self._headlines: tuple[float, list[dict]] | None = None

    async def headlines(self) -> list[dict]:
        """Fresh market headlines from the desks, newest first, one per story."""
        if self._headlines and time.time() - self._headlines[0] < CACHE_S:
            return self._headlines[1]
        import httpx
        from tools.news_tools import _fetch_feed
        items: list[dict] = []
        try:
            async with httpx.AsyncClient() as client:
                got = await asyncio.gather(
                    *(_fetch_feed(client, name, url) for name, url in MARKET_FEEDS),
                    return_exceptions=True)
            for g in got:
                if isinstance(g, list):
                    items += g
        except Exception:
            log.debug("market feeds failed", exc_info=True)
        # Reuters and the rest of the wire, through Finnhub's general feed.
        try:
            from tools.market_tools import _get
            wire = await _get("/news", category="general")
            for a in (wire or [])[:60] if isinstance(wire, list) else []:
                head = str(a.get("headline") or "")
                src = str(a.get("source") or "the wire")
                head = re.sub(r"\s+-\s+" + re.escape(src) + r"$", "", head)
                when = a.get("datetime")
                age = (max(0, int((time.time() - float(when)) // 60))
                       if when else None)
                items.append({"headline": head, "source": src,
                              "url": a.get("url"), "summary": str(a.get("summary") or "")[:220],
                              "age_minutes": age})
        except Exception:
            log.debug("finnhub general news failed", exc_info=True)
        keep = [i for i in items if relevant(i)]
        keep.sort(key=lambda i: i.get("age_minutes") if i.get("age_minutes") is not None else 1e9)
        keep = dedupe(keep)
        self._headlines = (time.time(), keep)
        return keep

    async def story(self) -> dict:
        """{'text', 'sources', 'headlines'}: two sentences on the day, attributed."""
        if self._story and time.time() - self._story[0] < CACHE_S:
            return self._story[1]
        heads = (await self.headlines())[:STORY_HEADLINES]
        out = {"text": "", "sources": [], "headlines": heads}
        if heads:
            lines = "\n".join(f"- {h['headline']} ({h['source']})" for h in heads)
            try:
                from llm.provider import local_llm
                text = ""
                async for ch in local_llm.stream(
                        [{"role": "user", "content": STORY_PROMPT.format(lines=lines)}],
                        max_tokens=160, sampling={"temperature": 0.0}):
                    text += ch.text
                    if ch.done:
                        break
                out["text"] = tidy_story(text)
            except Exception:
                log.debug("market story failed", exc_info=True)
            out["sources"] = sorted({h["source"] for h in heads})
        self._story = (time.time(), out)
        return out

    async def experts(self, limit: int = 2) -> list[dict]:
        """What strategists are saying, as the desks reported it."""
        heads = [h for h in await self.headlines() if expert(h)]
        return heads[:limit]

    async def gauges(self) -> dict:
        from tools.market_tools import _get, get_market_movers
        movers, small, vol, status = await asyncio.gather(
            get_market_movers(), _get("/quote", symbol="IWM"),
            _get("/quote", symbol="VIXY"), _get("/stock/market-status", exchange="US"),
            return_exceptions=True)
        markets = movers.get("markets") if isinstance(movers, dict) else []
        def q(x, name):
            if isinstance(x, dict) and x.get("c"):
                return {"name": name, "price": round(float(x["c"]), 2),
                        "percent": round(float(x.get("dp") or 0), 2)}
            return None
        return {"markets": markets or [], "small_caps": q(small, "small caps"),
                "volatility": q(vol, "volatility"),
                "status": status if isinstance(status, dict) and "isOpen" in status else None}

    async def earnings_ahead(self, days: int = 7) -> list[dict]:
        """This week's earnings that matter to him: his names, and the big ones."""
        from tools.market_tools import _get
        today = dt.date.today()
        data = await _get("/calendar/earnings", **{"from": str(today),
                                                   "to": str(today + dt.timedelta(days=days))})
        rows = (data or {}).get("earningsCalendar") if isinstance(data, dict) else None
        if not rows:
            return []
        held = {str(s).upper() for s in (config.get("markets", "watchlist", default=[]) or [])}
        from analyst import NAME_BY_TICKER
        out = []
        for r in rows:
            sym = str(r.get("symbol") or "").upper()
            if sym in held or sym in BIG_NAMES:
                out.append({"symbol": sym, "name": NAME_BY_TICKER.get(sym) or BIG_NAMES.get(sym) or sym,
                            "date": r.get("date"), "hour": r.get("hour"),
                            "eps_estimate": r.get("epsEstimate"), "held": sym in held})
        out.sort(key=lambda r: (str(r["date"]), not r["held"]))
        return out

    async def stock_context(self, symbol: str) -> dict:
        """One company: price, range, valuation, last quarter, analysts, insiders,
        next report. Each part is optional; a missing one is left out."""
        from tools.market_tools import _get, _resolve_symbol, get_analyst_view, get_stock_quote
        hit = await _resolve_symbol(symbol)
        if hit is None:
            return {"error": f"I couldn't find a listed company called {symbol}."}
        tick, name = hit
        today = dt.date.today()
        quote, metric, earn, view, insider, cal = await asyncio.gather(
            get_stock_quote(tick), _get("/stock/metric", symbol=tick, metric="all"),
            _get("/stock/earnings", symbol=tick), get_analyst_view(tick),
            _get("/stock/insider-sentiment", symbol=tick,
                 **{"from": str(today - dt.timedelta(days=90)), "to": str(today)}),
            _get("/calendar/earnings", symbol=tick,
                 **{"from": str(today), "to": str(today + dt.timedelta(days=100))}),
            return_exceptions=True)
        from analyst import speakable
        ctx: dict = {"symbol": tick, "name": speakable(name or tick)}
        if isinstance(quote, dict) and not quote.get("error"):
            ctx["price"], ctx["percent"] = quote.get("price"), quote.get("percent")
            ctx["name"] = speakable(quote.get("name") or ctx["name"])
        m = (metric or {}).get("metric") if isinstance(metric, dict) else None
        if isinstance(m, dict):
            ctx["high_52w"], ctx["low_52w"] = m.get("52WeekHigh"), m.get("52WeekLow")
            ctx["pe"] = m.get("peTTM") or m.get("peBasicExclExtraTTM")
            ctx["beta"] = m.get("beta")
            ctx["ytd_percent"] = m.get("yearToDatePriceReturnDaily")
        if isinstance(earn, list) and earn:
            ctx["last_surprise_percent"] = earn[0].get("surprisePercent")
        if isinstance(view, dict) and not view.get("error"):
            for k in ("consensus", "buy", "hold", "sell", "analysts"):
                ctx[k] = view.get(k)
        data = (insider or {}).get("data") if isinstance(insider, dict) else None
        if isinstance(data, list) and data:
            ctx["insider_mspr"] = data[-1].get("mspr")
        rows = (cal or {}).get("earningsCalendar") if isinstance(cal, dict) else None
        if isinstance(rows, list) and rows:
            ctx["next_earnings"] = min(str(r.get("date")) for r in rows if r.get("date"))
        ctx["spoken"] = context_line(ctx)
        return ctx

    async def state(self) -> dict:
        """The whole picture, for a question or a brief."""
        g, s, ex = await asyncio.gather(self.gauges(), self.story(), self.experts(),
                                        return_exceptions=True)
        g = g if isinstance(g, dict) else {"markets": []}
        s = s if isinstance(s, dict) else {"text": "", "sources": []}
        ex = ex if isinstance(ex, list) else []
        numbers = gauges_line(g["markets"], g.get("small_caps"), g.get("volatility"), g.get("status"))
        parts = []
        if numbers:
            parts.append(numbers + ".")
        if s.get("text"):
            parts.append(s["text"])
        if ex:
            parts.append("Among the experts, " + "; ".join(
                f"{h['source']} reports: {h['headline'].rstrip('.')}" for h in ex) + ".")
        spoken = " ".join(parts) or "I couldn't get a read on the market just now."
        # ON SCREEN too: the gauges as numbers, the story with its desks, the
        # experts' lines - the same picture he hears, for as long as he looks.
        try:
            from events import bus
            gauges = [{"name": m.get("name"), "percent": float(m.get("percent") or 0)}
                      for m in g["markets"] if m.get("name")]
            for extra in (g.get("small_caps"), g.get("volatility")):
                if extra and extra.get("percent") is not None:
                    gauges.append({"name": extra["name"], "percent": float(extra["percent"])})
            sections = []
            if s.get("text"):
                src = ", ".join(s.get("sources") or [])
                sections.append({"title": "The story",
                                 "lines": [s["text"] + (f" ({src})" if src else "")]})
            if ex:
                sections.append({"title": "Experts",
                                 "lines": [f"{h['headline'].rstrip('.')} ({h['source']})" for h in ex]})
            status = g.get("status") or {}
            if status.get("holiday"):
                sections.append({"title": "Session", "lines": [f"Closed for {status['holiday']}"]})
            elif status.get("isOpen") is False:
                sections.append({"title": "Session", "lines": ["The market is closed"]})
            await bus.emit("brief", title="The market", eyebrow="THE MARKET",
                           sections=sections, gauges=gauges)
        except Exception:
            log.debug("could not put the market on screen", exc_info=True)
        return {"spoken": spoken, "gauges": g, "story": s.get("text"),
                "sources": s.get("sources"), "experts": ex}

    async def brief_sections(self) -> list[tuple[str, list[tuple[str, str]]]]:
        """The intelligent half of the market brief: the story, the experts,
        what is ahead. Markets and his holdings are already in the brief."""
        out: list[tuple[str, list[tuple[str, str]]]] = []
        try:
            s = await self.story()
            if s.get("text"):
                src = ", ".join(s.get("sources") or [])
                out.append(("The story", [(s["text"], f"{s['text']} ({src})" if src else s["text"])]))
        except Exception:
            log.debug("brief: market story failed", exc_info=True)
        try:
            ex = await self.experts(2)
            if ex:
                out.append(("Experts", [(f"{h['source']} reports {h['headline'].rstrip('.')}",
                                         f"{h['headline'].rstrip('.')} ({h['source']})") for h in ex]))
        except Exception:
            log.debug("brief: experts failed", exc_info=True)
        try:
            rows = await self.earnings_ahead()
            line = earnings_line(rows)
            if line:
                out.append(("Ahead", [(line, line)]))
        except Exception:
            log.debug("brief: earnings ahead failed", exc_info=True)
        return out


_ABBREV = re.compile(r"\b(?:U\.S|U\.K|U\.N|E\.U|Inc|Corp|Ltd|Co|Mr|Mrs|Ms|Dr|St|vs|a\.m|p\.m|Jan|Feb|Aug|Sept|Oct|Nov|Dec)\.$", re.I)


def split_sentences(text: str) -> list[str]:
    """Sentences, without cutting at 'the U.S.' - which the first live night
    brief did: 'According to MarketWatch, the U.S.' was sentence one."""
    out: list[str] = []
    for piece in re.split(r"(?<=[.!?])\s+", text):
        if out and (_ABBREV.search(out[-1]) or re.search(r"\b[A-Z]\.$", out[-1])):
            out[-1] = out[-1] + " " + piece
        else:
            out.append(piece)
    return [p for p in out if p]


def tidy_story(text: str) -> str:
    s = re.sub(r"\s+", " ", str(text or "")).strip().strip('"')
    s = re.sub(r"^(?:two sentences|summary|answer)\s*:\s*", "", s, flags=re.I)
    parts = split_sentences(s)
    # a trailing fragment (the model ran out of tokens mid-sentence) is dropped
    # rather than read aloud as a sentence that stops nowhere
    if parts and not re.search(r"[.!?]$", parts[-1]):
        parts = parts[:-1]
    s = " ".join(parts[:2]).strip()
    return s if len(s) > 20 else ""


intel = MarketIntel()


# --- tools -----------------------------------------------------------------------

async def get_market_state() -> dict:
    return await intel.state()


async def get_earnings_ahead(days: int = 7) -> dict:
    rows = await intel.earnings_ahead(max(1, min(14, int(days or 7))))
    line = earnings_line(rows)
    return {"count": len(rows), "earnings": rows,
            "spoken": line or "Nothing of yours, and none of the big names, reports in that window."}


async def get_stock_context(symbol: str) -> dict:
    return await intel.stock_context(symbol)


def register_all() -> None:
    from tools.registry import Risk, Tool, registry
    registry.register(Tool(
        name="get_market_state",
        description="The state of the US stock market today: the indexes, volatility, "
                    "whether it is open, WHAT IS DRIVING IT according to the market desks "
                    "(the Journal, MarketWatch, CNBC, Reuters) and what strategists are "
                    "saying. Use for 'how's the market', 'what's going on in the market', "
                    "'why is the market down', 'what's driving stocks today'.",
        parameters={"type": "object", "properties": {}, "required": []},
        risk=Risk.SAFE, handler=get_market_state, timeout=90))
    registry.register(Tool(
        name="get_earnings_ahead",
        description="Which companies report earnings in the coming days - the user's own "
                    "names and the big ones that move the market. Use for 'any earnings "
                    "this week', 'when does Apple report', 'what's coming up in the market'.",
        parameters={"type": "object", "properties": {
            "days": {"type": "integer", "minimum": 1, "maximum": 14}}, "required": []},
        risk=Risk.SAFE, handler=get_earnings_ahead, timeout=30))
    registry.register(Tool(
        name="get_stock_context",
        description="The full picture on ONE company: price and day move, where it sits in "
                    "its 52-week range, year-to-date, P/E, volatility, last quarter's beat "
                    "or miss, analyst consensus, insider buying or selling, next earnings "
                    "date. Use for 'tell me about Nvidia stock', 'what should I know about "
                    "Apple', 'give me the picture on Tesla'.",
        parameters={"type": "object", "properties": {
            "symbol": {"type": "string", "description": "ticker or company name"}},
            "required": ["symbol"]},
        risk=Risk.SAFE, handler=get_stock_context, timeout=45))
