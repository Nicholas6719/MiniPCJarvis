"""Headlines, from RSS.

Deliberately keyless: publisher feeds are free, need no account, and carry
breaking items within seconds of publication — which is exactly the property a
news feature needs. A paid aggregator can slot in behind the same tools later
without changing anything a user says.

REALM 2 throughout: news is the definition of changeable. Nothing here is ever
cached as a fact, and every answer carries how old the item is.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import re
import xml.etree.ElementTree as ET

import httpx

from config import config
from tools.registry import Risk, Tool, registry

log = logging.getLogger("jarvis.news")

# Wire services and desks, chosen for breadth and for publishing fast.
FEEDS: dict[str, list[tuple[str, str]]] = {
    # Verified reachable 2026-08-27. AP, Reuters and the Washington Post all
    # refuse or 404 their public RSS now, so they are deliberately absent rather
    # than left in to fail silently — a dead feed makes the mix look like one
    # outlet's opinion. Re-test with the loop in tests/test_news.py before adding.
    "top": [
        ("BBC", "https://feeds.bbci.co.uk/news/rss.xml"),
        ("NPR", "https://feeds.npr.org/1001/rss.xml"),
        ("CBS", "https://www.cbsnews.com/latest/rss/main"),
        ("Sky News", "https://feeds.skynews.com/feeds/rss/home.xml"),
    ],
    "world": [("BBC World", "https://feeds.bbci.co.uk/news/world/rss.xml"),
              ("Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml"),
              ("NPR World", "https://feeds.npr.org/1004/rss.xml")],
    "us": [("NPR National", "https://feeds.npr.org/1003/rss.xml"),
           ("CBS US", "https://www.cbsnews.com/latest/rss/us")],
    "business": [("BBC Business", "https://feeds.bbci.co.uk/news/business/rss.xml"),
                 ("CNBC", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
                 ("NPR Business", "https://feeds.npr.org/1006/rss.xml")],
    "technology": [("BBC Tech", "https://feeds.bbci.co.uk/news/technology/rss.xml"),
                   ("Ars Technica", "https://feeds.arstechnica.com/arstechnica/index"),
                   ("The Verge", "https://www.theverge.com/rss/index.xml")],
    "science": [("BBC Science", "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml"),
                ("NASA", "https://www.nasa.gov/rss/dyn/breaking_news.rss")],
    "sports": [("BBC Sport", "https://feeds.bbci.co.uk/sport/rss.xml"),
               ("ESPN", "https://www.espn.com/espn/rss/news")],
    "local": [("WCVB Boston", "https://www.wcvb.com/topstories-rss"),
              ("Boston.com", "https://www.boston.com/feed/")],
}

_TAG = re.compile(r"<[^>]+>")
_NOISE = {"after", "with", "from", "that", "this", "they", "them", "have",
          "will", "over", "into", "than", "amid", "says", "said", "more"}
_UA = {"User-Agent": "JARVIS-personal-assistant/0.3 (local desktop app)"}


def _clean(s: str | None) -> str:
    return re.sub(r"\s+", " ", _TAG.sub(" ", s or "")).strip()


def _parse_date(raw: str | None) -> dt.datetime | None:
    if not raw:
        return None
    from email.utils import parsedate_to_datetime
    try:
        d = parsedate_to_datetime(raw)
        return d.replace(tzinfo=None) - (d.utcoffset() or dt.timedelta()) if d.tzinfo else d
    except Exception:
        try:                                    # Atom: 2026-08-27T11:04:00Z
            return dt.datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            return None


def _age(when: dt.datetime | None) -> str:
    if not when:
        return ""
    mins = max(0, int((dt.datetime.utcnow() - when).total_seconds() // 60))
    if mins < 60:
        return f"{mins} min ago"
    if mins < 1440:
        return f"{mins // 60} h ago"
    return f"{mins // 1440} d ago"


async def _fetch_feed(client: httpx.AsyncClient, source: str, url: str) -> list[dict]:
    try:
        r = await client.get(url, headers=_UA, timeout=8, follow_redirects=True)
        r.raise_for_status()
        root = ET.fromstring(r.content)
    except Exception as e:
        log.debug("feed %s failed: %s", source, e)
        return []
    out = []
    # RSS <item> and Atom <entry> in one pass
    for node in root.iter():
        tag = node.tag.rsplit("}", 1)[-1]
        if tag not in ("item", "entry"):
            continue
        get = lambda n: next((c.text for c in node if c.tag.rsplit("}", 1)[-1] == n), None)  # noqa: E731
        link = get("link")
        if not link:
            link = next((c.get("href") for c in node
                         if c.tag.rsplit("}", 1)[-1] == "link" and c.get("href")), None)
        when = _parse_date(get("pubDate") or get("published") or get("updated"))
        title = _clean(get("title"))
        if not title:
            continue
        out.append({"headline": title, "source": source, "url": link,
                    "summary": _clean(get("description") or get("summary"))[:220],
                    "when": _age(when), "_ts": when or dt.datetime.min})
    return out


async def get_news(topic: str = "top", count: int = 5, query: str = "") -> dict:
    """Latest headlines, newest first, merged across that topic's feeds."""
    topic = (topic or "top").strip().lower()
    if topic in ("headlines", "news", "general", "breaking"):
        topic = "top"
    if topic in ("tech",):
        topic = "technology"
    feeds = FEEDS.get(topic)
    if feeds is None:
        return {"error": f"I don't have a {topic} feed. I have: " + ", ".join(FEEDS)}
    async with httpx.AsyncClient() as c:
        batches = await asyncio.gather(*(_fetch_feed(c, s, u) for s, u in feeds))
    items = [i for b in batches for i in b]
    if query:
        words = [w for w in re.split(r"\s+", query.lower()) if len(w) > 2]
        items = [i for i in items
                 if all(w in (i["headline"] + " " + i["summary"]).lower() for w in words)]
    if not items:
        return {"error": f"nothing came back for {query or topic} just now."}
    items.sort(key=lambda i: i["_ts"], reverse=True)
    # The same story reaches us worded differently ("Celtic and Rangers ordered to
    # play..." vs "Celtic & Rangers to play..."), so a prefix key does not dedupe
    # it — compare the significant words instead.
    kept_words: list[set] = []
    unique = []
    for i in items:
        words = {w for w in re.sub(r"[^a-z0-9 ]", " ", i["headline"].lower()).split()
                 if len(w) > 3 and w not in _NOISE}
        if not words:
            continue
        if any(len(words & prev) / max(1, min(len(words), len(prev))) >= 0.6
               for prev in kept_words):
            continue
        kept_words.append(words)
        i.pop("_ts", None)
        unique.append(i)
    return {"topic": topic, "query": query or None, "count": len(unique[:count]),
            "items": unique[:max(1, min(10, count))]}


