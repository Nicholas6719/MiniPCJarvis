"""Whether the market take is judgement or a data dump.

He was explicit that a list of prices is worthless to him, and gave the shape of
what he wanted in one sentence: *"Nvidia is up right now, so you shouldn't buy
in."* A view, plus timing. Both halves are checked below.

The headlines here are REAL - pulled from the live feed while building this,
including the ones that broke it. Two failures they exposed:

  * "US Stock Market Today: S&P 500 Futures Slip As Traders Watch PCE And Fed"
    nominated PCE, FED and US as stocks to talk about.
  * "Stocks to watch: Wipro, Hero MotoCorp, HDFC Bank, Tata Motors PV" is a real
    result from a US financial query. He asked about the US market.

Offline: no network, no key needed.
Run: python tests/test_analyst.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from analyst import candidates, speakable, stance  # noqa: E402

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


def heads(*hs):
    return [{"headline": h} for h in hs]


def main() -> int:
    # --- reading names out of real headlines ---------------------------------
    got = dict(candidates(heads(
        "Nvidia (NVDA) Earns Price-Target Upgrades After Monster Q2",
        "Semtech (NASDAQ:SMTC) Shares Gap Up on Analyst Upgrade",
        "BLZE Stock On Track For Its Best Day Since March 2024",
        "Rate Concerns Take Their Toll; Amazon, Palantir, Nvidia In Focus")))
    check("a tagged ticker is found", got.get("NVDA") == 2, got)
    check("an exchange-qualified ticker is found", "SMTC" in got, got)
    check("a bare shouted ticker is found", "BLZE" in got, got)
    check("a company named without its ticker is found",
          got.get("AMZN") == 1 and got.get("PLTR") == 1, got)

    # --- and NOT reading names out of things that are not names --------------
    junk = dict(candidates(heads(
        "US Stock Market Today: S&P 500 Futures Slip As Traders Watch PCE And Fed",
        "The big lesson from this week's earnings: The AI buildout is not zero-sum",
        "Why The FDA And SEC Both Weighed In Today")))
    for bad in ("PCE", "FED", "US", "AI", "FDA", "SEC", "SP"):
        check(f"{bad} is not a stock tip", bad not in junk, junk)

    # --- one article shouting a name is still one opinion ---------------------
    once = dict(candidates(heads(
        "Nvidia (NVDA) is the NVDA story of the year, and NVDA knows it")))
    check("a name repeated in one article counts once", once.get("NVDA") == 1, once)
    check("...and two articles count twice",
          dict(candidates(heads("Nvidia (NVDA) climbs", "NVDA Stock extends gains"))
               ).get("NVDA") == 2)

    # --- the two-letter words the first live run mistook for stocks ----------
    # "Legrand stock rallies AS analyst upgrade" nominated Amer Sports (AS), and
    # a headline ending "...ahead" nominated Array Digital (AD). Both ARE real US
    # tickers, so confirming the listing did not catch either one.
    live = dict(candidates(heads(
        "Legrand stock rallies as analyst upgrade highlights data center growth",
        "SolarEdge Stock Rallies Following UBS Upgrade to Buy",
        "Here are the 3 big things we're watching in the stock market week ahead")))
    for bad in ("AS", "AD", "UBS", "TO"):
        check(f"{bad} is not nominated as a stock", bad not in live, live)

    # --- the foreign name that a US-listing check could not catch -------------
    # BDL is Bharat Dynamics in Bombay and Flanigan's Enterprises - a Florida
    # restaurant chain - here. Confirming the US listing passed it for the wrong
    # reason, and he was told experts were discussing a restaurant chain.
    indian = "Top stock picks: Analysts recommend buying Kotak Mahindra Bank, " \
             "AU SFB, BDL, Kaynes Tech and more"
    check("a bare name in a foreign list is not a stock tip",
          "BDL" not in dict(candidates(heads(indian))),
          dict(candidates(heads(indian))))
    check("...but two newsrooms shouting it means somebody means it",
          dict(candidates(heads(indian, "BDL climbs for a third day"))).get("BDL") == 2)
    check("a ticker beside the word Stock is still trusted at one mention",
          dict(candidates(heads("BLZE Stock On Track For Its Best Day"))).get("BLZE") == 1)
    check("...and so is one written as 'shares of'",
          "QUBT" in dict(candidates(heads("Investors pile into shares of QUBT"))))

    # --- names a person would actually say ------------------------------------
    check("a filing name is spoken like a person says it",
          speakable("Amazon.Com Inc") == "Amazon", speakable("Amazon.Com Inc"))
    check("a trailing Corp goes", speakable("Nvidia Corp") == "Nvidia")
    check("a leading The goes",
          speakable("The Walt Disney Company") == "Walt Disney",
          speakable("The Walt Disney Company"))
    check("a name that is already plain is left alone",
          speakable("Advanced Micro Devices") == "Advanced Micro Devices")
    check("a name that is ONLY a suffix is not erased to nothing",
          speakable("Inc") == "Inc", speakable("Inc"))
    check("a share class is not read out loud",
          speakable("Backblaze Inc-A") == "Backblaze", speakable("Backblaze Inc-A"))
    check("...nor the wordy kind", speakable("Alphabet Inc Cl C") == "Alphabet",
          speakable("Alphabet Inc Cl C"))
    # A real name must survive all of that trimming intact.
    for keep in ("Baxter International", "Advanced Micro Devices",
                 "Agilent Technologies Inc", "Berkshire Hathaway Inc-B"):
        got = speakable(keep)
        check(f"{keep!r} keeps its identity", got and got.split()[0] == keep.split()[0]
              and len(got) >= len(keep.split()[0]), got)

    # --- his sentence, which is the whole point -------------------------------
    verdict, line = stance(name="Nvidia", percent=4.6, consensus="buy", buy=40,
                           hold=5, sell=1, analysts=46, held=True)
    check("a stock analysts like that has already run is NOT a buy signal",
          verdict == "positive, but extended", verdict)
    check("...and it says so in words he used", "chasing" in line, line)
    check("...and it tells him he owns it", "you own it" in line, line)

    verdict, line = stance(name="Nike", percent=-3.1, consensus="buy", buy=22,
                           hold=8, sell=2, analysts=32, held=False)
    check("the same view on a name that sold off IS actionable",
          verdict == "positive, and cheaper", verdict)
    check("...and he hears about names he does not own", "Nike" in line, line)
    check("...without being told he owns it", "you own it" not in line, line)

    # --- the tiers that are not a buy ----------------------------------------
    check("a split verdict says split",
          stance(name="X", percent=0, consensus="hold", buy=5, hold=9, sell=4,
                 analysts=18, held=False)[0] == "split")
    check("a negative verdict says negative",
          stance(name="X", percent=1, consensus="sell", buy=1, hold=3, sell=9,
                 analysts=13, held=False)[0] == "negative")

    # --- silence about an uncovered name, rather than invention ---------------
    verdict, line = stance(name="Tiny Co", percent=9.0, consensus="", buy=0,
                           hold=0, sell=0, analysts=0, held=False)
    check("no coverage is admitted, not guessed", verdict == "no coverage", verdict)
    check("...and no view is implied", "buy" not in line.lower(), line)

    # --- it must never sound like advice --------------------------------------
    for pct, cons in ((4.6, "buy"), (-3.1, "buy"), (0.0, "hold"), (1.0, "sell")):
        _, line = stance(name="X", percent=pct, consensus=cons, buy=9, hold=2,
                         sell=1, analysts=12, held=False)
        check(f"no imperative in the {cons} line at {pct}%",
              not any(w in line.lower() for w in
                      ("you should", "i recommend", "guaranteed", "will rise")),
              line)

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
