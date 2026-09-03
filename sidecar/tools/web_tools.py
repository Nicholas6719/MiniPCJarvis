"""Web tools: page fetching/extraction and the visible research pipeline.

fetch_page uses httpx + trafilatura (fast, bundle-friendly). The tool interface
is deliberately engine-agnostic so a Playwright-backed implementation can slot
in for JS-heavy/interactive work in the interactive-browser phase.
"""
from __future__ import annotations

import asyncio
import logging
import time

import httpx

from events import bus
from tools.builtin import web_search
from tools.registry import Risk, Tool, registry

log = logging.getLogger("jarvis.tools.web")

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
_HEADERS = {
    "User-Agent": _UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


async def fetch_page(url: str, max_chars: int = 3500) -> dict:
    import trafilatura
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    headers = dict(_HEADERS)
    if "wikipedia.org" in url or "wikimedia.org" in url:
        # Wikimedia's policy blocks browser-imitating scripts; it asks for a
        # descriptive UA instead.
        headers["User-Agent"] = "JARVIS-personal-assistant/0.3 (local desktop app)"
    try:
        async with httpx.AsyncClient(
                http2=True, timeout=12, follow_redirects=True,
                headers=headers) as c:
            r = await c.get(url)
            r.raise_for_status()
            html = r.text
    except httpx.HTTPStatusError as e:
        return {"url": url, "error": f"HTTP {e.response.status_code}"}
    except Exception as e:
        return {"url": url, "error": f"fetch failed: {e}"}

    def _extract() -> tuple[str | None, str | None]:
        text = trafilatura.extract(html, include_comments=False,
                                   include_tables=True)
        meta = trafilatura.extract_metadata(html)
        title = meta.title if meta else None
        return title, text

    title, text = await asyncio.to_thread(_extract)
    if not text:
        return {"url": url, "title": title, "error": "no readable content extracted"}
    return {"url": url, "title": title,
            "content": text[:max_chars], "truncated": len(text) > max_chars}


async def research(query: str, num_sources: int = 4) -> dict:
    """Search → fetch top sources in parallel → return extracts + citations.

    The main model synthesizes the answer from these extracts in its follow-up
    round; every source is emitted to the UI so the process stays visible.
    """
    num_sources = max(2, min(6, num_sources))
    # Clean HERE, not just in web_search: the store keys the browser stage by
    # query, so research events and web events must carry the same string.
    from tools.query_clean import clean_search_query
    query = clean_search_query(query)
    await bus.emit("research", stage="searching", query=query)
    search = await web_search(query, count=num_sources + 2)
    if "error" in search:
        return search
    results = search.get("results") or []
    if not results:
        return {"query": query, "error": "no search results"}

    urls = [r["url"] for r in results[:num_sources]]
    await bus.emit("research", stage="reading", query=query,
                   sources=[{"url": r["url"], "title": r["title"]}
                            for r in results[:num_sources]])
    async def _read(u):
        # "opening" before, "read" after: the HUD's action marker shows the decision
        # (which result is being opened right now), not just the outcome.
        await bus.emit("web", stage="opening", query=query, url=u)
        page = await fetch_page(u, max_chars=2500)
        await bus.emit("web", stage="read", query=query, url=u,
                       ok=bool(page.get("content")), title=page.get("title"))
        return page
    pages = await asyncio.gather(*(_read(u) for u in urls))

    sources = []
    for r, page in zip(results[:num_sources], pages):
        entry = {"title": page.get("title") or r["title"], "url": r["url"]}
        if page.get("content"):
            entry["extract"] = page["content"]
        else:
            entry["extract"] = r.get("snippet") or ""
            entry["note"] = page.get("error", "used search snippet only")
        sources.append(entry)

    ok = sum(1 for s in sources if not s.get("note"))
    await bus.emit("research", stage="done", query=query, fetched=ok,
                   total=len(sources))
    if ok:
        from brain.facts import record_evidence
        record_evidence(query, [{"url": s["url"], "title": s.get("title", "")}
                                for s in sources if not s.get("note")], "research")
    return {
        "query": query,
        "sources": sources,
        "instruction": ("Synthesize an answer from these sources. Speak a concise "
                        "conclusion; mention source names naturally, not URLs."),
    }


# The subject of the last picture search, so "show me three more" has something
# to be three more OF. Deliberately not tied to the conversation window: the
# window decides whether he needs the wake word, this decides what JARVIS
# remembers, and they are different questions.
_last_subject: dict = {"q": ""}
# The pictures currently on screen, in the order they are laid out — which is the
# order he counts them in: four across the top, then back to the left and down.
# Kept so "focus on number six" and "3D print number three" have something to
# point AT; the grid lives in the HUD and the sidecar could not see it.
_last_images: list = []


# When a set of pictures was last put on screen. Read by the
# orchestrator to keep the conversation window open while he looks.
last_images_at = 0.0


async def show_images(query: str, count: int = 8) -> dict:
    """Find pictures and display them in the JARVIS interface."""
    from search_brave_web import brave_web
    from tools.query_clean import clean_image_query
    if not brave_web.available:
        return {"error": "image search needs the Brave browser installed"}
    # "show me 5 images of spiderman" -> query "spiderman", count 5 — whether the
    # brain or the LLM built this call, the engine sees keywords only.
    from tools.query_clean import more_request
    query, spoken_count = clean_image_query(query)
    if spoken_count:
        count = spoken_count
    # "SHOW ME THREE MORE" MEANS THREE MORE OF WHAT WE WERE LOOKING AT.
    #
    # His requirement, and it is about memory rather than about the follow-up
    # window: "if I say 'Show me two images of Iron Man' ... then the
    # conversation window closes, and then I use the wake word and say 'Show me
    # three more images' — it should know that we're talking about Iron Man."
    #
    # Fixed HERE rather than in the brain's slot extractor because both roads
    # arrive here: the reflex, and the model writing its own query. It had
    # written "three more" and the engine was duly asked for pictures of the
    # words "three more".
    more, more_count = more_request(query)
    if more:
        if not _last_subject["q"]:
            return {"error": "more of what, sir?"}
        query = _last_subject["q"]
        if more_count:
            count = more_count
    await bus.emit("web", stage="images_searching", query=query)
    try:
        imgs = await brave_web.images(query, max(1, min(12, count)))
    except Exception as e:
        return {"error": f"image search failed: {e}"}
    if not imgs:
        return {"error": "no images found"}
    _last_subject["q"] = query
    _last_images.clear()
    _last_images.extend(imgs)
    # WHEN THEY WENT UP, so the conversation window can stay open while he is
    # reading them. Eight seconds is not long enough to look at eight pictures
    # and say which one you meant.
    global last_images_at
    last_images_at = time.time()
    await bus.emit("images", query=query, images=imgs)
    return {"shown": len(imgs), "query": query,
            "instruction": "The pictures are now displayed on screen. Say so briefly."}


async def focus_image(number: int = 0, first: int = 0, last: int = 0) -> dict:
    """Enlarge one of the pictures on screen, or narrow the grid to a range.

    HIS NUMBERING, which is simply how the grid is laid out: four across the
    top, then back to the left and down — so picture five sits under picture
    one. He counts from ONE; the HUD indexes from zero.

    This exists as a TOOL as well as a reflex because the reflex cannot cover
    every phrasing, and without it the model had no way to do this at all: the
    `ui` skill carries no tool, it only emits an event. "Focus on number 6" fell
    through to the model, and the model had nothing to reach for.

    It also returns the picture's URL, so "focus on number three and give me a
    3D printout of that" can be two calls instead of a dead end.
    """
    total = len(_last_images)
    if not total:
        return {"error": "there are no pictures on screen, sir"}
    if first and last:
        lo, hi = max(1, min(first, last)), min(total, max(first, last))
        await bus.emit("ui", action="range", **{"from": lo - 1, "to": hi - 1})
        return {"showing": f"{lo} to {hi}", "of": total}
    n = int(number or 0)
    if not 1 <= n <= total:
        return {"error": f"there are only {total} pictures, sir"}
    img = _last_images[n - 1] or {}
    await bus.emit("ui", action="focus", index=n - 1)
    return {"focused": n, "of": total, "url": img.get("src", ""),
            "page": img.get("page", ""),
            "instruction": "It is now shown large. If he asked for anything to be "
                           "made FROM it, pass this url as image_path."}


def register_all() -> None:
    registry.register(Tool(
        name="focus_image",
        description="Enlarge one of the pictures currently on screen by its "
                    "number (he counts from 1, left to right then down), or "
                    "narrow the grid to a range with first/last. Returns the "
                    "picture's url so it can be used to make something.",
        parameters={"type": "object", "properties": {
            "number": {"type": "integer", "minimum": 1,
                       "description": "which picture, counting from 1"},
            "first": {"type": "integer", "minimum": 1},
            "last": {"type": "integer", "minimum": 1}},
            "required": []},
        risk=Risk.SAFE, handler=focus_image, timeout=15))
    registry.register(Tool(
        name="show_images",
        description="Find pictures of something and display them on the JARVIS "
                    "screen. Use for 'show me a picture/photo/image of X'.",
        parameters={"type": "object", "properties": {
            "query": {"type": "string"},
            "count": {"type": "integer", "minimum": 1, "maximum": 12}},
            "required": ["query"]},
        risk=Risk.LOW, handler=show_images, timeout=60))
    registry.register(Tool(
        name="fetch_page",
        description="Fetch a web page and return its readable text content.",
        parameters={"type": "object", "properties": {
            "url": {"type": "string"}}, "required": ["url"]},
        risk=Risk.LOW, handler=fetch_page, timeout=20))
    registry.register(Tool(
        name="research",
        description="Research a question on the web: searches, reads the top "
                    "sources, and returns extracts with citations. Use for any "
                    "question needing current or in-depth information.",
        parameters={"type": "object", "properties": {
            "query": {"type": "string"},
            "num_sources": {"type": "integer", "minimum": 2, "maximum": 6}},
            "required": ["query"]},
        risk=Risk.LOW, handler=research, timeout=120))
