"""What the market people are actually talking about today, and what to make of it.

Nicholas was precise about what this must NOT be: *"I don't want a data dump."*
He can read a quote himself. What he asked for is judgement — *"here are the top
eight stocks experts are talking about today"* — including names he does not own:
*"Experts are saying buy into Nike stock even though I don't own it."* And he gave
the shape of the judgement in one line:

    *"Nvidia is up right now, so you shouldn't buy in."*

That is not a price. It is a price PLUS a view PLUS timing, which is the only
combination worth speaking aloud. So each name here carries three things:

    who is talking     the financial press, counted by how often a name comes up
    what they say      Finnhub's analyst consensus, buy/hold/sell, attributed
    where it is now    today's move, which decides whether the view is actionable

The stance rules are deliberately mechanical rather than a model's opinion. An
LLM inventing conviction about somebody's money is exactly the wrong use of one:
these say what analysts think and whether the stock has already run, and stop
there. Nothing here is a recommendation and it says so out loud.

Two things the live feed forced, neither of which was in the design:

  * Finnhub's free "general" news feed carries NO ticker tags at all - the
    `related` field is empty on all 100 items - so the names have to be read out
    of the headlines themselves.
  * Those headlines are full of Bombay and Sydney. "Stocks to watch: Wipro, Hero
    MotoCorp, HDFC Bank, Tata Motors" is a real result. He asked about the US
    market, so every candidate is confirmed US-listed before it can be spoken.
"""
from __future__ import annotations

import asyncio
import logging
import re

log = logging.getLogger("jarvis.analyst")

# How the press signals that experts are weighing in, rather than merely
# reporting a price. Each is a separate search: one query returns one newsroom's
# idea of the day, several return a consensus.
QUERIES = (
    "analyst upgrade price target stocks",
    "stocks to watch today",
    "analyst downgrade stock",
    "top stock picks analysts",
)

# A ticker written the way the press writes it: "(NVDA)", "(NASDAQ:SMTC)",
# "(NYSE: X)". This is the strongest signal there is - the newsroom has already
# done the disambiguation for us.
TAGGED = re.compile(r"\((?:NASDAQ|NYSE|AMEX|NYSEARCA|OTC)?\s*:?\s*([A-Z]{1,5})\)")

# A bare shouted ticker: "BLZE Stock On Track", "QUBT Opinions". Weaker, because
# plenty of ordinary shouting looks the same - hence the stop list below.
#
# THREE letters minimum, and that floor is load-bearing. The first live run
# nominated AS (Amer Sports) off "rallies AS analyst upgrade", and AD (Array
# Digital) the same way. Both are genuinely US-listed, so confirming the
# listing did not save us. Nearly every two-letter ticker is also an English
# word, while a real one worth discussing (F, GM, BA) arrives tagged or by
# name anyway.
BARE = re.compile(r"\b([A-Z]{3,5})\b")

# ...unless the press stands it next to the word Stock or Shares, which is how a
# newsroom writes a ticker it expects you to recognise: "BLZE Stock On Track",
# "shares of QUBT". That is evidence. A capitalised word inside a list is not.
BARE_STRONG = re.compile(
    r"\b([A-Z]{3,5})[ ]+(?:Stock|Shares|stock|shares)\b"
    r"|(?:shares|stock) of[ ]+([A-Z]{3,5})\b")

