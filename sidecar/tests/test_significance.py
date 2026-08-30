"""What is worth interrupting him for.

The load-bearing test of the whole proactive design. If this gets it wrong in one
direction he stops reading the briefs; in the other, he misses the thing that
mattered.

His rule, verbatim: *"I don't need to hear about a road closure in Ohio, but if
there is major news like a gas leak in an Ohio facility, I wanna know about it."*
Both of those are checked below, by name.

Run: python tests/test_significance.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from significance import (ALERT, NONE, NOTABLE, URGENT, classify_market,  # noqa: E402
                          classify_news)

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


def tier(headline, summary=""):
    return classify_news({"headline": headline, "summary": summary})[0]


def main() -> int:
    # --- his own words, both halves -------------------------------------------
    check("a road closure in Ohio is not news to him",
          tier("Route 33 lane closure in Columbus, Ohio through Friday") == NONE,
          tier("Route 33 lane closure in Columbus, Ohio through Friday"))
    check("a gas leak at an Ohio facility IS",
          tier("Gas leak at Ohio chemical facility forces evacuation of 300 homes")
          in (URGENT, ALERT))

    # --- life and limb, close to home -----------------------------------------
    check("an active shooter in Framingham wakes him",
          tier("Police respond to active shooter report in Framingham") == URGENT)
    check("an earthquake in Massachusetts wakes him",
          tier("Earthquake felt across Massachusetts, damage reported") == URGENT)
    check("a fatal crash in Natick reaches him now",
          tier("Fatal crash closes Route 9 in Natick") in (URGENT, ALERT))

    # --- and far away, when it is big enough ----------------------------------
    check("a distant disaster with casualties still reaches him",
          tier("Tornado kills 14 in Oklahoma, hundreds injured") == URGENT)
    check("a distant hazard still reaches him — it is still unfolding",
          tier("Small explosion reported at a warehouse in Nevada") == ALERT)

    # --- national weight -------------------------------------------------------
    check("war news of consequence is an alert",
          tier("President declares state of emergency as invasion begins") == ALERT)
    check("routine politics waits for the brief",
          tier("Congress debates infrastructure funding bill") == NOTABLE)

    # --- his towns get a lower bar, but not NO bar -----------------------------
    check("something real in Sudbury reaches him",
          tier("Sudbury schools closed after water main break") == ALERT)
    check("a ribbon cutting in Maynard does not",
          tier("Ribbon cutting for new Maynard library wing") == NOTABLE,
          tier("Ribbon cutting for new Maynard library wing"))
    check("a Natick road closure is a line in the brief, not an interruption",
          tier("Road closure on Speen Street in Natick for construction") == NOTABLE)

    # --- and the rest of the world's small change is not mentioned at all ------
    for junk in ("High school football roundup: Iowa playoffs",
                 "Farmers market returns to downtown Tulsa",
                 "Traffic delays expected on I-5 in Oregon",
                 "Local library in Phoenix extends summer hours"):
        check(f"below the bar: {junk[:38]!r}", tier(junk) == NONE, tier(junk))

    # --- a death, with no hazard word to announce it ---------------------------
    # "Fatal MBTA rail incident" names no danger at all, and read as ordinary
    # local news until FATALITY existed. Somebody died near his home.
    check("a fatal incident near home is not a footnote",
          tier("Fatal MBTA rail incident in Boston") == ALERT,
          tier("Fatal MBTA rail incident in Boston"))
    check("a death in one of his towns wakes him",
          tier("Man dies in Framingham house fire") == URGENT)
    check("many deaths anywhere wake him",
          tier("Dozens killed in bus crash in Peru") == URGENT)
    check("a single distant death waits for the brief",
          tier("Fatal crash on I-80 in Nebraska") == NOTABLE,
          tier("Fatal crash on I-80 in Nebraska"))

    # --- a real headline that used to wake him for nothing ---------------------
    # One death, abroad, no scale. It read as URGENT because the scale words
    # include the fatality words; that is now separated.
    check("a single death abroad does not wake him",
          tier("British woman killed in stabbing at German railway station") == NOTABLE,
          tier("British woman killed in stabbing at German railway station"))
    check("...but a mass casualty abroad does",
          tier("Mass casualty incident at German railway station, dozens dead") == URGENT)

    # --- how the local press actually writes his state -------------------------
    # "mass." was matched with a leading space so that "mass shooting" would not
    # read as Massachusetts. That also made a headline STARTING with "Mass." -
    # the local house style - not local at all, and it was dropped from his brief.
    from significance import is_local  # noqa: E402
    check("a headline starting 'Mass.' is his state",
          is_local({"headline": "Mass. awards $17.9 million to 31 communities"})[0])
    check("...and 'mass shooting' still is not",
          not is_local({"headline": "Mass shooting reported in Denver"})[0])
    check("...nor 'mass casualty'",
          not is_local({"headline": "Mass casualty incident in Berlin"})[0])
    check("a town name still wins outright",
          is_local({"headline": "Fatal crash closes Route 9 in Natick"}) == (True, True))

    # --- an empty story is not a story ----------------------------------------
    check("nothing is not something", classify_news({})[0] == NONE)

    # --- the market, and whose money it is -------------------------------------
    check("a 12% move in something he OWNS wakes him",
          classify_market(symbol="NVDA", percent=-12, held=True)[0] == URGENT)
    check("a 6% move in something he owns reaches him now",
          classify_market(symbol="AAPL", percent=6, held=True)[0] == ALERT)
    check("a 3% move in something he owns waits for the brief",
          classify_market(symbol="TSLA", percent=-3, held=True)[0] == NOTABLE)
    check("a 1% move in something he owns is an ordinary day",
          classify_market(symbol="AMC", percent=1, held=True)[0] == NONE)

    check("the same 6% in a stock he does NOT own is not an interruption",
          classify_market(symbol="XYZ", percent=6)[0] == NONE,
          classify_market(symbol="XYZ", percent=6))
    check("...but a 20% move anywhere is worth saying",
          classify_market(symbol="XYZ", percent=20)[0] == ALERT)

    check("the market falling 4% is an alert",
          classify_market(symbol="SPY", percent=-4, is_index=True)[0] == ALERT)
    check("the market drifting 0.3% is nothing",
          classify_market(symbol="SPY", percent=-0.3, is_index=True)[0] == NONE)

    # --- the asymmetry that matters -------------------------------------------
    # It must be harder to interrupt him than to stay quiet. If these ever
    # invert, he stops reading.
    louder = classify_market(symbol="NVDA", percent=6, held=True)[0]
    quieter = classify_market(symbol="NVDA", percent=6, held=False)[0]
    check("owning it always raises the tier, never lowers it",
          (louder, quieter) == (ALERT, NONE), (louder, quieter))

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
