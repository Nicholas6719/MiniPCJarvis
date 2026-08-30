"""The market tools, against Finnhub, on the running app.

Checking that a call "came back" proves almost nothing — a field mapped to the
wrong key still comes back. So this checks the numbers against THEMSELVES:
price minus previous close must equal the change it reports, and the percentage
must match that change. A quote whose fields have been shuffled fails that even
though every value in it is real.

Modest by design: markets data is rate-limited, and a gate that hammers the API
is a gate that gets the key throttled.

Run: python tests/market_e2e.py PORT TOKEN
"""
import asyncio
import json
import sys
import time

import httpx
import websockets

PORT = sys.argv[1] if len(sys.argv) > 1 else "8790"
TOKEN = sys.argv[2] if len(sys.argv) > 2 else "devtoken123"
BASE, H = f"http://127.0.0.1:{PORT}", {"X-Jarvis-Token": TOKEN}

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


async def tool(c, _tool, **args) -> dict:
    """Run one tool; a refusal comes back INSIDE the result, not as a failed call."""
    r = (await c.post(f"{BASE}/debug/tool", headers=H,
                      json={"tool": _tool, "args": args})).json()
    res = r.get("result") if isinstance(r.get("result"), dict) else {}
    return {"_ok": bool(r.get("ok")) and "error" not in res,
            "_error": r.get("error") or res.get("error"), **res}


async def main() -> int:
    async with httpx.AsyncClient(timeout=120) as c:
        q = await tool(c, "get_stock_quote", symbol="AAPL")
        if not q["_ok"] and "api key" in str(q["_error"]).lower():
            print(f"  SKIPPED - no Finnhub key configured.\n  ({q['_error']})\n\n"
                  "MARKET E2E: SKIPPED")
            return 0

        # --- a quote, checked against itself ---------------------------------
        check("a quote comes back", q["_ok"], q["_error"])
        check("the company is NAMED, not just its ticker",
              isinstance(q.get("name"), str) and q.get("name", "").upper() != "AAPL",
              q.get("name"))
        price, prev = q.get("price"), q.get("previous_close")
        check("the price is a real number", isinstance(price, (int, float)) and price > 0,
              price)
        ok_nums = all(isinstance(v, (int, float)) for v in
                      (price, prev, q.get("change"), q.get("percent")))
        check("every number in it is a number", ok_nums, q)
        if ok_nums and prev:
            check("the change it reports IS price minus previous close",
                  abs((price - prev) - q["change"]) < 0.02,
                  f"{price} - {prev} = {round(price - prev, 2)}, it says {q['change']}")
            check("and the percentage matches that change",
                  abs(q["change"] / prev * 100 - q["percent"]) < 0.05,
                  f"{round(q['change'] / prev * 100, 2)} vs {q['percent']}")
            check("the day's high is not below its low",
                  q.get("high", 0) >= q.get("low", 0), (q.get("high"), q.get("low")))

        # --- analysts --------------------------------------------------------
        a = await tool(c, "get_analyst_view", symbol="NVDA")
        check("analyst ratings come back", a["_ok"], a["_error"])
        if a["_ok"]:
            parts = [a.get(k) for k in ("buy", "hold", "sell")]
            check("the ratings are counts", all(isinstance(v, int) for v in parts), parts)
            check("they do not exceed the number of analysts",
                  isinstance(a.get("analysts"), int) and sum(
                      v for v in parts if isinstance(v, int)) <= a["analysts"],
                  f"{parts} of {a.get('analysts')}")
            check("the consensus is one of buy, hold or sell",
                  a.get("consensus") in ("buy", "hold", "sell"), a.get("consensus"))

        # --- company news ----------------------------------------------------
        n = await tool(c, "get_company_news", symbol="TSLA", count=3)
        check("company news comes back", n["_ok"], n["_error"])
        if n["_ok"]:
            items = n.get("items") or []
            check("with headlines and who ran them",
                  items and all(i.get("headline") and i.get("source") for i in items),
                  items[:2])
            check("and no more than asked for", len(items) <= 3, len(items))

        # --- the market as a whole -------------------------------------------
        m = await tool(c, "get_market_movers")
        check("the market-wide check comes back", m["_ok"], m["_error"])
        if m["_ok"]:
            syms = {x.get("symbol") for x in (m.get("markets") or [])}
            check("it covers the S&P, the Nasdaq and the Dow",
                  {"SPY", "QQQ", "DIA"} <= syms, syms)
            check("each one has a price",
                  all(isinstance(x.get("price"), (int, float)) and x["price"] > 0
                      for x in m["markets"]), m.get("markets"))
            check("each one is NAMED as he would say it",
                  all(x.get("name") and x["name"] != x.get("symbol")
                      for x in m["markets"]),
                  [x.get("name") for x in m.get("markets", [])])

        # --- a company that does not exist is refused, not invented -----------
        bad = await tool(c, "get_stock_quote", symbol="Zzyzxq Holdings")
        check("a company that does not exist is refused, not invented",
              not bad["_ok"] and bad.get("price") is None, bad)

        # --- his own names, and the limiter that serves them ------------------
        w = await tool(c, "get_watchlist")
        check("his own stocks come back as a list", w["_ok"], w["_error"])
        if w["_ok"]:
            rows = w.get("stocks") or []
            check("with more than one name in it", len(rows) >= 2, len(rows))
            check("biggest mover first — that is the one he wants",
                  all(abs(rows[i].get("percent") or 0) >= abs(rows[i + 1].get("percent") or 0)
                      for i in range(len(rows) - 1)),
                  [(r.get("symbol"), r.get("percent")) for r in rows])

        # --- and it survives being asked twice in a row (rate limiting) -------
        t0 = time.time()
        two = await asyncio.gather(tool(c, "get_stock_quote", symbol="MSFT"),
                                   tool(c, "get_stock_quote", symbol="AMZN"))
        check("two at once are both answered, not throttled into an error",
              all(x["_ok"] for x in two), [x.get("_error") for x in two])
        # they may now go together — the limiter bursts and only waits when the
        # last minute is genuinely full, because serialising cost five seconds
        # on a five-stock answer
        check("...and quickly, not one per second",
              time.time() - t0 < 4.0, f"{round(time.time() - t0, 2)}s")

    # --- and the whole way through, by asking him ----------------------------
    ev: list = []

    async def listen():
        async with websockets.connect(f"ws://127.0.0.1:{PORT}/ws?token={TOKEN}",
                                      max_size=None) as ws:
            async for m in ws:
                ev.append(json.loads(m))

    lt = asyncio.create_task(listen())
    await asyncio.sleep(1)
    async with httpx.AsyncClient(timeout=120) as c:
        t0 = time.time()
        await c.post(f"{BASE}/text", headers=H, json={"text": "what's apple trading at"})
        reply, tools_used = "", []
        while time.time() - t0 < 90:
            for e in ev:
                if e.get("kind") == "tool_call" and e.get("status") == "pending":
                    tools_used.append(e.get("tool"))
                elif e.get("kind") == "turn_done":
                    reply = e.get("text") or ""
            if reply:
                break
            await asyncio.sleep(0.1)
    lt.cancel()
    check("asking him routes to the quote tool", "get_stock_quote" in tools_used,
          tools_used)
    check("and he says a price out loud",
          "dollar" in reply.lower() and any(ch.isdigit() for ch in reply), reply[:120])

    print(f"\nMARKET E2E: {'PASS' if not fails else f'FAIL ({len(fails)})'}")
    return 0 if not fails else 1


sys.exit(asyncio.run(main()))