# Capitalised things that are not companies. Without this, "US Stock Market
# Today: S&P 500 Futures Slip As Traders Watch PCE And Fed" nominates PCE.
NOT_TICKERS = {
    "US", "USA", "UK", "EU", "AI", "IPO", "ETF", "CEO", "CFO", "COO", "CTO",
    "FED", "FOMC", "PCE", "CPI", "GDP", "PPI", "SEC", "FCC", "FDA", "FTC", "DOJ",
    "IRS", "NYSE", "NASDAQ", "AMEX", "OTC", "SP", "DJIA", "ESG", "EPS", "PE",
    "YOY", "QOQ", "EBIT", "EBITDA", "ROI", "ROE", "IPOS", "M&A", "MA",
    "Q1", "Q2", "Q3", "Q4", "H1", "H2", "FY", "TV", "PC", "EV", "OS", "APP",
    "NEWS", "STOCK", "STOCKS", "MARKET", "BUY", "SELL", "HOLD", "TOP", "NEW",
    "BEST", "WATCH", "TODAY", "WEEK", "DAY", "YEAR", "HIGH", "LOW", "UP", "DOWN",
    "THE", "AND", "FOR", "ITS", "WHY", "HOW", "WHAT", "WHO", "NOW", "BIG",
    "BLACKROCK", "UBS", "RBC", "BMO", "TD", "HSBC", "BNP", "JPM",
    "GAP", "BAR", "BULL", "BEAR", "BEARS", "BULLS", "BAN", "WAR", "OIL", "GAS",
    "CHINA", "INDIA", "JAPAN", "IRAN", "AMID", "AFTER", "OVER", "INTO", "WITH",
    # three-letter English that survives the length floor
    "ALL", "ARE", "BUT", "CAN", "DID", "GET", "HAS", "HAD", "HER", "HIM", "HIS",
    "ITS", "MAY", "NOT", "ONE", "OUR", "OUT", "SAY", "SEE", "SET", "SHE", "TWO",
    "WAS", "WAY", "WHY", "YET", "YOU", "OFF", "OWN", "PER", "PRO", "RUN", "TOO",
    "USE", "WIN", "WON", "CUT", "HIT", "LED", "PUT", "SIX", "TEN", "ADD", "AGO",
    "AIM", "BID", "END", "EYE", "FAR", "FEW", "KEY", "LOT", "MOST", "MUCH",
    "NEAR", "NEXT", "PLAN", "SAID", "SAYS", "THAN", "THAT", "THEN", "THIS",
    "TIME", "WILL", "WITH", "YEARS", "AHEAD", "AMONG", "COULD", "FIRST",
}

# Names the press uses without ever printing the ticker: "Amazon, Palantir,
# Nvidia In Focus". Deliberately short and mega-cap only - a long list of
# ambiguous company words ("Target", "Visa", "Gap") creates more false names than
# it finds real ones, and a false name here is a stock tip about nothing.
BY_NAME = {
    "nvidia": "NVDA", "apple": "AAPL", "microsoft": "MSFT", "amazon": "AMZN",
    "alphabet": "GOOGL", "google": "GOOGL", "meta": "META", "tesla": "TSLA",
    "palantir": "PLTR", "broadcom": "AVGO", "netflix": "NFLX", "coinbase": "COIN",
    "advanced micro devices": "AMD", "intel": "INTC", "micron": "MU",
    "salesforce": "CRM", "oracle": "ORCL", "walmart": "WMT", "costco": "COST",
    "boeing": "BA", "nike": "NKE", "starbucks": "SBUX", "pfizer": "PFE",
    "eli lilly": "LLY", "jpmorgan": "JPM", "goldman sachs": "GS", "ford": "F",
    "general motors": "GM", "rivian": "RIVN", "lucid": "LCID", "uber": "UBER",
    "airbnb": "ABNB", "shopify": "SHOP", "snowflake": "SNOW", "crowdstrike": "CRWD",
    "super micro": "SMCI", "arm holdings": "ARM", "qualcomm": "QCOM",
    "disney": "DIS", "moderna": "MRNA", "robinhood": "HOOD", "spotify": "SPOT",
}

MAX_NAMES = 8            # his number, verbatim
MAX_LOOKUPS = 24         # candidates confirmed before giving up, to bound API calls
EXTENDED_PCT = 3.0       # "up right now" - a move big enough that buying is chasing
DIP_PCT = -2.0           # sold off enough that a positive view is actionable


# Finnhub returns filing names: "Amazon.Com Inc", "Nvidia Corp", "Advanced Micro
# Devices". Spoken aloud those are wrong - nobody says "Amazon dot com ink" - and
# this is a speech-first assistant before it is a screen.
SUFFIX = re.compile(r"\s*\b(?:inc|corp|corporation|co|company|ltd|limited|plc|holdings?|group|sa|nv|ag|the)\b\.?" * 1 + r"\s*$", re.I)


