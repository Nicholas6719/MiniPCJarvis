"""Reading the article instead of reciting the headline.

Nicholas asked for this directly: *"is Jarvis reading through these like news
articles... and then summarizing them using the LLM into 1-2 sentences (and I can
always ask follow up questions or for the source directly) because that's what I
want him to do!"*

Two properties matter more than the summary being good, and both are checked
here, because both fail silently:

  * an alert is NEVER lost. A summariser that swallows an emergency because a
    webserver hung is a broken alarm, so every failure path still returns
    something sendable.
  * nothing is INVENTED. If the article cannot be read he gets the headline, not
    a model's guess at what a headline implies.

Offline: no network, no LLM, no key. The article fetch and the model are both
stubbed, which is the only way to test the failure paths on purpose.
Run: python tests/test_newsroom.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import newsroom  # noqa: E402
from newsroom import _clean_headline, _tidy_summary, readable, spoken_line  # noqa: E402

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


ARTICLE = ("BOSTON (WHDH) - A 19-year-old man is dead and a woman is in critical "
           "condition after falling onto the tracks at the Forest Hills MBTA "
           "Station on Saturday afternoon, officials said. Transit police "
           "responded shortly before 2 p.m. and service was suspended for about "
           "an hour while crews worked at the scene.")


def stub(*, body=ARTICLE, said="A man died and a woman was critically hurt after "
                                "falling onto the tracks at Forest Hills Station.",
         read_raises=None, think_raises=None):
    async def _read(_url):
        if read_raises:
            raise read_raises
        return body

    async def _think(_h, _b):
        if think_raises:
            raise think_raises
        return said
    newsroom._read, newsroom._think = _read, _think


def main() -> int:
    story = {"headline": "'Horrific tragedy': Man dead, woman critical | WHDH",
             "url": "https://whdh.com/news/horrific-tragedy/", "source": "WHDH"}

    # --- the thing he asked for ----------------------------------------------
    stub()
    said = asyncio.run(newsroom.summarize(story))
    check("it reads the article", said["read"], said)
    check("...and says what happened, not what was printed",
          "died" in said["summary"] and "Horrific" not in said["summary"],
          said["summary"])
    check("...keeping the link so he can ask for the source",
          said["url"].startswith("https://whdh.com"), said)
    check("the summary leads the message",
          spoken_line(said).startswith("A man died"), spoken_line(said))
    check("...and the source is still named", spoken_line(said).endswith("— WHDH."),
          spoken_line(said))

    # --- never lose the alert -------------------------------------------------
    stub(read_raises=OSError("connection reset"))
    said = asyncio.run(newsroom.summarize(story))
    check("a dead webserver does not lose the alert", bool(spoken_line(said).strip()))
    check("...and it falls back to the headline, not silence",
          not said["read"] and "Man dead" in spoken_line(said), spoken_line(said))

    stub(think_raises=RuntimeError("llm offline"))
    said = asyncio.run(newsroom.summarize(story))
    check("an LLM that is down does not lose the alert",
          not said["read"] and "Man dead" in spoken_line(said), spoken_line(said))

    stub(body="Subscribe to continue reading.")
    said = asyncio.run(newsroom.summarize(story))
    check("a paywall stub is not treated as an article", not said["read"], said)

    # --- never invent ---------------------------------------------------------
    stub(said="UNCLEAR")
    said = asyncio.run(newsroom.summarize(story))
    check("a model that cannot tell says nothing rather than guessing",
          not said["read"] and said["summary"] == "", said)

    stub(said="Yes.")
    said = asyncio.run(newsroom.summarize(story))
    check("a one-word answer is not a summary", said["summary"] == "", said)

    # an unreadable link is known before a request is spent on it
    google = dict(story, url="https://news.google.com/rss/articles/CBMiigJBVV95cUx")
    check("a Google News redirect is known to be unreadable",
          not readable(google["url"]))
    stub()
    said = asyncio.run(newsroom.summarize(google))
    check("...so it is not fetched at all", not said["read"], said)
    check("...and he still gets the headline", "Man dead" in spoken_line(said))

    # --- headlines as they should read ---------------------------------------
    check("the outlet's own suffix is removed",
          _clean_headline("Does Massachusetts have the death penalty? | Hindustan "
                          "Times") == "Does Massachusetts have the death penalty?",
          _clean_headline("Does Massachusetts have the death penalty? | Hindustan Times"))
    check("...and is not named twice",
          _clean_headline("Man admits to killing woman - WHDH", "WHDH")
          == "Man admits to killing woman",
          _clean_headline("Man admits to killing woman - WHDH", "WHDH"))
    check("a CMS trailing dash goes",
          _clean_headline("Markey Holds Lead Over Moulton in Primary -")
          == "Markey Holds Lead Over Moulton in Primary")
    check("a truncated headline does not end mid-phrase",
          _clean_headline("Commonwealth of Massachusetts gifted Jaylen Brown a …")
          == "Commonwealth of Massachusetts gifted Jaylen Brown",
          _clean_headline("Commonwealth of Massachusetts gifted Jaylen Brown a …"))
    check("a normal headline is left alone",
          _clean_headline("Markey trounces Moulton in Massachusetts Senate primary poll")
          == "Markey trounces Moulton in Massachusetts Senate primary poll")

    # --- two sentences is the brief he asked for ------------------------------
    long = ("One thing happened. Then a second thing happened. Then a third "
            "thing happened. And a fourth.")
    check("a model that runs on is cut to two sentences",
          _tidy_summary(long) == "One thing happened. Then a second thing happened.",
          _tidy_summary(long))
    check("scaffolding is trimmed",
          _tidy_summary("Summary: The bridge will be closed until 3:30 p.m today.")
          .startswith("The bridge"),
          _tidy_summary("Summary: The bridge will be closed until 3:30 p.m today."))

    # --- several at once, and one bad apple does not spoil them ---------------
    stub()
    many = asyncio.run(newsroom.summarize_all([story, dict(story), dict(story)]))
    check("three articles come back three summaries", len(many) == 3, len(many))
    check("it stops at the limit it was given",
          len(asyncio.run(newsroom.summarize_all([story] * 9, limit=3))) == 3)

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
