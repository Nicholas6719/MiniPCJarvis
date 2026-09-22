"""Find something ON a named site: open it where he can see it, read him the top.

Last night (2026-09-21): "find me a tower fan and heater on Amazon" ran a web
search, loaded an Amazon page into JARVIS's own hidden browser, and read a
price off a snippet; "can you show me?" opened another hidden page and said
"now open, sir" to a dark room. His rule, the next day: naming the site IS
the instruction to show it. So find_on_site does both at once:

  1. the site's own search for what he asked, in HIS browser (open_url);
  2. the same page read in the hidden browser, so he hears the top results
     with prices without touching the mouse.

If the hidden read fails (a bot wall, a slow page) he still gets the page in
front of him and an honest sentence, never a made-up product.
"""
from __future__ import annotations

import asyncio
import logging
import re
import urllib.parse

from tools.registry import Risk, Tool, registry

log = logging.getLogger("jarvis.tools.site")

SEARCH = {
    "amazon":        "https://www.amazon.com/s?k={q}",
    "ebay":          "https://www.ebay.com/sch/i.html?_nkw={q}",
    "bestbuy":       "https://www.bestbuy.com/site/searchpage.jsp?st={q}",
    "walmart":       "https://www.walmart.com/search?q={q}",
    "target":        "https://www.target.com/s?searchTerm={q}",
    "newegg":        "https://www.newegg.com/p/pl?d={q}",
    "microcenter":   "https://www.microcenter.com/search/search_results.aspx?Ntt={q}",
    "etsy":          "https://www.etsy.com/search?q={q}",
    "reddit":        "https://www.reddit.com/search/?q={q}",
    "youtube":       "https://www.youtube.com/results?search_query={q}",
    "wikipedia":     "https://en.wikipedia.org/w/index.php?search={q}",
    "github":        "https://github.com/search?q={q}",
    "stackoverflow": "https://stackoverflow.com/search?q={q}",
    "thingiverse":   "https://www.thingiverse.com/search?q={q}",
    "printables":    "https://www.printables.com/search/models?q={q}",
}
FRONT = {
    "reddit":  "https://www.reddit.com/r/all/",
    "youtube": "https://www.youtube.com/feed/trending",
    "amazon":  "https://www.amazon.com/gp/bestsellers",
    "github":  "https://github.com/trending",
}
PRETTY = {"bestbuy": "Best Buy", "ebay": "eBay", "youtube": "YouTube", "github": "GitHub",
          "stackoverflow": "Stack Overflow", "microcenter": "Micro Center", "newegg": "Newegg"}
SHOPS = {"amazon", "ebay", "bestbuy", "walmart", "target", "newegg", "microcenter", "etsy"}

_PRICE = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)")
_NOISE = re.compile(r"sponsored|results?|filter|sort by|sign in|skip to|department|"
                    r"delivery|free shipping|add to cart|see options|customer reviews|"
                    r"^\$|^\d|best seller|overall pick|more buying choices|typical:|list:",
                    re.I)


def pretty(site: str) -> str:
    return PRETTY.get(site, site.capitalize())


def url_for(site: str, query: str) -> str | None:
    site = (site or "").lower().replace(" ", "")
    q = " ".join((query or "").split())
    if not q:
        return FRONT.get(site) or (SEARCH[site].split("?")[0].rsplit("/", 1)[0] if site in SEARCH else None)
    if site not in SEARCH:
        return None
    return SEARCH[site].format(q=urllib.parse.quote(q))


def extract_results(site: str, text: str, limit: int = 3) -> list[dict]:
    """Top results from the page's visible text. Shops: a title followed by a
    price. Other sites: the first substantial lines. Heuristic and honest:
    nothing is invented; an empty list is a fine answer."""
    lines = [" ".join(ln.split()) for ln in (text or "").splitlines()]
    lines = [ln for ln in lines if ln]
    out: list[dict] = []
    seen: set[str] = set()
    if site in SHOPS:
        recent: list[str] = []
        for ln in lines:
            m = _PRICE.search(ln)
            if m and len(ln) < 40:
                title = next((t for t in reversed(recent) if len(t) >= 20 and not _NOISE.search(t)), "")
                if title and title not in seen:
                    seen.add(title)
                    out.append({"title": title[:90], "price": m.group(1)})
                    if len(out) >= limit:
                        break
                recent = []
            else:
                recent.append(ln)
                recent = recent[-6:]
        return out
    for ln in lines:
        if len(ln) >= 25 and not _NOISE.search(ln) and ln not in seen:
            seen.add(ln)
            out.append({"title": ln[:110]})
            if len(out) >= limit:
                break
    return out


async def find_on_site(site: str, query: str = "") -> dict:
    site = (site or "").lower().replace(" ", "")
    url = url_for(site, query)
    if not url:
        return {"error": f"I don't know how to search {site or 'that site'}, sir."}
    opened = False
    try:
        from tools.windows_tools import open_url
        res = await asyncio.to_thread(open_url, url)
        opened = not (isinstance(res, dict) and res.get("error"))
    except Exception:
        log.warning("could not open %s in his browser", url, exc_info=True)
    results: list[dict] = []
    if query:
        try:
            from browser.session import browser
            obs = await asyncio.wait_for(browser.goto(url), timeout=12)
            results = extract_results(site, str((obs or {}).get("text") or ""))
        except Exception as e:
            log.info("could not read %s results for him: %s", site, str(e)[:80])
    return {"site": pretty(site), "query": query, "url": url, "opened": opened,
            "results": results, "count": len(results)}


def register_all() -> None:
    registry.register(Tool(
        name="find_on_site",
        description="Find something ON a named website (Amazon, eBay, Best Buy, Walmart, Target, "
                    "Newegg, Micro Center, Etsy, Reddit, YouTube, Wikipedia, GitHub, Thingiverse, "
                    "Printables): opens that site's own results in the USER's browser immediately "
                    "AND returns the top results to read out. Use this whenever he names the site; "
                    "never browser_open or web_search for that.",
        parameters={"type": "object", "properties": {
            "site": {"type": "string"}, "query": {"type": "string"}},
            "required": ["site"]},
        risk=Risk.LOW, handler=find_on_site, timeout=30))