def speakable(name: str) -> str:
    """A company name as a person would say it."""
    out = str(name or '').strip()
    out = re.sub(r"\.[Cc]om\b", "", out)      # Amazon.Com -> Amazon
    # Share classes: "Backblaze Inc-A", "Alphabet Inc Cl C". He is being told what
    # the company is, not which line of the cap table it trades on.
    out = re.sub(r"[\s-]+(?:Cl(?:ass)?[\s-]*)?[A-C]$", "", out)
    out = re.sub(r"\s*-\s*[A-C]\b", "", out)
    out = re.sub(r"^[Tt]he \s*", "", out)      # The Walt Disney -> Walt Disney
    for _ in range(3):                        # 'Alphabet Inc Class A Corp'
        trimmed = SUFFIX.sub('', out).strip(' ,.')
        if trimmed == out or not trimmed:
            break
        out = trimmed
    return out or str(name or '').strip()


def _text(story: dict) -> str:
    return " ".join(str(story.get(k) or "") for k in ("headline", "title", "summary"))


def candidates(stories: list[dict]) -> list[tuple[str, int]]:
    """Ticker symbols the press is talking about, most-discussed first.

    Counts DISTINCT headlines, not mentions: an article that says NVDA six times
    is still one newsroom holding one opinion.

    Evidence is GRADED, and that grading is what keeps foreign names out. A live
    run put Flanigan's Enterprises - a Florida restaurant chain - in front of him,
    because "Analysts recommend buying Kotak Mahindra Bank, AU SFB, BDL, Kaynes
    Tech" is an Indian headline, and BDL is Bharat Dynamics there and Flanigan's
    here. Confirming the US listing could not catch it: the symbol is real, it is
    simply a different company. So a bare capitalised word in a list is treated as
    the weak evidence it is, and has to appear twice before it costs him anything.
    """
    strong: dict[str, int] = {}
    weak: dict[str, int] = {}
    for story in stories:
        t = _text(story)
        sure: set[str] = set()
        maybe: set[str] = set()
        sure.update(TAGGED.findall(t))
        for a, b in BARE_STRONG.findall(t):
            sure.add(a or b)
        low = t.lower()
        for name, sym in BY_NAME.items():
            if re.search(r"\b" + re.escape(name) + r"\b", low):
                sure.add(sym)
        for sym in BARE.findall(t):
            if sym not in NOT_TICKERS and sym not in sure:
                maybe.add(sym)
        for sym in sure:
            strong[sym] = strong.get(sym, 0) + 1
        for sym in maybe:
            weak[sym] = weak.get(sym, 0) + 1

    counts = dict(strong)
    for sym, n in weak.items():
        # One unsupported shout inside a list of names is not worth his
        # attention. Twice, and somebody means it.
        if sym in counts or n >= 2:
            counts[sym] = counts.get(sym, 0) + n
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def stance(*, name: str, percent: float, consensus: str, buy: int, hold: int,
           sell: int, analysts: int, held: bool) -> tuple[str, str]:
    """(verdict, one sentence). What analysts think, and whether it is buyable now.

    This is the shape he asked for. Analysts supply the view; today's move
    supplies the timing; the two together are the only thing worth saying. The
    verdict never comes from a model and never pretends to certainty.
    """
    pct = float(percent or 0)
    move = (f"up {pct:.1f}% today" if pct > 0 else
            f"down {abs(pct):.1f}% today" if pct < 0 else "flat today")
    mine = " — and you own it" if held else ""

    if not analysts:
        return "no coverage", f"{name} is {move}, but no analyst covers it{mine}."

    view = f"{buy} of {analysts} analysts say buy" if consensus == "buy" else \
           f"{sell} of {analysts} say sell" if consensus == "sell" else \
           f"analysts are split — {buy} buy, {hold} hold, {sell} sell"

    if consensus == "buy" and pct >= EXTENDED_PCT:
        # His own example, generalised: the view is good and the entry is not.
        return "positive, but extended", (
            f"{name}: {view}, but it is {move} — that is chasing it, not buying "
            f"a dip{mine}.")
    if consensus == "buy" and pct <= DIP_PCT:
        return "positive, and cheaper", (
            f"{name}: {view}, and it is {move}{mine}.")
    if consensus == "buy":
        return "positive", f"{name}: {view}. It is {move}{mine}."
    if consensus == "sell":
        return "negative", (
            f"{name}: {view}. It is {move}{mine}." if not held else
            f"{name}: {view}, and it is {move} — worth a look, since you own it.")
    return "split", f"{name}: {view}. It is {move}{mine}."


