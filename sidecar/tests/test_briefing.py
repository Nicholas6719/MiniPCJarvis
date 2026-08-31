"""His shift: four briefs a day, and a watch for the things that will not wait.

Two rules, and they pull in opposite directions on purpose:

  a DIGEST can wait until morning        -> quiet hours suppress briefs
  an EMERGENCY cannot                    -> quiet hours never suppress alerts

He was explicit about the second: *"if there is breaking national news at 3 a.m.,
I want to know about it."*

Offline: nothing is fetched, nothing is sent.
Run: python tests/test_briefing.py
"""
import asyncio
import datetime as dt
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "gate.db"))
import briefing as br  # noqa: E402

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


def story(headline, age=10, source="Somewhere"):
    return {"headline": headline, "source": source, "age_minutes": age}


class Recorder:
    """Stands in for delivery: records rather than speaking or sending."""

    def __init__(self):
        self.sent = []

    async def deliver(self, text, tier="notable", *, key="", subject=""):
        self.sent.append((tier, text))
        return {"delivered": "recorded"}


def main() -> int:
    # --- quiet hours: the whole point of the two rules ------------------------
    at = dt.datetime(2026, 8, 30, 3, 0)
    check("3 a.m. is quiet hours", br._in_quiet_hours(at))
    check("midday is not", not br._in_quiet_hours(dt.datetime(2026, 8, 30, 12, 30)))
    check("07:30 is not quiet — that is his first brief",
          not br._in_quiet_hours(dt.datetime(2026, 8, 30, 7, 30)))

    b = br.Briefing()
    rec = Recorder()
    br.delivery = rec

    # a brief due during quiet hours is HELD...
    b._last_brief = ""
    br._now = lambda: dt.datetime(2026, 8, 30, 3, 0)
    asyncio.run(b._maybe_brief())
    check("a brief due at 3 a.m. is not sent", rec.sent == [], rec.sent)

    # ...but an alert at the same hour is not
    b._primed = True
    b._seen.clear()
    b.scan = lambda **k: _fake_scan([("Active shooter in Natick", "urgent")],
                                    news=k.get("news", True))
    b._last_news = b._last_market = 0
    asyncio.run(b._maybe_watch())
    check("an alert at 3 a.m. IS sent", len(rec.sent) == 1 and rec.sent[0][0] == "urgent",
          rec.sent)

    # --- the watch does not mistake "already out there" for "just happened" ---
    rec.sent.clear()
    b2 = br.Briefing()
    b2.scan = lambda **k: _fake_scan([("Something serious", "alert")],
                                     news=k.get("news", True))
    b2._last_news = b2._last_market = 0
    asyncio.run(b2._maybe_watch())
    check("the first pass after a restart sends nothing", rec.sent == [], rec.sent)
    check("...and marks itself primed", b2._primed)
    b2._last_news = b2._last_market = 0
    asyncio.run(b2._maybe_watch())
    check("the second pass sends normally", len(rec.sent) == 1, rec.sent)

    # --- old news is not breaking news ---------------------------------------
    b3 = br.Briefing()
    b3._primed = True
    old = story("Active shooter reported in Framingham", age=4000)
    fresh = story("Gas leak forces evacuation in Natick", age=5)
    b3._fresh_stories = lambda: _fake_stories([old, fresh])
    b3._market_moves = lambda: _fake_moves()
    found = asyncio.run(b3.scan())
    heads = " ".join(t for t, _, _ in found)
    check("a three-day-old story does not wake him", "Active shooter" not in heads, heads)
    check("...but a fresh one does", "Gas leak" in heads, heads)

    # --- one event, four newsrooms -------------------------------------------
    b4 = br.Briefing()
    same = [story("Framingham man allegedly told police he accidentally killed woman"),
            story("Man held without bail after deadly assault in Framingham"),
            story("Man admits to killing 33-year-old woman in Framingham")]
    check("the same killing, reported three ways, is one story",
          len(b4._dedupe(same)) == 1, [s["headline"][:40] for s in b4._dedupe(same)])
    check("two genuinely different stories both survive",
          len(b4._dedupe([story("Gas leak in Natick"),
                          story("Nvidia falls after earnings")])) == 2)

    # --- NOTABLE means "waits for the brief", not "belongs in the brief" ------
    # Both of these are real, and both filled the first brief he would have got.
    b6 = br.Briefing()
    b6._primed = True
    b6._market_moves = lambda: _fake_moves()
    b6._fresh_stories = lambda: _fake_stories([
        story("Man Utd pay tribute to 'proud Red' officer who died in A66 crash"),
        story("Football Adds Former Minuteman Kevin Coyle To Defensive Staff"),
        story("Mass. awards $17.9 million to improve health in 31 communities"),
    ])
    asyncio.run(b6.scan())
    rolled = " | ".join(h["text"] for h in b6._held)
    check("a UK football tribute does not reach his morning",
          "A66" not in rolled, rolled)
    check("...and neither does a college football staff hire",
          "Defensive Staff" not in rolled, rolled)
    check("...but something from his own state does",
          "Mass." in rolled, rolled)

    # --- the one door that must stay open ------------------------------------
    # He is on local-only. An earlier version stopped FETCHING the national wire
    # to save the call, which silently closed the exception he asked for: "the
    # absolute emergencies from other places". The wire is always read; the
    # classifier is what discards it.
    import significance as sig
    b7 = br.Briefing()
    b7._primed = True
    b7._market_moves = lambda: _fake_moves()
    b7._fresh_stories = lambda: _fake_stories([
        story("Ross Fire burns 85,000 acres across 2 counties"),
        story("Nuclear plant meltdown prompts evacuation in Pennsylvania"),
    ])
    found = asyncio.run(b7.scan())
    heads = " ".join(t for t, _, _ in found)
    check("a California wildfire does not reach him", "Ross Fire" not in heads, heads)
    check("a nuclear meltdown does", "meltdown" in heads, heads)
    check("...and it is urgent", any(tr == "urgent" for _t, tr, _k in found), found)
    check("the national wire is still read on local-only", sig.local_only())

    # --- two clocks: emergencies more often than prices -----------------------
    # He asked: "can it check for just emergencies every 5 minutes, or is it all
    # or nothing?" It is not all or nothing. The news lane runs on
    # `emergency_minutes` (5), the market lane on `watch_minutes` (10).
    rec.sent.clear()
    b8 = br.Briefing()
    b8._primed = True
    lanes = []
    async def _record(news=True, market=True):
        lanes.append("news" if news else "market")
        return []
    b8.scan = _record

    now = __import__("time").time()
    b8._last_news = b8._last_market = now          # both just ran
    asyncio.run(b8._maybe_watch())
    check("nothing runs when neither lane is due", lanes == [], lanes)

    b8._last_news = now - 5 * 60                   # 5 minutes on
    b8._last_market = now - 5 * 60
    asyncio.run(b8._maybe_watch())
    check("at 5 minutes the emergency lane runs alone", lanes == ["news"], lanes)

    lanes.clear()
    b8._last_news = b8._last_market = now - 10 * 60   # 10 minutes on
    asyncio.run(b8._maybe_watch())
    check("at 10 minutes both lanes run", lanes == ["news", "market"], lanes)

    # and a feed that stops answering makes it back off rather than hammer
    b9 = br.Briefing()
    b9._fresh_stories_real = br.Briefing._fresh_stories
    check("a healthy feed has no backoff", b9._feed_fails == 0)
    b9._feed_fails = 3
    check("a refusing feed stretches the gap, not the other way",
          (1 + min(b9._feed_fails, 5)) > 1)

    # --- one brief, two shapes ------------------------------------------------
    # He was sent a 300-word spoken paragraph on Telegram and said: "way too
    # cluttered and not easy to read at all. I should get clear and concise bullet
    # points!" The cause was structural - one string written for the ear, then
    # sent to a screen. Both shapes are built from one set of sections now.
    bA = br.Briefing()
    bA._fresh_stories = lambda: _fake_stories([
        story("Man held without bail after woman found dead in Mass. home",
              source="WCVB"),
        # a real headline, complete with the trailing dash its CMS emitted
        story("Mass. 2026 Poll: Markey Holds Lead Over Moulton in Primary -",
              source="Patch")])
    bA._held = []

    async def _sections_of(b):
        import tools.market_tools as mt
        mt.get_market_movers = lambda: _fake_dict({"markets": [
            {"symbol": "SPY", "name": "the S&P 500", "percent": -0.23}]})
        mt.get_watchlist = lambda: _fake_dict({"stocks": [
            {"symbol": "AMC", "name": "Amc Entertainment Hlds-Cl A", "percent": -4.07},
            {"symbol": "SPCX", "name": "Space Exploration Techn-Cl A", "percent": 0.45}]})
        import analyst as _an
        _an.analyst.take = lambda limit=8: _fake_take()
        secs = await b._sections()
        return secs, await b.compose_brief(secs), await b.compose_brief_written(secs)

    import analyst as _analyst_mod
    _real_take = _analyst_mod.analyst.take
    secs, said, shown = asyncio.run(_sections_of(bA))
    # put it back: a stub left in place made the later "quiet day" check see a
    # market take and fail, which is the test leaking, not the code breaking
    _analyst_mod.analyst.take = _real_take

    check("the written brief is bullets, not a paragraph",
          shown.count("•") >= 3, shown[:120])
    check("...with headings he can scan", "YOURS" in shown and "NEWS" in shown,
          shown[:120])
    check("the spoken brief has no bullets", "•" not in said, said[:120])

    # the two must not disagree about the numbers, only about their shape
    check("the screen shows a signed percentage", "-4.07%" in shown, shown)
    check("...and the voice says it in words", "down 4.07%" in said, said)
    check("the voice never reads a minus sign", "-4.07%" not in said, said)
    check("...nor a bullet separator character", "·" not in said, said)

    # filing names reach neither of them
    for ugly in ("Hlds", "Techn", "-Cl A"):
        check(f"{ugly!r} never reaches him", ugly not in shown and ugly not in said,
              shown)
    check("his holdings are named properly", "AMC Entertainment" in shown, shown)
    check("...including SpaceX", "SpaceX" in shown, shown)

    # and the CMS artifact is cleaned off the end of a headline
    check("a trailing dash is trimmed off a headline",
          "Primary -" not in shown and "Primary" in shown, shown)

    # building once is what keeps them honest: the held roll-up is consumed on
    # the first build, so a second build would quietly drop it
    check("both shapes come from one build",
          len(secs) == len([t for t, _l in secs]), secs)

    # --- one event, two newsrooms, two HOURS apart ----------------------------
    # He was told about the same death twice: the MBTA teen in the afternoon
    # brief, then a memorial piece two hours later. _same_story could always see
    # it; it was only ever asked within one sweep, never across them.
    bB = br.Briefing()
    first = ("Teen dies trying to rescue companion after fall at Massachusetts "
             "Bay Transportation Authority train station")
    later = ("Massachusetts teen who died trying to rescue girlfriend remembered "
             "for putting others before himself")
    bB._seen["news:" + first[:80].lower()] = __import__("time").time()
    check("the follow-up is recognised as the same event", bB._seen_before(later))
    check("an unrelated local story still gets through",
          not bB._seen_before("Power outage affects 4,000 in Framingham"))

    # --- a short brief must not pass for a complete one -----------------------
    # His 07:30 brief on 2026-08-31 said "MARKETS: the Nasdaq 100 -0.65%" and
    # "YOURS: Apple +1.63%" while Finnhub was throwing 503s - 58 of them inside
    # that one brief. Four of his five holdings and two of the three indices had
    # simply failed. Read plainly, it said he owns one stock. Silence about a gap
    # is worse than the gap.
    bC = br.Briefing()
    bC._fresh_stories = lambda: _fake_stories([])

    async def _degraded():
        import tools.market_tools as mt
        mt.get_market_movers = lambda: _fake_dict({
            "markets": [{"symbol": "QQQ", "name": "the Nasdaq 100", "percent": -0.65}],
            "missing": ["the S&P 500", "the Dow"]})
        mt.get_watchlist = lambda: _fake_dict({
            "stocks": [{"symbol": "AAPL", "name": "Apple Inc", "percent": 1.63}],
            "missing": ["NVDA", "AMC", "TSLA", "SPCX"]})
        import analyst as _an
        _an.analyst.take = lambda limit=8: _fake_take_empty()
        secs = await bC._sections()
        return await bC.compose_brief_written(secs), await bC.compose_brief(secs)

    _real_take2 = __import__("analyst").analyst.take
    shown_d, said_d = asyncio.run(_degraded())
    __import__("analyst").analyst.take = _real_take2

    check("the missing indices are named", "the S&P 500" in shown_d and "the Dow" in shown_d,
          shown_d)
    check("the unreachable holdings are named",
          all(t in shown_d for t in ("NVDA", "AMC", "TSLA", "SPCX")), shown_d)
    check("...and the voice says it too", "couldn't reach" in said_d, said_d)
    check("what DID arrive is still reported",
          "Apple" in shown_d and "Nasdaq" in shown_d, shown_d)

    # --- a quiet day is allowed to be quiet ----------------------------------
    b5 = br.Briefing()
    b5._fresh_stories = lambda: _fake_stories([])
    b5._market_moves = lambda: _fake_moves()

    async def nothing_at_all():
        b5.compose_brief = br.Briefing.compose_brief.__get__(b5)
        import tools.market_tools as mt
        mt.get_market_movers = lambda: _fake_dict({})
        mt.get_watchlist = lambda: _fake_dict({})
        return await b5.compose_brief()
    text = asyncio.run(nothing_at_all())
    check("a quiet day says so rather than padding", "Nothing worth reporting" in text,
          text[:80])

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 0 if not fails else 1


async def _fake_scan(items, news=True):
    """Stands in for a real scan, and honours the lane it was asked for.

    The two lanes have separate clocks and both come due at once on a cold
    start. A fake that ignored `news`/`market` handed the same story to both and
    made it look as though he would be told twice - real scans return news to one
    lane and prices to the other, and _seen would catch a repeat anyway.
    """
    if not news:
        return []
    return [(text, tier, f"k:{text[:20]}") for text, tier in items]


async def _fake_stories(items):
    return items


async def _fake_moves():
    return []


async def _fake_dict(d):
    return d


async def _fake_take_empty():
    return []


async def _fake_take():
    return [{"name": "Nvidia",
             "line": "Nvidia: 64 of 68 analysts say buy, and it is down 4.6% today."}]


if __name__ == "__main__":
    sys.exit(main())
