"""Managed Playwright browser session — headed, so the user can watch JARVIS work.

Every action returns fresh observations (url, title, text) so the model follows
an observe -> act -> verify loop instead of assuming success.
"""
from __future__ import annotations

import asyncio
import logging

log = logging.getLogger("jarvis.browser")

MAX_TEXT = 3000


class BrowserSession:
    def __init__(self) -> None:
        self._pw = None
        self._browser = None
        self._page = None
        self._lock = asyncio.Lock()

    async def _ensure(self):
        if self._page is not None and not self._page.is_closed():
            return self._page
        from playwright.async_api import async_playwright
        if self._pw is None:
            self._pw = await async_playwright().start()
        if self._browser is None or not self._browser.is_connected():
            # Bundled Chromium needs the MSVC runtime, which this machine lacks
            # (admin-gated install). System Edge/Chrome ship their own runtime,
            # so prefer those channels; bundled build is the last resort.
            last_err = None
            import os
            brave = next((p for p in (
                r"C:\Program Files\BraveSoftware\Brave-Browser\Applicationrave.exe",
                os.path.expandvars(r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Applicationrave.exe"),
            ) if os.path.exists(p)), None)
            attempts = ([("brave", {"executable_path": brave})] if brave else []) + [
                ("msedge", {"channel": "msedge"}), ("chrome", {"channel": "chrome"}),
                ("bundled", {})]
            for label, kw in attempts:
                try:
                    log.info("launching browser (%s)", label)
                    self._browser = await self._pw.chromium.launch(
                        headless=False, args=["--window-size=1200,800"], **kw)
                    break
                except Exception as e:
                    last_err = e
            if self._browser is None:
                raise RuntimeError(f"no launchable browser: {last_err}")
        ctx = await self._browser.new_context(viewport={"width": 1180, "height": 720})
        self._page = await ctx.new_page()
        return self._page

    async def observe(self) -> dict:
        page = await self._ensure()
        try:
            title = await page.title()
            text = await page.evaluate(
                "() => document.body ? document.body.innerText : ''")
        except Exception as e:
            return {"url": page.url, "error": f"observe failed: {e}"}
        return {"url": page.url, "title": title,
                "text": text[:MAX_TEXT], "truncated": len(text) > MAX_TEXT}

    async def goto(self, url: str) -> dict:
        async with self._lock:
            page = await self._ensure()
            if not url.startswith(("http://", "https://")):
                url = "https://" + url
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                await page.wait_for_timeout(800)
            except Exception as e:
                return {"error": f"navigation failed: {e}", "url": page.url}
            return await self.observe()

    async def click(self, target: str) -> dict:
        """Click by visible text / accessible name; falls back to CSS selector."""
        async with self._lock:
            page = await self._ensure()
            tried = []
            for locator in (
                page.get_by_role("button", name=target),
                page.get_by_role("link", name=target),
                page.get_by_text(target, exact=False),
                page.locator(target),
            ):
                try:
                    await locator.first.click(timeout=4000)
                    await page.wait_for_timeout(900)
                    obs = await self.observe()
                    obs["clicked"] = target
                    return obs
                except Exception as e:
                    tried.append(str(e).split("\n")[0][:80])
            return {"error": f"could not click '{target}'", "attempts": tried[-1:]}

    async def type_text(self, field: str, text: str) -> dict:
        """Fill an input located by label/placeholder/name; fallback selector."""
        async with self._lock:
            page = await self._ensure()
            for locator in (
                page.get_by_label(field),
                page.get_by_placeholder(field),
                page.locator(f"input[name='{field}']"),
                page.locator(field),
            ):
                try:
                    await locator.first.fill(text, timeout=4000)
                    return {"filled": field, "chars": len(text), "url": page.url}
                except Exception:
                    continue
            return {"error": f"could not find field '{field}'"}

    async def press_enter(self) -> dict:
        async with self._lock:
            page = await self._ensure()
            await page.keyboard.press("Enter")
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=10000)
            except Exception:
                pass
            await page.wait_for_timeout(800)
            return await self.observe()

    async def back(self) -> dict:
        async with self._lock:
            page = await self._ensure()
            try:
                await page.go_back(wait_until="domcontentloaded", timeout=15000)
            except Exception as e:
                return {"error": f"back failed: {e}"}
            return await self.observe()

    async def close(self) -> None:
        try:
            if self._browser:
                await self._browser.close()
            if self._pw:
                await self._pw.stop()
        except Exception:
            pass
        self._browser = None
        self._page = None
        self._pw = None


browser = BrowserSession()
