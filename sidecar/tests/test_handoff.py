"""What "it" means, and moving it somewhere else.

"Send it to my phone." "Give me the article." Neither names its subject, because
a person would not — it is whatever he was just told. So the link behind a
headline has to survive from the tool that fetched it to the sentence he hears,
and every source shapes its rows differently: search results carry `url` and
`title`, news carries `url` and `headline`, company news the same again.

Run: python tests/test_handoff.py
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "gate.db"))
from lastseen import LastSeen  # noqa: E402

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


def main() -> int:
    # --- every source shapes its rows differently -----------------------------
    ls = LastSeen()
    ls.note_result({"items": [
        {"headline": "Fatal MBTA incident", "url": "https://wcvb.com/a", "source": "WCVB"},
        {"headline": "Clouds increase Sunday", "url": "https://wcvb.com/b", "source": "WCVB"}]})
    check("news rows give up their links", len(ls.links) == 2, ls.links)
    check("...with the headline as the title",
          ls.links[0]["title"] == "Fatal MBTA incident", ls.links[0])

    ls = LastSeen()
    ls.note_result({"results": [
        {"title": "A search result", "url": "https://example.com/1", "snippet": "..."}]})
    check("search results give up theirs too",
          len(ls.links) == 1 and ls.links[0]["title"] == "A search result", ls.links)

    # nested, because company news arrives inside another object
    ls = LastSeen()
    ls.note_result({"symbol": "TSLA", "news": {"items": [
        {"headline": "Tesla recalls cars", "link": "https://reuters.com/x"}]}})
    check("a link nested two levels down is still found", len(ls.links) == 1, ls.links)

    # --- and nothing that is not a link ---------------------------------------
    ls = LastSeen()
    ls.note_result({"price": 319.7, "name": "Apple Inc", "symbol": "AAPL"})
    check("a stock quote has no article to open", ls.links == [], ls.links)
    ls.note_result({"path": "C:/Users/x/file.txt", "url": "file:///C:/x"})
    check("a local file path is not a web link", ls.links == [], ls.links)

    # --- it does not hoard ----------------------------------------------------
    ls = LastSeen()
    ls.note_result({"items": [{"headline": f"h{i}", "url": f"https://x/{i}"}
                              for i in range(50)]})
    check("it keeps a handful, not fifty", len(ls.links) <= 8, len(ls.links))

    # --- "it" stops meaning anything after a while ----------------------------
    ls = LastSeen()
    ls.note_reply("Here is the news.")
    check("just said: not stale", not ls.stale)
    ls.at -= 3600
    check("an hour later, 'it' means nothing", ls.stale)

    # --- a fresh answer replaces the old subject ------------------------------
    ls = LastSeen()
    ls.note_result({"items": [{"headline": "old", "url": "https://old/1"}]})
    ls.note_result({"items": [{"headline": "new", "url": "https://new/1"}]})
    check("the newest thing he was told is the subject",
          len(ls.links) == 1 and "new" in ls.links[0]["url"], ls.links)

    # --- an empty reply must not wipe what he was told ------------------------
    ls = LastSeen()
    ls.note_reply("Something worth sending.")
    when = ls.at
    ls.note_reply("   ")
    check("an empty reply changes nothing",
          ls.text == "Something worth sending." and ls.at == when, ls.text)

    # --- "when was that?" --------------------------------------------------
    # He asked it straight after a news alert and was told "That question came up
    # earlier today." The router had matched it to PROVENANCE - which answers
    # "when did YOU learn this" - so "that" resolved to his own question rather
    # than the story he had just been sent.
    import os as _os, tempfile as _tf
    _os.environ.setdefault("JARVIS_DB", _os.path.join(_tf.mkdtemp(), "h.db"))
    from lastseen import last_seen as _ls
    from brain.skills import say_story_time

    _ls.clear()
    _ls.note_result({"items": [{
        "headline": "Grand Canyon flash floods leave more than 20 missing",
        "url": "https://www.cbsnews.com/x", "source": "CBS", "age_minutes": 126}]})
    said = say_story_time({}, {})
    check("it answers about the STORY, not about the question",
          "Grand Canyon" in said, said)
    check("...with how long ago it was", "2 hours ago" in said, said)
    check("...and never claims the question came up earlier",
          "question" not in said.lower(), said)

    # publication time is not event time, and it must not pretend otherwise
    check("it says PUBLISHED, not that the event happened then",
          "published" in said.lower(), said)

    _ls.links[0]["age_minutes"] = 8
    check("minutes are read as minutes", "8 minutes ago" in say_story_time({}, {}),
          say_story_time({}, {}))
    _ls.links[0]["age_minutes"] = 4300
    check("days are read as days", "3 days ago" in say_story_time({}, {}),
          say_story_time({}, {}))

    # no time at all: admit it rather than invent one
    _ls.links[0]["age_minutes"] = None
    _ls.links[0]["when"] = ""
    check("an unknown time is admitted, not guessed",
          "don't have a time" in say_story_time({}, {}), say_story_time({}, {}))

    # and once "it" has gone stale, provenance answers instead
    _ls.clear()
    check("a stale subject falls back rather than inventing",
          "Grand Canyon" not in say_story_time({}, {}), say_story_time({}, {}))

    # --- malformed results must not raise -------------------------------------
    for junk in (None, "a string", 42, [], {"items": None}, {"items": ["not a dict"]}):
        try:
            LastSeen().note_result(junk)
            check(f"{str(junk)[:22]!r} is survived", True)
        except Exception as e:                        # noqa: BLE001
            check(f"{str(junk)[:22]!r} is survived", False, f"{type(e).__name__}: {e}")

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