class Analyst:
    """Reads the financial press, then asks Finnhub what the analysts think."""

    def __init__(self) -> None:
        self._names: dict[str, str] = {}     # symbol -> company name

    async def chatter(self) -> list[dict]:
        """Every headline in which somebody is expressing a view today."""
        from tools.news_tools import get_news
        out: list[dict] = []
        for q in QUERIES:
            try:
                res = await get_news(query=q, count=10)
                out += (res.get("items") or [])
            except Exception:
                log.debug("chatter query failed: %s", q, exc_info=True)
        return out

    async def shortlist(self, limit: int = MAX_NAMES) -> list[str]:
        """The most-discussed US-listed symbols, confirmed to actually exist.

        Confirmation is the load-bearing step, and not for tidiness: the press
        feed is full of Wipro, Tata Motors and Pilbara Minerals. He asked about
        the US market, and a symbol that does not resolve on a US exchange never
        reaches him.
        """
        from tools.market_tools import _resolve_symbol
        picked: list[str] = []
        tried = 0
        for sym, _n in candidates(await self.chatter()):
            if len(picked) >= limit or tried >= MAX_LOOKUPS:
                break
            tried += 1
            try:
                hit = await _resolve_symbol(sym)
            except Exception:
                log.debug("resolve failed for %s", sym, exc_info=True)
                continue
            if hit is None:
                continue
            tick, name = hit
            if tick in picked:
                continue
            self._names[tick] = name
            picked.append(tick)
        return picked

    async def _one(self, symbol: str, held: bool) -> dict | None:
        from tools.market_tools import get_analyst_view, get_stock_quote
        quote, view = await asyncio.gather(
            get_stock_quote(symbol), get_analyst_view(symbol),
            return_exceptions=True)
        if isinstance(quote, Exception) or not isinstance(quote, dict) or quote.get("error"):
            return None
        v = view if isinstance(view, dict) and not view.get("error") else {}
        name = speakable(quote.get("name") or self._names.get(symbol)
                         or symbol)
        verdict, line = stance(
            name=name, percent=quote.get("percent") or 0,
            consensus=v.get("consensus") or "", buy=int(v.get("buy") or 0),
            hold=int(v.get("hold") or 0), sell=int(v.get("sell") or 0),
            analysts=int(v.get("analysts") or 0), held=held)
        return {"symbol": symbol, "name": name, "price": quote.get("price"),
                "percent": quote.get("percent"), "held": held,
                "verdict": verdict, "line": line,
                "analysts": int(v.get("analysts") or 0)}

    async def take(self, limit: int = MAX_NAMES) -> list[dict]:
        """The shortlist, with a stance on each."""
        from config import config
        held = {str(s).upper() for s in
                (config.get("markets", "watchlist", default=[]) or [])}
        syms = await self.shortlist(limit)
        rows = await asyncio.gather(*(self._one(s, s in held) for s in syms),
                                    return_exceptions=True)
        out = [r for r in rows if isinstance(r, dict)]
        # No analyst covers it, so by definition the experts are NOT talking about
        # it, whatever the headline was. This is the second half of the Flanigan's
        # problem: grading the evidence stops most of it, and requiring coverage
        # stops the rest. His own holdings are exempt - he wants those either way.
        out = [r for r in out if r["analysts"] or r["held"]]
        # What he owns is said first: it is his money before it is a stock tip.
        out.sort(key=lambda r: (not r["held"], -(r["analysts"] or 0)))
        return out

    async def compose(self, limit: int = MAX_NAMES, detail: int = 3) -> str:
        """The spoken version. Names first, then a stance on the few that matter.

        Eight full stances is a lecture, not a briefing. He hears every name the
        press is on, and reasons for the handful he can act on.
        """
        rows = await self.take(limit)
        if not rows:
            return "I couldn't get a read on what the market is talking about."
        names = ", ".join(r["name"] for r in rows)
        parts = [f"The names in the conversation today: {names}."]
        parts += [r["line"] for r in rows[:detail]]
        parts.append("That is what analysts are saying, not advice.")
        return " ".join(parts)


analyst = Analyst()
