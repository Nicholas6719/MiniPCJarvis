"""Market intelligence: the story, the experts, what is ahead - offline.

His instruction, 2026-09-05: "he mentions stocks now but I need more
intelligent info". Everything live (feeds, Finnhub, the model) is faked here;
what is gated is the judgement: which headlines are the market's story, which
are an expert speaking, how a company is described, and that the brief gets
the sections in a shape he can read.

Run: python tests/test_market_intel.py
"""
import asyncio
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


def story(headline, source="CNBC", age=60, summary=""):
    return {"headline": headline, "source": source, "age_minutes": age, "summary": summary}


def main() -> int:
    import market_intel as mi

    print("\n-- what is the market's story, and what is not --")
    check("a Wall Street headline is the story",
          mi.relevant(story("Stocks rally as Fed signals a rate cut")))
    check("a Treasury yields headline is the story",
          mi.relevant(story("Treasury yields jump after hot inflation print")))
    check("a product launch is not", not mi.relevant(story("Apple unveils a thinner iPhone")))
    check("a sportsbook story is not, whatever it says about the market",
          not mi.relevant(story("NFL preview: sportsbooks take on the prediction market")))
    check("yesterday's story is not today's",
          not mi.relevant(story("Stocks rally as Fed signals a rate cut", age=30 * 60)))
    check("an undated headline is given the benefit of the doubt",
          mi.relevant({"headline": "Dow falls 400 points", "source": "WSJ"}))

    print("\n-- an expert speaking --")
    check("a strategist's call is an expert", mi.expert(story("Goldman sees S&P 500 at 7,000 by year end")))
    check("an analyst upgrade is an expert", mi.expert(story("Analysts upgrade Nvidia ahead of earnings")))
    check("a price report is not", not mi.expert(story("Dow falls 400 points at the open")))

    print("\n-- one story, two desks --")
    check("the same event in different words is one story",
          mi.same_story("Wall Street ends sharply higher as Waller remarks ease rate hike fears",
                        "Stocks close sharply higher after Fed's Waller eases rate-hike fears"))
    check("two different events are two",
          not mi.same_story("Stocks rally as Fed signals a rate cut",
                            "Oil jumps after tanker attack near Kharg Island"))
    d = mi.dedupe([story("Stocks rally as Fed signals a rate cut", "CNBC"),
                   story("Wall Street rallies after Fed signals rate cut", "WSJ"),
                   story("Oil jumps after tanker attack", "Reuters")])
    check("dedupe keeps the first desk and the other story", len(d) == 2 and d[0]["source"] == "CNBC", d)

    print("\n-- the numbers as a sentence --")
    line = mi.gauges_line([{"name": "the S&P 500", "percent": 0.8}, {"name": "the Nasdaq 100", "percent": -0.3}],
                          {"name": "small caps", "percent": 1.2}, {"name": "volatility", "percent": -6.0},
                          {"isOpen": False, "holiday": None})
    check("indexes, small caps, volatility and the state of play",
          line == "the S&P 500 up 0.8 percent, the Nasdaq 100 down 0.3 percent, small caps up 1.2 percent; "
                  "volatility is down 6 percent. The market is closed", line)
    check("a quiet volatility day is not mentioned",
          "volatility" not in mi.gauges_line([{"name": "the Dow", "percent": 0.1}], None,
                                             {"name": "volatility", "percent": 1.0}, None))
    check("a holiday is named", "closed for Labor Day" in mi.gauges_line(
        [{"name": "the Dow", "percent": 0.0}], None, None, {"isOpen": False, "holiday": "Labor Day"}))
    check("flat is flat", mi.move_words(0.02) == "flat")

    print("\n-- what is ahead --")
    today = dt.date(2026, 9, 7)      # a Monday
    rows = [{"symbol": "AAPL", "name": "Apple", "date": "2026-09-10", "hour": "amc", "held": True},
            {"symbol": "NVDA", "name": "Nvidia", "date": "2026-09-09", "hour": "bmo", "held": False},
            {"symbol": "XYZ", "name": "XYZ", "date": "2026-09-08", "hour": "", "held": False}]
    line = mi.earnings_line(rows, today)
    check("earnings ahead reads as a sentence",
          line == "Earnings ahead: Apple on Thursday after the close, which you own; "
                  "Nvidia on Wednesday before the open; XYZ tomorrow", line)
    check("nothing ahead is silence", mi.earnings_line([], today) == "")

    print("\n-- one company, as a person would put it --")
    ctx = {"symbol": "NVDA", "name": "Nvidia", "price": 182.5, "percent": 2.1,
           "high_52w": 190.0, "low_52w": 90.0, "ytd_percent": 35.2, "pe": 48.3, "beta": 1.9,
           "last_surprise_percent": 4.2, "consensus": "buy", "buy": 50, "hold": 6, "sell": 1,
           "analysts": 57, "insider_mspr": -35.0, "next_earnings": "2026-11-19"}
    line = mi.context_line(ctx)
    for want in ("Nvidia is at 182.5 dollars, up 2.1 percent today", "near its 52-week high",
                 "Up 35.2 percent this year", "48 times earnings", "volatile name",
                 "beat estimates by 4.2 percent", "50 of 57 analysts say buy",
                 "insiders have been selling", "reports next on November 19", "not advice"):
        check(f"says: {want!r}", want.lower() in line.lower(), line)
    low = mi.context_line({"symbol": "F", "name": "Ford", "price": 9.0, "percent": -0.5,
                           "high_52w": 15.0, "low_52w": 8.5, "beta": 0.6, "consensus": "hold",
                           "buy": 4, "hold": 12, "sell": 2, "analysts": 18, "last_surprise_percent": 0.1})
    check("near the low is said", "near its 52-week low" in low.lower(), low)
    check("a steady name is said", "steadier than the market" in low, low)
    check("split analysts are said", "analysts are split, 4 buy and 12 hold" in low.lower(), low)
    check("landing on estimates is said", "landed on estimates" in low, low)
    check("no data is honest", "couldn't get a read" in mi.context_line({"symbol": "Q", "name": "Q"}))

    print("\n-- sentences, with abbreviations intact --")
    # the first live night brief read "According to MarketWatch, the U.S." and stopped
    t = mi.tidy_story("According to MarketWatch, the U.S. stock market is mixed today, with energy "
                      "shares falling. CNBC reports strategists expect a quiet week. A third sentence.")
    check("'the U.S.' is not the end of a sentence",
          t == "According to MarketWatch, the U.S. stock market is mixed today, with energy shares "
               "falling. CNBC reports strategists expect a quiet week.", t)
    t = mi.tidy_story("Stocks rose on Friday, according to the Journal. Energy shares fell amid job and")
    check("a fragment the model never finished is dropped", t == "Stocks rose on Friday, according to the Journal.", t)
    check("Inc. and Mr. do not split either",
          mi.split_sentences("Apple Inc. rose. Mr. Cook spoke.") == ["Apple Inc. rose.", "Mr. Cook spoke."],
          mi.split_sentences("Apple Inc. rose. Mr. Cook spoke."))

    print("\n-- the story, from the desks, through the model --")
    calls = []

    class _Chunk:
        def __init__(self, text, done):
            self.text, self.done = text, done

    class _LLM:
        async def stream(self, messages, **kw):
            calls.append(messages[0]["content"])
            yield _Chunk("Two sentences: Stocks rose as the Fed signalled a cut, according to the "
                         "Journal. CNBC reports strategists expect more.", True)

    import llm.provider as prov
    real = prov.local_llm
    prov.local_llm = _LLM()
    m = mi.MarketIntel()

    async def fake_heads():
        return [story("Stocks rally as Fed signals a rate cut", "the Wall Street Journal", 40),
                story("Goldman sees S&P 500 at 7,000 by year end", "CNBC", 90),
                story("Treasury yields slip", "MarketWatch", 120)]
    m.headlines = fake_heads
    try:
        s = asyncio.run(m.story())
        check("the model is shown the headlines with their desks",
              calls and "(the Wall Street Journal)" in calls[0] and "Goldman" in calls[0], calls[:1])
        check("the story is two attributed sentences, scaffolding removed",
              s["text"].startswith("Stocks rose as the Fed") and "Journal" in s["text"]
              and not s["text"].startswith("Two sentences"), s["text"])
        check("the sources are listed", s["sources"] == ["CNBC", "MarketWatch", "the Wall Street Journal"], s["sources"])
        ex = asyncio.run(m.experts())
        check("the experts are the expert headlines", [e["headline"][:7] for e in ex] == ["Goldman"], ex)
        secs = asyncio.run(m.brief_sections())
        titles = [t for t, _ in secs]
        check("the brief gets the story and the experts", titles[:2] == ["The story", "Experts"], titles)
        written = secs[0][1][0][1]
        check("...and the written story names its desks", "(CNBC, MarketWatch, the Wall Street Journal)" in written, written)
        check("a second ask is answered from cache, not the model", (asyncio.run(m.story()), len(calls))[1] == 1, len(calls))
    finally:
        prov.local_llm = real

    print("\n-- the tools are registered --")
    from tools.registry import registry
    mi.register_all()
    for name in ("get_market_state", "get_earnings_ahead", "get_stock_context"):
        check(f"{name} is a tool", registry.get(name) is not None if hasattr(registry, "get") else name in registry.names())

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
