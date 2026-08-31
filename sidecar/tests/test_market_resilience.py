"""What the market tools do when the market data service is having a bad day.

On 2026-08-31 Finnhub threw 245 `503 Service Unavailable` responses. His 07:30
brief came out reading "MARKETS: the Nasdaq 100 -0.65%" and "YOURS: Apple
+1.63%" - one index and one holding - with no hint that four of his five stocks
had simply failed. Read plainly, that says he owns one stock.

Fixing it took three goes, and the two wrong ones are the reason this file
exists:

  1. retry EVERY failure three times. A timeout had already spent 12 seconds, so
     three of them blew through the tool's 25s budget: get_watchlist timed out.
  2. retry only the cheap failure (a 503 is refused in 0.10s). Better, but during
     a real outage retries TRIPLE the call volume, the 55-per-minute budget
     empties, and the limiter then holds every later call for up to a minute.
     Still 25 seconds, still a timeout.
  3. retry the hiccup, and stop retrying once failures dominate.

Offline: no network. The HTTP layer is stubbed, because the behaviour under test
is what we do when someone else's service is broken.

Run: python tests/test_market_resilience.py
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


class Reply:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def fake_client(script, calls):
    """An httpx.AsyncClient stand-in that replays `script` and counts calls."""
    class C:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            calls.append(url)
            item = script[min(len(calls) - 1, len(script) - 1)]
            if isinstance(item, Exception):
                raise item
            return item
    return lambda **kw: C()


def main() -> int:
    import tools.market_tools as mt
    mt.secrets["finnhub_api_key"] = "test-key"   # a plain dict, not an object
    real_client = mt.httpx.AsyncClient
    real_quote = mt.get_stock_quote
    real_cfg = mt.config.get

    def reset():
        mt._recent.clear()
        mt._breaker_until = 0.0
        mt._calls.clear()

    # --- a hiccup is retried, and recovered ----------------------------------
    reset()
    calls = []
    mt.httpx.AsyncClient = fake_client(
        [Reply(503), Reply(200, {"c": 178.2, "dp": 1.6})], calls)
    got = asyncio.run(mt._get("/quote", symbol="AAPL"))
    check("a single 503 is retried", len(calls) == 2, len(calls))
    check("...and the recovered value is returned", got.get("c") == 178.2, got)

    # --- an expensive failure is NOT retried ---------------------------------
    # A timeout has already spent its full budget. Three of those is a timeout of
    # the whole tool, which is worse than the missing number it was chasing.
    reset()
    calls = []
    mt.httpx.AsyncClient = fake_client([TimeoutError("read timeout")], calls)
    t0 = time.time()
    got = asyncio.run(mt._get("/quote", symbol="AAPL"))
    check("a timeout is not retried", len(calls) == 1, len(calls))
    check("...and it gives up promptly", time.time() - t0 < 2.0)
    check("...reporting the failure", "didn't answer" in str(got.get("_error")), got)

    # --- and once the service is plainly down, retrying stops ----------------
    reset()
    calls = []
    mt.httpx.AsyncClient = fake_client([Reply(503)], calls)
    for _ in range(4):                       # enough failures to trip it
        asyncio.run(mt._get("/quote", symbol="AAPL"))
    check("the breaker opens after sustained failure", not mt._retries_allowed())

    before = len(calls)
    asyncio.run(mt._get("/quote", symbol="MSFT"))
    check("...so later calls take exactly one shot", len(calls) - before == 1,
          len(calls) - before)
    check("...and say the service is down",
          "down" in str(asyncio.run(mt._get("/quote", symbol="X")).get("_error")))

    # a success reopens the door
    reset()
    calls = []
    mt.httpx.AsyncClient = fake_client([Reply(200, {"c": 1.0})], calls)
    asyncio.run(mt._get("/quote", symbol="AAPL"))
    check("a healthy service keeps its retries", mt._retries_allowed())

    # --- the whole point: he is TOLD what is missing -------------------------
    reset()
    mt.httpx.AsyncClient = real_client

    async def only_apple(symbol):
        if str(symbol).upper() == "AAPL":
            return {"symbol": "AAPL", "name": "Apple Inc", "price": 232.1,
                    "percent": 1.63}
        return {"error": "the market data service didn't answer"}

    mt.get_stock_quote = only_apple
    mt.config.get = lambda *a, **k: (["NVDA", "AMC", "TSLA", "AAPL", "SPCX"]
                                     if a[:2] == ("markets", "watchlist")
                                     else k.get("default"))
    res = asyncio.run(mt.get_watchlist())
    check("what came back is reported", len(res.get("stocks") or []) == 1, res)
    check("...and what did NOT is named, rather than silently dropped",
          set(res.get("missing") or []) == {"NVDA", "AMC", "TSLA", "SPCX"},
          res.get("missing"))

    # --- he should not need a webservice to know what Tesla is ---------------
    # Finnhub's /search went down with everything else, so "Tesla" came back
    # "I couldn't find a listed company called Tesla" and AMD was reported as a
    # company named "AMD". These resolve with no network at all.
    reset()
    mt.get_stock_quote = real_quote
    mt.httpx.AsyncClient = fake_client([Reply(503)], [])   # search is dead
    for asked, ticker, label in (("Tesla", "TSLA", "Tesla"),
                                 ("tesla", "TSLA", "Tesla"),
                                 ("AMD", "AMD", "Advanced Micro Devices"),
                                 ("goldman sachs", "GS", "Goldman Sachs"),
                                 ("NVDA", "NVDA", "Nvidia")):
        got = asyncio.run(mt._resolve_symbol(asked))
        check(f"{asked!r} resolves with the search down", got == (ticker, label), got)
    check("an unknown name still asks the service",
          asyncio.run(mt._resolve_symbol("some obscure thing")) is None)

    # leave the module as we found it
    mt.get_stock_quote, mt.config.get = real_quote, real_cfg
    mt.secrets.pop("finnhub_api_key", None)

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
