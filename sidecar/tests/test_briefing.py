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
    b.scan = lambda: _fake_scan([("Active shooter in Natick", "urgent")])
    b._last_watch = 0
    asyncio.run(b._maybe_watch())
    check("an alert at 3 a.m. IS sent", len(rec.sent) == 1 and rec.sent[0][0] == "urgent",
          rec.sent)

    # --- the watch does not mistake "already out there" for "just happened" ---
    rec.sent.clear()
    b2 = br.Briefing()
    b2.scan = lambda: _fake_scan([("Something serious", "alert")])
    b2._last_watch = 0
    asyncio.run(b2._maybe_watch())
    check("the first pass after a restart sends nothing", rec.sent == [], rec.sent)
    check("...and marks itself primed", b2._primed)
    b2._last_watch = 0
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


async def _fake_scan(items):
    return [(text, tier, f"k:{text[:20]}") for text, tier in items]


async def _fake_stories(items):
    return items


async def _fake_moves():
    return []


async def _fake_dict(d):
    return d


if __name__ == "__main__":
    sys.exit(main())
