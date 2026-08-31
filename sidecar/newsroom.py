"""Reading the article, rather than reciting the headline.

Nicholas asked, looking at two alerts on his phone: *"does that just come from
the headline or is Jarvis reading through these like news articles... and then
summarizing them using the LLM into 1-2 sentences (and I can always ask follow up
questions or for the source directly) because that's what I want him to do!"*

It was the headline, verbatim, with the source stapled on. Which is why he got:

    "Lindsay Clancy trial: Does Massachusetts have the death penalty? What
     sentence could she face if found guilty? | Hindustan Times - Hindustan Times."

A question, not an answer; the outlet's name twice; and nothing about what
actually happened. This module fixes that: fetch the piece, read it, say what
happened in a sentence or two, and keep the link so he can ask for the source.

Three things it must never do, in order of importance:

  * INVENT. The summary is grounded in the fetched text and nothing else. If the
    article cannot be read, he gets the headline, plainly, rather than a model's
    guess at what a headline implies. A confident wrong sentence about a death
    near his home is far worse than a headline he has to open himself.
  * LOSE THE ALERT. Every failure path returns something sendable. A summariser
    that swallows an emergency because a webserver was slow is a broken alarm.
  * BE SLOW ABOUT IT. Emergencies wait for nobody, so the whole thing is capped
    and falls back the moment it runs long.
"""
from __future__ import annotations

import asyncio
import logging
import re

log = logging.getLogger("jarvis.newsroom")

FETCH_CHARS = 2600           # enough for the top of a news piece; the rest is filler
SUMMARY_TOKENS = 110         # 1-2 sentences, not an essay
TOTAL_BUDGET_S = 22.0        # read + think, before we give up and send the headline

# A Google News link is not a link. It is a JavaScript interstitial that returns
# 600KB of Angular and zero characters of article, so anything pointing there is
# known-unreadable before we spend a request on it.
UNREADABLE = ("news.google.com", "google.com/url", "consent.")

PROMPT = """You are summarising one news article for someone who has just been \
alerted to it. Write 1 to 2 short sentences saying WHAT HAPPENED.

Rules:
- Use only the article text below. Never add background, context or consequences \
that are not in it.
- Plain and factual. No editorialising, no "reportedly", no hedging padding.
- If the article text does not actually say what happened, reply with exactly: \
UNCLEAR
- Do not begin with "The article" or "This story". Just say what happened.

HEADLINE: {headline}

ARTICLE:
{body}

SUMMARY:"""


def _clean_headline(headline: str, source: str = "") -> str:
    """A headline with the outlet's own branding taken off the end.

    Aggregated feeds append it - "... found guilty? | Hindustan Times" - and then
    we appended it again, so he was told the source twice in one line.
    """
    h = re.sub(r"\s+", " ", str(headline or "")).strip()
    h = re.split(r"\s+[|–—-]\s+(?=[A-Z][^|]{0,28}$)", h)[0].strip()
    if source:
        h = re.sub(r"[\s|–—-]+" + re.escape(str(source)) + r"\s*$", "", h,
                   flags=re.I).strip()
    # Feeds truncate: "Commonwealth of Massachusetts gifted Jaylen Brown a …".
    # A dangling article is worse than a shorter sentence, so the fragment goes
    # with the ellipsis.
    h = re.sub(r"\s*(?:\.\.\.|…)\s*$", "", h)
    h = re.sub(r"\s+\b(?:a|an|the|and|of|to|for|with|in|on|at|from|by)\s*$", "",
               h, flags=re.I)
    return re.sub(r"[\s\-–—|:;,]+$", "", h)


def readable(url: str) -> bool:
    u = str(url or "")
    return u.startswith(("http://", "https://")) and not any(b in u for b in UNREADABLE)


async def _read(url: str) -> str:
    from tools.web_tools import fetch_page
    page = await fetch_page(url, max_chars=FETCH_CHARS)
    if not isinstance(page, dict) or page.get("error"):
        return ""
    return str(page.get("content") or "").strip()


async def _think(headline: str, body: str) -> str:
    from llm.provider import local_llm
    out = ""
    async for ch in local_llm.stream(
            [{"role": "user", "content": PROMPT.format(headline=headline,
                                                       body=body[:FETCH_CHARS])}],
            max_tokens=SUMMARY_TOKENS, sampling={"temperature": 0.1}):
        out += ch.text
        if ch.done:
            break
    return out.strip()


def _tidy_summary(text: str) -> str:
    """Trim the model's stray scaffolding without rewriting what it said."""
    s = re.sub(r"\s+", " ", str(text or "")).strip().strip('"')
    s = re.sub(r"^(?:summary|answer)\s*:\s*", "", s, flags=re.I)
    s = re.sub(r"^(?:the article|this story|this article)\s+", "", s, flags=re.I)
    if not s or "UNCLEAR" in s.upper():
        return ""
    # Two sentences is the brief he asked for; a model that runs on gets cut.
    parts = re.split(r"(?<=[.!?])\s+", s)
    s = " ".join(parts[:2]).strip()
    return s if len(s) > 25 else ""


async def summarize(story: dict) -> dict:
    """{headline, summary, source, url, read} - what happened, and where to read it.

    `read` says whether the article was actually opened, so nothing downstream
    has to guess whether the sentence is grounded or is just the headline again.
    """
    headline = _clean_headline(story.get("headline") or story.get("title"),
                               story.get("source") or "")
    source = str(story.get("source") or "").strip()
    url = str(story.get("url") or story.get("link") or "")
    out = {"headline": headline, "summary": "", "source": source,
           "url": url, "read": False,
           # carried through so "when was that?" has an answer
           "when": str(story.get("when") or ""),
           "age_minutes": story.get("age_minutes")}
    if not readable(url):
        log.debug("no readable link for %r", headline[:60])
        return out
    try:
        body = await asyncio.wait_for(_read(url), timeout=TOTAL_BUDGET_S * 0.4)
        if len(body) < 220:                     # a paywall stub is not an article
            return out
        said = await asyncio.wait_for(_think(headline, body),
                                      timeout=TOTAL_BUDGET_S * 0.6)
    except asyncio.TimeoutError:
        log.info("summary timed out, sending the headline: %r", headline[:60])
        return out
    except Exception:
        log.debug("summary failed for %r", headline[:60], exc_info=True)
        return out
    out["summary"] = _tidy_summary(said)
    out["read"] = bool(out["summary"])
    return out


def spoken_line(s: dict) -> str:
    """One alert, as he should hear or read it.

    The summary leads when there is one, because that is the news; the headline
    leads when there is not, because inventing one would be worse.
    """
    body = s.get("summary") or s.get("headline") or ""
    src = s.get("source")
    body = body.rstrip(" .")
    return f"{body} — {src}." if src else f"{body}."


async def summarize_all(stories: list[dict], limit: int = 3) -> list[dict]:
    """Read several at once. They are independent, so they are read in parallel."""
    picked = list(stories)[:limit]
    if not picked:
        return []
    done = await asyncio.gather(*(summarize(s) for s in picked),
                                return_exceptions=True)
    out = []
    for original, result in zip(picked, done):
        if isinstance(result, dict):
            out.append(result)
        else:                                   # never drop the story itself
            out.append({"headline": _clean_headline(original.get("headline"),
                                                    original.get("source") or ""),
                        "summary": "", "source": original.get("source") or "",
                        "url": original.get("url") or "", "read": False})
    return out
