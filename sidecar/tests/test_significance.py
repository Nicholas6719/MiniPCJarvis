"""What is worth interrupting him for.

The load-bearing test of the whole proactive design. If this gets it wrong in one
direction he stops reading the briefs; in the other, he misses the thing that
mattered.

His rule, as of 2026-08-30 and verbatim: *"I've been getting WAY too many news
reports... I want the same type of emergency notifications/alerts but let's keep
all news local."* The first block below is exactly that - the four real headlines
that made him say it, all now silent, and the local emergencies that must still
get through unchanged.

The second block checks the wider setting he is NOT on (`briefing.news_scope =
"national"`), including his earlier rule: *"I don't need to hear about a road
closure in Ohio, but if there is major news like a gas leak in an Ohio facility, I
wanna know about it."* It is kept because the tiers did not change and one line
brings it back.

Run: python tests/test_significance.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import significance  # noqa: E402
from significance import (ALERT, NONE, NOTABLE, URGENT, classify_market,  # noqa: E402
                          classify_news)

fails = []


def news_mode(mode: str) -> None:
    """His rule as of 2026-08-31: emergencies only.

    *"Only have him tell me about emergencies from now on... I only want to hear
    about the emergencies and the local ones. Only tell me about national ones
    if it's extremely important."* So NOTABLE stops existing for news - no local
    colour, no town notices, no roll-up. The "all" expectations below are kept
    because the tiers are unchanged and one config line restores them.
    """
    significance.emergencies_only = lambda: mode != "all"


def scope(mode: str) -> None:
    """Switch between his setting and the wider one, without a config file.

    He runs LOCAL. The national expectations below are kept and still checked,
    because the machinery is unchanged and one config line brings it back - but
    they are no longer what his phone does.
    """
    significance.local_only = lambda: mode != "national"


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


def tier(headline, summary=""):
    return classify_news({"headline": headline, "summary": summary})[0]


def main() -> int:
    # ==========================================================================
    # HIS SETTING: local only. Everything in this block is what his phone does.
    # ==========================================================================
    scope("local")
    news_mode("emergencies")

    # The ones that actually reached him on 2026-08-30 and made him say "WAY too
    # many news reports". Every one is real, serious, and none of it is his.
    # The gas leaks are here by his own instruction: he named an Arizona gas leak
    # and asked whether it survives. It does not - it is not something everyone in
    # the country needs to hear about, which is the bar he set.
    for far in ("Ross Fire burns 85,000 acres, leaves destruction across 2 counties",
                "Grand Canyon flash floods leave more than 20 missing, dozens evacuated",
                "Gas leak at Ohio chemical facility forces evacuation of 300 homes",
                "Gas leak in Arizona forces evacuation of 300 homes",
                "Tornado kills 14 in Oklahoma, hundreds injured",
                "Hurricane makes landfall in Florida, state of emergency declared",
                "Power outage affects 40,000 in Denver",
                "Small explosion reported at a warehouse in Nevada",
                "Israeli settlers stage new attack on home in West Bank's Qusra",
                "Visa, Mastercard launch international card payments in Syria"):
        check(f"silent: {far[:44]!r}", tier(far) == NONE, tier(far))

    # ...and the one door left open: *"the absolute emergencies from other places,
    # something everyone in the country needs to know or hear about."*
    for huge in ("Terrorist attack at Chicago airport leaves dozens dead",
                 "President assassinated in Dallas",
                 "Nuclear plant meltdown prompts evacuation in Pennsylvania",
                 "Nationwide grid failure leaves millions without power",
                 "CDC declares pandemic as new virus spreads",
                 "US declares war after missile strike on naval base",
                 "Mass casualty incident at Los Angeles stadium",
                 "Hundreds dead as earthquake levels city"):
        check(f"reaches him: {huge[:42]!r}", tier(huge) == URGENT, tier(huge))

    # --- somebody else's country is not "the country" -------------------------
    # On 2026-08-31 at 1:42pm he was sent "Nepal rescuers blast hillside in search
    # of hydropower workers" and then chased about it twice. The BBC summary said
    # hundreds dead, which is exactly what CATASTROPHIC_TOLL is for - so the rule
    # fired as designed, and the design was wrong. "Everyone in THE COUNTRY needs
    # to know" means his country.
    for abroad, summ in (
            ("Nepal rescuers blast hillside in search of hydropower workers",
             "Hundreds dead as floods sweep the valley."),
            ("Nepal flash flooding death toll rises past 800", ""),
            ("Hundreds dead in Pakistan earthquake", ""),
            ("Mass casualty incident at German railway station", ""),
            ("Thousands killed as cyclone hits Bangladesh", "")):
        got = classify_news({"headline": abroad, "summary": summ})[0]
        check(f"not his emergency: {abroad[:40]!r}", got == NONE, got)

    # ...but a foreign dateline that IS his country's news still counts
    check("a US strike abroad is still national news",
          tier("US declares war after missile strike on naval base") == URGENT)
    check("a catastrophe at home is unaffected",
          tier("Hundreds dead as tornado levels Joplin") == URGENT)

    # --- a place name is not evidence -----------------------------------------
    # The always-scanned wire is BBC and Sky, and England has a Boston, a
    # Cambridge, a Worcester and a Newton. Verified against the real classifier:
    # "Cambridge United striker killed in car crash" read as somebody dying close
    # to home, and "Marlboro maker Altria" as one of his five towns.
    for uk in ("Cambridge United striker killed in car crash",
               "Worcester man charged over stabbing outside pub",
               "Boston United sack manager after relegation",
               "Marlboro maker Altria to cut 500 jobs"):
        check(f"not his: {uk[:38]!r}", tier(uk) == NONE, tier(uk))

    # ...while a story off one of HIS desks is local whatever it says
    from_desk = {"headline": "Fatal crash closes Route 9", "_local_feed": True}
    check("an emergency from his own desk is local", classify_news(from_desk)[0] != NONE,
          classify_news(from_desk))
    check("an explicit Massachusetts is local too",
          tier("Man held without bail after woman found dead in Mass. home")
          in (URGENT, ALERT))
    check("and his towns still are",
          tier("Fatal crash closes Route 9 in Natick") == URGENT)

    # --- "us" the pronoun is not "US" the country -----------------------------
    # re.I on the abbreviation matched the ordinary word, and RSS summaries are
    # full of it: "a survivor told us the ground shook" put a Nepal quake back
    # through the FOREIGN gate about an hour after that gate was written.
    nepal = {"headline": "Nepal quake: hundreds dead",
             "summary": "A survivor told us the ground shook for a minute."}
    check("a pronoun does not make a foreign disaster national",
          classify_news(nepal)[0] == NONE, classify_news(nepal))
    check("...but the actual country still does",
          tier("US declares war after missile strike on naval base") == URGENT)

    # A national emergency is not proven by a scary word. An early version of this
    # used a "man-made hazard" keyword list and matched a Trump/NBC story, a
    # Visa/Mastercard announcement and a West Bank report - "attack" alone is
    # worthless. These are the shapes that must NOT be mistaken for one.
    for notreally in ("Trump criticizes NBC over election comment",
                      "Analysts attack the Fed's latest projections",
                      "Company recalls 4,000 units over faulty wiring",
                      "Storm knocks out power for 12,000 in Ohio"):
        check(f"not an emergency: {notreally[:36]!r}",
              tier(notreally) == NONE, tier(notreally))

    # ...while the emergency machinery near him is untouched. This is the whole
    # point of the change: quieter, not deafer.
    check("an active shooter in Framingham still wakes him",
          tier("Police respond to active shooter report in Framingham") == URGENT)
    check("a Boston hazmat call still reaches him",
          tier("Boston hazmat team responds to chemical exposure at Mass General")
          in (URGENT, ALERT))
    check("a death in a Mass. home still reaches him",
          tier("Man held without bail after woman found dead in Mass. home")
          in (URGENT, ALERT))
    check("the MBTA death he was sent is still his news",
          tier("Teen dies after fall at Massachusetts Bay Transportation Authority "
               "train station") in (URGENT, ALERT))
    check("a Natick fatal crash still wakes him",
          tier("Fatal crash closes Route 9 in Natick") in (URGENT, ALERT))

    # Going local-only nearly backfired here. With his towns on a low bar and
    # nothing else arriving, every town notice became an ALERT - he would have
    # swapped national noise for Sudbury noise. His town gets a lower bar for
    # being MENTIONED, not for interrupting him.
    for townish in ("Sudbury police to host e-bike safety presentation",
                    "Framingham State University announces new dean",
                    "Natick Mall to add three new stores this fall",
                    "Marlborough approves budget for new sidewalk project",
                    "Maynard restaurant wins regional award"):
        check(f"not an emergency, so silent: {townish[:34]!r}",
              tier(townish) == NONE, tier(townish))

    # ...but anything that changes what he can actually do today does ping.
    for real in ("Sudbury schools closed after water main break",
                 "Power outage affects 4,000 in Framingham",
                 "Boil water order issued for Natick"):
        check(f"pings him: {real[:38]!r}", tier(real) in (URGENT, ALERT), tier(real))

    # ==========================================================================
    # THE WIDER SETTING (briefing.news_scope = "national"), which he is not on.
    # Kept because the tiers themselves did not change and he may want it back.
    # ==========================================================================
    scope("national")

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
          tier("Congress debates infrastructure funding bill") not in (URGENT, ALERT))

    # --- his towns get a lower bar, but not NO bar -----------------------------
    check("something real in Sudbury reaches him",
          tier("Sudbury schools closed after water main break") == ALERT)
    check("a ribbon cutting in Maynard does not",
          tier("Ribbon cutting for new Maynard library wing") not in (URGENT, ALERT),
          tier("Ribbon cutting for new Maynard library wing"))
    check("a Natick road closure is a line in the brief, not an interruption",
          tier("Road closure on Speen Street in Natick for construction") not in (URGENT, ALERT))

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
          tier("Fatal crash on I-80 in Nebraska") not in (URGENT, ALERT),
          tier("Fatal crash on I-80 in Nebraska"))

    # --- a real headline that used to wake him for nothing ---------------------
    # One death, abroad, no scale. It read as URGENT because the scale words
    # include the fatality words; that is now separated.
    check("a single death abroad does not wake him",
          tier("British woman killed in stabbing at German railway station") not in (URGENT, ALERT),
          tier("British woman killed in stabbing at German railway station"))
    check("...but a mass casualty abroad does",
          tier("Mass casualty incident at German railway station, dozens dead") == URGENT)

    scope("local")            # back to his actual setting

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

    # --- "death" doing legal work is not a death ------------------------------
    # A real alert on his phone: a Hindustan Times explainer about what sentence
    # Lindsay Clancy could face read as "somebody died close to home", because
    # FATALITY found the word "death" inside "death penalty". Nobody had died.
    for legal in ("Lindsay Clancy trial: Does Massachusetts have the death "
                  "penalty? What sentence could she face if found guilty?",
                  "Massachusetts man on death row seeks new trial",
                  "Suspect received death threats, Boston police say"):
        check(f"not a death: {legal[:34]!r}", tier(legal) not in (URGENT, ALERT),
              tier(legal))

    # ...while a real one near him is untouched
    check("a real local death still reaches him",
          tier("Man dies in Framingham house fire") == URGENT)
    check("a death toll is still a death",
          tier("Death toll rises to 12 in Massachusetts flooding")
          in (URGENT, ALERT), tier("Death toll rises to 12 in Massachusetts flooding"))

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

    # --- an obituary is not an emergency (2026-09-01) -------------------------
    # He was sent "Dolly Parton died on August 25 after a brief battle with
    # cancer. Levi Herman, an outlaw country musician, died on Monday
    # — MassLive." and asked, fairly: *"this doesn't seem like a news
    # emergency... so why am I hearing about it?"* Local desks reprint national
    # obituaries, so provenance made it local and the word "died" made it fatal.
    # An emergency is an INCIDENT — something happened, and it may still be
    # happening. An illness is news, and news waits for the brief.
    for obit in ("Dolly Parton died on August 25 after a brief battle with cancer",
                 "Levi Herman, an outlaw country musician, died on Monday",
                 "Beloved actor dies at 88 after long illness",
                 "Grammy-winning singer passed away at her home"):
        got, why = classify_news({"headline": obit, "_local_feed": True})
        check(f"obituary is silent: {obit[:36]!r}", got == NONE, f"{got} ({why})")

    # ...and a death that IS an incident still reaches him
    for real in ("Crews battle house fire on Concord Street in Framingham",
                 "One dead after crash on Route 9 in Natick",
                 "Fatal MBTA rail incident halts the Framingham line"):
        got, why = classify_news({"headline": real, "_local_feed": True})
        check(f"an incident death still reaches him: {real[:32]!r}",
              got in (URGENT, ALERT), f"{got} ({why})")

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
