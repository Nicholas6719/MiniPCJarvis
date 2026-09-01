"""What would actually reach him, from the feeds as they are right now.

He narrowed the news three times in two days, ending at: *"Only have him tell me
about emergencies from now on. I was getting too many news feeds. I only want to
hear about the emergencies and the local ones. Only tell me about national ones
if it's extremely important."*

Unit tests prove the classifier agrees with the rules. They cannot prove the
rules produce a quiet phone, because that depends on what the world is doing.
This runs the REAL sweep briefing.py runs, classifies every story the way the
live path does, and prints both halves - what gets through, and what was
dropped and why. If something in the dropped column looks like it should have
reached him, or anything in the kept column looks like noise, that is the bug.

Needs the network. Nothing else - no app, no audio, no LLM.
Run: python tests/news_emergencies_live.py
"""
import asyncio
import os
import sys
import tempfile
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "news.db"))


async def sweep() -> list[dict]:
    """Exactly what briefing._scan collects, in the same order, with the same
    provenance flag - a story off a local desk IS local whatever it says."""
    from tools.news_tools import get_breaking_news, get_news
    stories: list[dict] = []
    try:
        national = await get_breaking_news(count=8)
        stories += (national.get("items") or national.get("latest") or [])
    except Exception as e:
        print(f"  (national sweep failed: {e})")
    for topic in ("local", "towns"):
        try:
            local = await get_news(topic=topic, count=8)
            for it in (local.get("items") or []):
                it["_local_feed"] = True
            stories += (local.get("items") or [])
        except Exception as e:
            print(f"  ({topic} sweep failed: {e})")
    # the wider wires, to prove they are being dropped rather than never asked
    for topic in ("top", "world", "us", "business", "technology", "sports"):
        try:
            wide = await get_news(topic=topic, count=6)
            stories += (wide.get("items") or [])
        except Exception as e:
            print(f"  ({topic} sweep failed: {e})")
    return stories


def main() -> int:
    from config import config
    from significance import ALERT, NONE, NOTABLE, URGENT, classify_news

    mode = config.get("briefing", "news_mode", default="?")
    scope = config.get("briefing", "news_scope", default="?")
    every = config.get("briefing", "emergency_minutes", default="?")
    print(f"news_mode={mode}   news_scope={scope}   checked every {every} min\n")

    stories = asyncio.run(sweep())
    if not stories:
        print("FAIL  no stories came back at all - the feeds are unreachable")
        return 1
    print(f"swept {len(stories)} stories from his live feeds\n")

    buckets: dict[str, list] = {URGENT: [], ALERT: [], NOTABLE: [], NONE: []}
    reasons: Counter = Counter()
    for s in stories:
        tier, why = classify_news(s)
        buckets.setdefault(tier, []).append((s, why))
        reasons[why] += 1

    reaches = buckets[URGENT] + buckets[ALERT]
    print("=" * 72)
    print(f"REACHES HIM  ({len(reaches)} of {len(stories)})")
    print("=" * 72)
    if not reaches:
        print("  nothing - no emergencies in the feeds right now.")
    for s, why in reaches:
        tier = URGENT if (s, why) in buckets[URGENT] else ALERT
        local = "local" if s.get("_local_feed") else "wire"
        print(f"  [{tier:6s}] ({local}) {s['headline'][:88]}")
        print(f"           {s.get('source', '?')} - {why}")

    print()
    print("=" * 72)
    print(f"SILENT  ({len(buckets[NONE]) + len(buckets[NOTABLE])} of {len(stories)})")
    print("=" * 72)
    for why, n in reasons.most_common():
        if why in ("the whole country needs to know this", "something dangerous in one of his towns",
                   "something dangerous close to home", "violence in the state, but not his town",
                   "a serious incident, wherever it is", "something still unfolding, wherever it is",
                   "somebody died in one of his towns", "somebody died close to home",
                   "many people have died", "national news of consequence",
                   "his own town, and it changes his day"):
            continue
        print(f"  {n:3d}  {why}")

    print("\n  a sample of what he no longer sees:")
    for s, why in (buckets[NONE] + buckets[NOTABLE])[:8]:
        print(f"    - {s['headline'][:78]}")

    # --- the assertions -----------------------------------------------------
    fails = []
    if buckets[NOTABLE]:
        fails.append(f"{len(buckets[NOTABLE])} stories are still NOTABLE - "
                     "emergencies-only should have collapsed those to nothing")

    # Anything reaching him from a national wire has to have come through the
    # one narrow door, not through a hazard rule that ignored distance.
    for s, why in reaches:
        if not s.get("_local_feed") and why != "the whole country needs to know this":
            from significance import is_local
            near, _ = is_local(s)
            if not near:
                fails.append(f"a non-local story got through on '{why}': {s['headline'][:60]}")

    share = len(reaches) / len(stories)
    if share > 0.25:
        fails.append(f"{share:.0%} of the feed is reaching him - that is a news service again")

    print()
    for f in fails:
        print(f"FAIL  {f}")
    if not fails:
        print(f"ALL PASS  {len(reaches)}/{len(stories)} got through "
              f"({share:.0%}); the rest was silent.")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
