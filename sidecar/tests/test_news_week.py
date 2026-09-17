"""The week his phone got it wrong (2026-09-08 to 13), replayed.

Every delivery in that week was read back out of his database. His rule is
unchanged - *"I expect local news that's an emergency. I expect national news
that's a huge emergency"* - and as URGENT he received: a dead pit-bull, a pond
of trout, an outbreak that had ended (twice), a crash in New Hampshire off a
Boston desk, a bomb threat where nothing was found, a smoky Green Line train
and an op-ed two days after an assassination. The assassination itself never
reached him on the day. One cause each; every headline below is real.

Run: python tests/test_news_week.py
"""
import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "week.db"))

import significance  # noqa: E402
from significance import ALERT, NONE, URGENT, classify_news  # noqa: E402

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


def tier(headline, summary="", local=True):
    s = {"headline": headline, "summary": summary}
    if local:
        s["_local_feed"] = True
    return classify_news(s)


def main() -> int:
    # his settings: local, emergencies only
    significance.local_only = lambda: True
    significance.emergencies_only = lambda: True

    print("\n-- what reached him, and should not have --")
    quiet = [
        ("a dead dog is not a fatality",
         "Police find dead dog, several others emaciated in Boston apartment, owner arrested",
         "A man was arrested after police found a dead pit-bull named Gwap and several emaciated "
         "dogs in his Boston apartment, where he kept them for breeding."),
        ("...and neither is a pond of trout",
         "Biologists determine why thousands of rainbow trout died in small pond", ""),
        ("an outbreak declared OVER is over",
         "Massive cyclospora outbreak linked to lettuce declared over, FDA says",
         "The FDA declared the multistate cyclospora outbreak linked to contaminated iceberg "
         "lettuce officially over."),
        ("...and so is one that 'has ended'",
         "Health officials give important update on parasite outbreak in MA",
         "Health officials announced that the cyclospora outbreak has ended and all recalled "
         "iceberg lettuce is off the market."),
        ("Laconia is New Hampshire, whatever desk it came off",
         "3 people dead after overnight crash in Laconia", ""),
        ("...with or without the state in the summary",
         "3 people dead after overnight crash in Laconia",
         "Three people died in a motorcycle crash near the Looney Bin Bar and Grill in Laconia, "
         "New Hampshire, just before 11 p.m. Saturday."),
        ("a teen charged over THREATS is not a shooting",
         "Mass. teen charged after school shooting, bombing threats",
         "A 17-year-old from Raynham was arrested after making school shooting and bombing threats "
         "on an online gaming platform. He was charged with making terroristic threats."),
        ("a bomb threat where nothing was found is over",
         "Logan cargo terminal evacuated after report of bomb threat",
         "A bomb threat was phoned into a cargo terminal at Logan Airport, prompting an evacuation "
         "and a bomb squad sweep that found no hazardous materials."),
        ("...and even before the summary says so, Logan is Boston's",
         "Logan cargo terminal evacuated after report of bomb threat", ""),
        ("a smoky train at North Station is Boston's",
         "Green Line train at North Station evacuated due to smoke",
         "Smoke was detected on the roof of a Green Line train at North Station, so passengers "
         "were evacuated onto another train and the smoky train was taken out of service."),
        ("a recall is a notice, whatever its label says",
         "Popular infant toys sold nationwide recalled over 'serious injury or death'",
         "More than 22,000 Baby Sesame Street Elmo Silicone Teethers are being recalled because "
         "small pieces can break off and pose a choking hazard."),
        ("Hawaiian is Hawaii",
         "Hawaiian homes devastated by Hurricane Lowell", ""),
        ("an op-ed two days after an assassination is not the assassination",
         "Neighbor turned on neighbor after Charlie Kirk's killing. Is the divide here to stay?",
         "Charlie Kirk was assassinated at Utah Valley University in Orem, Utah."),
        ("...nor is the fallout",
         "They lost their jobs after posting about Charlie Kirk, but some have no regrets",
         "He posted critical remarks about Charlie Kirk after the activist's death, the posts went "
         "viral, and he was fired from his job."),
        ("a new office is not an emergency",
         "Take a look inside Fidelity's new headquarters. Workers are full time in office.",
         "Fidelity employees returned full-time to its newly renovated headquarters at "
         "Commonwealth Pier this week."),
        ("a quiet hurricane season is not one",
         "Where have the Atlantic hurricanes gone? El Nino might bring record slow start",
         "No Atlantic hurricanes have formed yet this year, and the season remains the quietest "
         "on record because a strong El Nino is suppressing storm development."),
        ("a boil-water order LIFTED in his own town is the end of something",
         "Boil water order lifted in Framingham after tests come back clean", ""),
        ("a dog in a hot car in his town is not a fatality either",
         "Framingham police: dog dies after being left in hot car outside Natick Mall", ""),
    ]
    for name, head, summ in quiet:
        t, why = tier(head, summ)
        check(name, t == NONE, f"{t}: {why}")

    print("\n-- what must still reach him --")
    loud = [
        ("the assassination itself, on the day",
         "Conservative activist Charlie Kirk shot dead at Utah university event", "", False, (URGENT,)),
        ("...however it is worded",
         "Charlie Kirk, conservative activist, assassinated during campus event in Utah", "", False, (URGENT,)),
        ("a senator killed in an attack",
         "US senator killed in shooting at town hall event", "", False, (URGENT,)),
        ("an active shooter in his town",
         "Active shooter reported at Framingham High School, police responding", "", True, (URGENT,)),
        ("a gas leak with evacuations in his town",
         "Gas leak forces evacuation of homes on Main Street in Natick", "", True, (URGENT,)),
        ("people dead in his town",
         "Two dead in overnight Sudbury house fire", "", True, (URGENT,)),
        ("his own school emptied for a bomb threat, whatever is found later",
         "Framingham High School evacuated after bomb threat", "", True, (URGENT, ALERT)),
        ("a gunman at large in Boston reaches him",
         "Mass shooting at Boston nightclub, gunman at large", "", True, (URGENT, ALERT)),
        ("...and so does a gas leak in the Back Bay",
         "Gas leak forces evacuations in Boston's Back Bay, crews on scene", "", True, (URGENT, ALERT)),
        ("a tornado through Middlesex County",
         "Tornado touches down in Middlesex County, homes destroyed in Sudbury", "", True, (URGENT, ALERT)),
        ("a nationwide grid failure",
         "Nationwide grid failure leaves millions without power", "", False, (URGENT,)),
        ("hundreds dead somewhere in the country",
         "Hundreds dead as dam fails above Texas town", "", False, (URGENT,)),
    ]
    for name, head, summ, local, want in loud:
        t, why = tier(head, summ, local=local)
        check(name, t in want, f"{t}: {why}")

    print("\n-- read the article, then judge it again --")
    import briefing as br
    import newsroom
    import nws

    async def fake_stories(items):
        return items

    async def fake_moves():
        return []

    async def no_weather(seen):
        return []

    nws.scan = no_weather
    summaries = {
        "Multiple people dead in overnight crash":
            "Three people died in a motorcycle crash in Laconia, New Hampshire, late on Saturday.",
        "Gas leak forces evacuation in Natick":
            "A gas leak on Main Street in Natick forced the evacuation of thirty homes; crews are on scene.",
        "Terminal evacuated after threat":
            "A bomb threat was phoned in, prompting an evacuation and a sweep that found no hazardous materials.",
    }

    async def fake_summarize(story):
        h = story.get("headline") or ""
        return {"headline": h, "summary": summaries.get(h, ""), "source": "WCVB",
                "url": "", "read": h in summaries}

    newsroom.summarize = fake_summarize
    b = br.Briefing()
    # The gates share one database, and a seen story is remembered for three
    # days now: another gate's "Gas leak ... in Natick" must not dedupe this one.
    b._seen = {}
    b._primed = True
    b._market_moves = fake_moves
    b._fresh_stories = lambda: fake_stories([
        {"headline": "Multiple people dead in overnight crash", "source": "WCVB",
         "age_minutes": 5, "_local_feed": True},
        {"headline": "Gas leak forces evacuation in Natick", "source": "WCVB",
         "age_minutes": 5, "_local_feed": True},
        {"headline": "Terminal evacuated after threat", "source": "WHDH",
         "age_minutes": 5, "_local_feed": True},
        # unreadable link: no summary, judged on the headline as before
        {"headline": "Active shooter reported in Framingham", "source": "WHDH",
         "age_minutes": 5, "_local_feed": True},
    ])
    found = asyncio.run(b.scan())
    heads = " | ".join(t for t, _, _ in found)
    check("a crash the headline put near him, and the article put in New Hampshire, is not sent",
          "Laconia" not in heads and "crash" not in heads.lower(), heads)
    check("a threat the article says came to nothing is not sent",
          "threat" not in heads.lower(), heads)
    check("a gas leak the article confirms still goes", "Natick" in heads, heads)
    check("a story with no readable article is judged on its headline, as before",
          "shooter" in heads.lower(), heads)
    check("...and the demoted ones are remembered, so they are not asked again",
          any("multiple people dead" in k for k in b._seen), list(b._seen)[:4])

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