async def get_breaking_news(count: int = 4) -> dict:
    """Only what broke in the last few hours, across the wires."""
    res = await get_news("top", count=10)
    if "error" in res:
        return res
    fresh = [i for i in res["items"]
             if i["when"].endswith("min ago") or i["when"] in ("1 h ago", "2 h ago", "3 h ago")]
    if not fresh:
        return {"nothing_breaking": True, "latest": res["items"][:2]}
    return {"count": len(fresh[:count]), "items": fresh[:count]}


def register_all() -> None:
    if not config.get("news", "enabled", default=True):
        return
    registry.register(Tool(
        name="get_news",
        description="Latest news headlines by topic (top, world, us, business, technology, "
                    "science, sports, local) or filtered by keyword. Live RSS from the BBC, "
                    "NPR, CBS, Sky, Al Jazeera, CNBC, Ars Technica and others.",
        parameters={"type": "object", "properties": {
            "topic": {"type": "string",
                      "enum": ["top", "world", "us", "business", "technology",
                               "science", "sports", "local"]},
            "count": {"type": "integer", "minimum": 1, "maximum": 10},
            "query": {"type": "string", "description": "optional keywords to filter on"}},
            "required": []},
        risk=Risk.SAFE, handler=get_news, timeout=25))
    registry.register(Tool(
        name="get_breaking_news",
        description="Only stories that broke in the last few hours.",
        parameters={"type": "object", "properties": {
            "count": {"type": "integer", "minimum": 1, "maximum": 8}}, "required": []},
        risk=Risk.SAFE, handler=get_breaking_news, timeout=25))
