"""Web tools: page fetching/extraction and the visible research pipeline.

fetch_page uses httpx + trafilatura (fast, bundle-friendly). The tool interface
is deliberately engine-agnostic so a Playwright-backed implementation can slot
in for JS-heavy/interactive work in the interactive-browser phase.
"""
from __future__ import annotations

import asyncio
import logging

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


async def show_images(query: str, count: int = 8) -> dict:
    """Find pictures and display them in the JARVIS interface."""
    from search_brave_web import brave_web
    from tools.query_clean import clean_image_query
    if not brave_web.available:
        return {"error": "image search needs the Brave browser installed"}
    # "show me 5 images of spiderman" -> query "spiderman", count 5 — whether the
    # brain or the LLM built this call, the engine sees keywords only.
    query, spoken_count = clean_image_query(query)
    if spoken_count:
        count = spoken_count
    await bus.emit("web", stage="images_searching", query=query)
    try:
        imgs = await brave_web.images(query, max(1, min(12, count)))
    except Exception as e:
        return {"error": f"image search failed: {e}"}
    if not imgs:
        return {"error": "no images found"}
    await bus.emit("images", query=query, images=imgs)
    return {"shown": len(imgs), "query": query,
            "instruction": "The pictures are now displayed on screen. Say so briefly."}


def register_all() -> None:
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
