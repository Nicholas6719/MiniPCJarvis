"""Keyless web search using the user's installed Brave browser.

Runs Brave Search in a hidden (off-screen) persistent Brave window driven by
Playwright, so no API key or account is needed. Headless mode gets a captcha;
an off-screen headed window does not. The window is kept warm between searches
and closed after a few idle minutes.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from urllib.parse import quote_plus

log = logging.getLogger("jarvis.search")

IDLE_CLOSE_S = 240

_EXTRACT_JS = """() => {
  const out = [], seen = new Set();
  for (const a of document.querySelectorAll('a[href^="http"]')) {
    const parent = a.parentElement;
    if (!parent || !/result-content/.test(parent.className || '')) continue;
    const href = a.href;
    if (!href || seen.has(href) || href.includes('search.brave.com')) continue;
    seen.add(href);
    const lines = (a.innerText || '').split('\\n').map(s => s.trim()).filter(Boolean);
    const title = lines.length ? lines[lines.length - 1] : href;
    // result-content -> result-wrapper -> result-body; the description is the
    // longest leaf text in the body that isn't the link itself
    const body = parent.parentElement && parent.parentElement.parentElement;
    let snippet = '';
    if (body) {
      const leaves = [...body.querySelectorAll('*')].filter(e => e.children.length === 0 && !a.contains(e) && e.innerText && e.innerText.trim().length > 40);
      leaves.sort((x, y) => y.innerText.length - x.innerText.length);
      snippet = leaves.length ? leaves[0].innerText.trim() : '';
    }
    out.push({ title, url: href, snippet: snippet.slice(0, 240) });
  }
  return out;
}"""


def _brave_path() -> str | None:
    rel = os.path.join("BraveSoftware", "Brave-Browser", "Application", "brave.exe")
    for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                 os.environ.get("ProgramFiles(x86)", ""),
                 os.environ.get("LOCALAPPDATA", "")):
        if base:
            p = os.path.join(base, rel)
            if os.path.exists(p):
                return p
    return None


class BraveWebSearch:
    def __init__(self) -> None:
        self._pw = None
        self._ctx = None
        self._lock = asyncio.Lock()
        self._last = 0.0
        self._reaper: asyncio.Task | None = None

    @property
    def available(self) -> bool:
        return _brave_path() is not None

    async def _ensure(self):
        if self._ctx is not None:
            return self._ctx
        from playwright.async_api import async_playwright
        from config import APP_DIR
        if self._pw is None:
            self._pw = await async_playwright().start()
        profile = str(APP_DIR / "browser-profile")
        self._ctx = await self._pw.chromium.launch_persistent_context(
            profile, executable_path=_brave_path(), headless=False,
            args=["--window-position=-32000,-32000", "--window-size=1200,900"],
            viewport={"width": 1200, "height": 900})
        if self._reaper is None or self._reaper.done():
            self._reaper = asyncio.create_task(self._reap())
        return self._ctx

    async def _reap(self) -> None:
        while self._ctx is not None:
            await asyncio.sleep(30)
            if time.time() - self._last > IDLE_CLOSE_S:
                await self.close()

    async def search(self, query: str, count: int = 5) -> list[dict]:
        async with self._lock:
            self._last = time.time()
            ctx = await self._ensure()
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            await page.goto(f"https://search.brave.com/search?q={quote_plus(query)}&source=web",
                            wait_until="domcontentloaded", timeout=20000)
            try:
                await page.wait_for_selector(".result-content", timeout=8000)
            except Exception:
                pass
            results = await page.evaluate(_EXTRACT_JS)
            self._last = time.time()
            return results[:count]

    async def close(self) -> None:
        try:
            if self._ctx:
                await self._ctx.close()
        except Exception:
            pass
        self._ctx = None
        try:
            if self._pw:
                await self._pw.stop()
        except Exception:
            pass
        self._pw = None


brave_web = BraveWebSearch()
