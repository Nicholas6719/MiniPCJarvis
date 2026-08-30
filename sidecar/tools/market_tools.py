"""Markets: quotes, analyst opinion and company news, via Finnhub.

Everything here is REALM 2 by definition (docs/BRAIN_ROADMAP.md): a price is true
for seconds. Nothing this module returns may ever be cached in the fact store or
answered from the model's memory — the fact store's REALM2 pattern already
matches "price", "worth", "current" and friends, and these answers are spoken
with the time they were taken so a stale number can't masquerade as a live one.

The API key lives in Windows Credential Manager (secrets["finnhub_api_key"]),
injected by the Rust core at startup exactly like the Brave key. It is never in
config.json, never on disk here, never in git. Without it every tool returns a
plain instruction instead of failing obscurely.

Free tier, as of 2026-08: 60 calls/min, real-time US quotes, company news, and
recommendation trends. Rate limiting is handled here rather than discovered.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time

import httpx

from config import config, secrets
from tools.registry import Risk, Tool, registry

log = logging.getLogger("jarvis.market")

BASE = "https://finnhub.io/api/v1"
NO_KEY = "I need a Finnhub API key for market data — add it in Settings, under Markets."

# 60 calls/minute on the free tier. The first version spaced every call 1.1 s
# apart, which is under the limit but also serialises everything: five stocks
# took five and a half seconds before he said a word, and a market brief wants
# thirty calls. A sliding window is the honest reading of "60 per minute" — burst
# freely, and only wait when the last minute is actually full.
_LIMIT_PER_MIN = 55          # a little under 60, for clock skew and retries
_calls: list[float] = []     # timestamps of recent calls, newest last
_lock = asyncio.Lock()


async def _rate_limit() -> None:
    while True:
        async with _lock:
            now = time.time()
            _calls[:] = [t for t in _calls if now - t < 60.0]
            if len(_calls) < _LIMIT_PER_MIN:
                _calls.append(now)
                return
            wait = 60.0 - (now - _calls[0]) + 0.05
        log.info("finnhub minute is full - holding %.1fs", wait)
        await asyncio.sleep(max(0.05, wait))


async def _get(path: str, **params) -> dict | list | None:
    key = secrets.get("finnhub_api_key")
    if not key:
        return None
    await _rate_limit()
    try:
        async with httpx.AsyncClient(timeout=12) as c:
            r = await c.get(f"{BASE}{path}", params={**params, "token": key})
            if r.status_code == 429:
                return {"_error": "Finnhub is rate-limiting us; try again in a moment."}
            if r.status_code == 401:
                return {"_error": "Finnhub rejected the API key — it may need replacing in Settings."}
            r.raise_for_status()
            return r.json()
    except Exception as e:
        log.warning("finnhub %s failed: %s", path, e)
        return {"_error": f"the market data service didn't answer ({type(e).__name__})"}


def _err(data) -> str | None:
    return data.get("_error") if isinstance(data, dict) else None


# A company's name for its ticker does not change between one question and the
# next, so look it up once. Without this a bare ticker had no name to give and
# he read the letters out: "A A P L is at 319 dollars".
_NAMES: dict[str, tuple[str, str]] = {}


def _looks_like_ticker(q: str) -> bool:
    return q.isupper() and 1 <= len(q) <= 5 and q.isalpha()


async def _resolve_symbol(name: str) -> tuple[str, str] | None:
    """'apple' -> ('AAPL', 'Apple Inc'), and 'AAPL' -> ('AAPL', 'Apple Inc') too."""
    q = (name or "").strip()
    if not q:
        return None
    hit = _NAMES.get(q.lower())
    if hit:
        return hit
    data = await _get("/search", q=q)
    if not isinstance(data, dict) or _err(data):
        # the search failing must not lose him a quote he could still have
        return (q, q) if _looks_like_ticker(q) else None
    rows = data.get("result") or []
    # asked for a ticker, the ticker wins — searching "AMD" also returns
    # companies with AMD in their name, and the first of those is not it
    pick = next((r for r in rows if (r.get("symbol") or "").upper() == q.upper()), None)
    if pick is None:
        for row in rows:
            # common stock on a US exchange: skip warrants, ADR duplicates, foreign lines
            if row.get("type") in ("Common Stock", "ADR", "ETP", "") \
                    and "." not in (row.get("symbol") or "."):
                pick = row
                break
    if pick is None:
        return (q, q) if _looks_like_ticker(q) else None
    label = str(pick.get("description") or pick["symbol"]).strip()
    if label.isupper():                 # "APPLE INC" is shouting; he is not
        label = label.title()
    found = (pick["symbol"], label)
    _NAMES[q.lower()] = found
    _NAMES[pick["symbol"].lower()] = found
    return found


async def get_stock_quote(symbol: str) -> dict:
    """Live price for one company or ticker."""
    hit = await _resolve_symbol(symbol)
    if hit is None:
        return {"error": NO_KEY if not secrets.get("finnhub_api_key")
                else f"I couldn't find a listed company called {symbol}."}
    tick, name = hit
    data = await _get("/quote", symbol=tick)
    if data is None:
        return {"error": NO_KEY}
    if _err(data):
        return {"error": _err(data)}
    if not data.get("c"):
        return {"error": f"no live price came back for {tick}."}
    return {
        "symbol": tick, "name": name,
        "price": round(float(data["c"]), 2),
        "change": round(float(data.get("d") or 0), 2),
        "percent": round(float(data.get("dp") or 0), 2),
        "high": round(float(data.get("h") or 0), 2),
        "low": round(float(data.get("l") or 0), 2),
        "open": round(float(data.get("o") or 0), 2),
        "previous_close": round(float(data.get("pc") or 0), 2),
        "as_of": dt.datetime.now().strftime("%H:%M"),
    }


async def get_analyst_view(symbol: str) -> dict:
    """What analysts currently recommend, and where they think the price is going."""
    hit = await _resolve_symbol(symbol)
    if hit is None:
        return {"error": NO_KEY if not secrets.get("finnhub_api_key")
                else f"I couldn't find a listed company called {symbol}."}
    tick, name = hit
    recs, target = await asyncio.gather(
        _get("/stock/recommendation", symbol=tick),
        _get("/stock/price-target", symbol=tick))
    if recs is None:
        return {"error": NO_KEY}
    if _err(recs):
        return {"error": _err(recs)}
    if not isinstance(recs, list) or not recs:
        return {"error": f"no analyst coverage came back for {tick}."}
    latest = recs[0]
    buy = int(latest.get("strongBuy", 0)) + int(latest.get("buy", 0))
    hold = int(latest.get("hold", 0))
    sell = int(latest.get("sell", 0)) + int(latest.get("strongSell", 0))
    total = buy + hold + sell
    out = {
        "symbol": tick, "name": name, "period": latest.get("period"),
        "buy": buy, "hold": hold, "sell": sell, "analysts": total,
        "consensus": ("buy" if buy > hold + sell else
                      "sell" if sell > buy + hold else "hold"),
    }
    if isinstance(target, dict) and not _err(target) and target.get("targetMean"):
        out["target_mean"] = round(float(target["targetMean"]), 2)
        out["target_high"] = round(float(target.get("targetHigh") or 0), 2)
        out["target_low"] = round(float(target.get("targetLow") or 0), 2)
    return out


async def get_company_news(symbol: str, days: int = 5, count: int = 5) -> dict:
    """Recent headlines about one company."""
    hit = await _resolve_symbol(symbol)
    if hit is None:
        return {"error": NO_KEY if not secrets.get("finnhub_api_key")
                else f"I couldn't find a listed company called {symbol}."}
    tick, name = hit
    today = dt.date.today()
    data = await _get("/company-news", symbol=tick,
                      **{"from": str(today - dt.timedelta(days=max(1, min(30, days)))),
                         "to": str(today)})
    if data is None:
        return {"error": NO_KEY}
    if _err(data):
        return {"error": _err(data)}
    items = [{"headline": a.get("headline"), "source": a.get("source"),
              "url": a.get("url"),
              "when": dt.datetime.fromtimestamp(a["datetime"]).strftime("%b %d")
              if a.get("datetime") else ""}
             for a in (data or [])[:max(1, min(10, count))]]
    return {"symbol": tick, "name": name, "count": len(items), "items": items}


async def get_watchlist() -> dict:
    """How HIS names are doing — the ones he holds or follows.

    Separate from get_market_movers, which is the market as a whole. This is the
    list in config markets.watchlist, and it is the same list the proactive brief
    uses to decide a move is worth interrupting him for.
    """
    names = config.get("markets", "watchlist", default=[]) or []
    if not names:
        return {"error": "You haven't given me a list of stocks to follow yet, sir."}
    out = []
    for sym in names[:12]:
        q = await get_stock_quote(sym)
        if not q.get("error"):
            out.append(q)
    if not out:
        return {"error": NO_KEY if not secrets.get("finnhub_api_key")
                else "None of your stocks came back just now."}
    out.sort(key=lambda q: abs(q.get("percent") or 0), reverse=True)
    return {"count": len(out), "stocks": out}


async def get_market_movers() -> dict:
    """How the market itself is doing, via the big index ETFs (no index feed on
    the free tier — SPY/QQQ/DIA track them closely enough to speak about)."""
    if not secrets.get("finnhub_api_key"):
        return {"error": NO_KEY}
    names = {"SPY": "the S&P 500", "QQQ": "the Nasdaq 100", "DIA": "the Dow"}
    quotes = await asyncio.gather(*(_get("/quote", symbol=s) for s in names))
    out = []
    for sym, q in zip(names, quotes):
        if isinstance(q, dict) and not _err(q) and q.get("c"):
            out.append({"symbol": sym, "name": names[sym],
                        "price": round(float(q["c"]), 2),
                        "percent": round(float(q.get("dp") or 0), 2)})
    if not out:
        return {"error": "no market data came back."}
    return {"markets": out, "as_of": dt.datetime.now().strftime("%H:%M")}


def register_all() -> None:
    registry.register(Tool(
        name="get_stock_quote",
        description="Live share price for a company or ticker, with the day's change. "
                    "Use for 'what's Apple trading at', 'how is NVDA doing'.",
        parameters={"type": "object", "properties": {
            "symbol": {"type": "string", "description": "ticker or company name"}},
            "required": ["symbol"]},
        risk=Risk.SAFE, handler=get_stock_quote, timeout=25))
    registry.register(Tool(
        name="get_analyst_view",
        description="Current analyst recommendations (buy/hold/sell counts) and mean "
                    "price target for a company. Use for 'what do analysts say about X', "
                    "'is X a buy'.",
        parameters={"type": "object", "properties": {
            "symbol": {"type": "string", "description": "ticker or company name"}},
            "required": ["symbol"]},
        risk=Risk.SAFE, handler=get_analyst_view, timeout=25))
    registry.register(Tool(
        name="get_company_news",
        description="Recent news headlines about one company.",
        parameters={"type": "object", "properties": {
            "symbol": {"type": "string"},
            "days": {"type": "integer", "minimum": 1, "maximum": 30},
            "count": {"type": "integer", "minimum": 1, "maximum": 10}},
            "required": ["symbol"]},
        risk=Risk.SAFE, handler=get_company_news, timeout=25))
    registry.register(Tool(
        name="get_watchlist",
        description="How the user's OWN stocks are doing - the list he follows. Use for "
                    "'how are my stocks doing', 'how's my portfolio', 'check my stocks'. "
                    "For a single named company use get_stock_quote; for the market as a "
                    "whole use get_market_movers.",
        parameters={"type": "object", "properties": {}, "required": []},
        risk=Risk.SAFE, handler=get_watchlist, timeout=45))
    registry.register(Tool(
        name="get_market_movers",
        description="How the overall US market is doing right now (S&P 500, Nasdaq, Dow).",
        parameters={"type": "object", "properties": {}, "required": []},
        risk=Risk.SAFE, handler=get_market_movers, timeout=25))
