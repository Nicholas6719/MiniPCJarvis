"""JARVIS's browser session — runs INSIDE JARVIS.

Pages are loaded in the same hidden Brave instance the web search uses
(off-screen, tool-window, never on the taskbar, never the user's browser).
After every action a screenshot + page text is pushed to the HUD ("browser"
event) so the user watches JARVIS work in the WEB panel, never in another app.

Every action returns fresh observations (url, title, text) so the model follows
an observe -> act -> verify loop instead of assuming success.
"""
from __future__ import annotations

import asyncio
import base64
import logging

from events import bus

log = logging.getLogger("jarvis.browser")

MAX_TEXT = 3000
SHOT_W, SHOT_H = 1200, 900


class BrowserSession:
    def __init__(self) -> None:
        self._page = None
        self._lock = asyncio.Lock()

    async def _ensure(self):
        if self._page is not None and not self._page.is_closed():
            return self._page
        from search_brave_web import brave_session
        if not brave_session.available:
            raise RuntimeError("Brave browser is not installed; JARVIS's in-app browser needs it")
        ctx = await brave_session._ensure()
        self._page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await self._page.set_viewport_size({"width": SHOT_W, "height": SHOT_H})
        return self._page

    def _pid(self) -> int | None:
        from search_brave_web import brave_session
        return brave_session._pid

    def _show(self) -> None:
        from search_brave_web import _show_windows_of_pid_offscreen
        pid = self._pid()
        if pid:
            _show_windows_of_pid_offscreen(pid)

    def _hide(self) -> None:
        from search_brave_web import _hide_windows_of_pid
        pid = self._pid()
        if pid:
            _hide_windows_of_pid(pid)

    async def _publish(self, obs: dict, action: str) -> None:
        """Push a screenshot + text of the current page to the HUD."""
        page = self._page
        if page is None:
            return
        shot = None
        try:
            await page.bring_to_front()
            raw = await page.screenshot(type="jpeg", quality=55, timeout=8000)
            shot = "data:image/jpeg;base64," + base64.b64encode(raw).decode()
        except Exception as e:
            log.debug("screenshot failed: %s", e)
        await bus.emit("browser", action=action, url=obs.get("url"), title=obs.get("title"),
                       text=(obs.get("text") or "")[:1500], shot=shot, error=obs.get("error"))

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

    async def _act(self, action: str, fn) -> dict:
        """Run a page action with the hidden window 'shown' off-screen (so Chromium
        renders), then publish what happened to the HUD and hide again."""
        async with self._lock:
            page = await self._ensure()
            import time
            from search_brave_web import brave_session
            brave_session._last = time.time()   # keep the idle reaper away while in use
            self._show()
            try:
                obs = await fn(page)
            except Exception as e:
                obs = {"error": f"{action} failed: {e}", "url": page.url}
            try:
                await self._publish(obs, action)
            finally:
                self._hide()
            return obs

    async def goto(self, url: str) -> dict:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        async def fn(page):
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                await page.wait_for_timeout(800)
            except Exception as e:
                return {"error": f"navigation failed: {e}", "url": page.url}
            return await self.observe()
        return await self._act("open", fn)

    async def read(self) -> dict:
        return await self._act("read", lambda page: self.observe())

    async def click(self, target: str) -> dict:
        """Click by visible text / accessible name; falls back to CSS selector."""
        async def fn(page):
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
            return {"error": f"could not click '{target}'", "attempts": tried[-1:], "url": page.url}
        return await self._act("click", fn)

    async def type_text(self, field: str, text: str) -> dict:
        """Fill an input located by label/placeholder/name; fallback selector."""
        async def fn(page):
            for locator in (
                page.get_by_label(field),
                page.get_by_placeholder(field),
                page.locator(f"input[name='{field}']"),
                page.locator(field),
            ):
                try:
                    await locator.first.fill(text, timeout=4000)
                    obs = await self.observe()
                    obs.update({"filled": field, "chars": len(text)})
                    return obs
                except Exception:
                    continue
            return {"error": f"could not find field '{field}'", "url": page.url}
        return await self._act("type", fn)

    async def press_enter(self) -> dict:
        async def fn(page):
            await page.keyboard.press("Enter")
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=10000)
            except Exception:
                pass
            await page.wait_for_timeout(800)
            return await self.observe()
        return await self._act("submit", fn)

    async def back(self) -> dict:
        async def fn(page):
            try:
                await page.go_back(wait_until="domcontentloaded", timeout=15000)
            except Exception as e:
                return {"error": f"back failed: {e}", "url": page.url}
            return await self.observe()
        return await self._act("back", fn)

    # ---- direct interaction from the HUD (click-through on the screenshot) ----

    async def click_at(self, fx: float, fy: float) -> dict:
        async def fn(page):
            await page.mouse.click(fx * SHOT_W, fy * SHOT_H)
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=6000)
            except Exception:
                pass
            await page.wait_for_timeout(600)
            return await self.observe()
        return await self._act("click", fn)

    async def scroll_by(self, dy: int) -> dict:
        async def fn(page):
            await page.mouse.move(SHOT_W / 2, SHOT_H / 2)
            await page.mouse.wheel(0, dy)
            await page.wait_for_timeout(250)
            return await self.observe()
        return await self._act("scroll", fn)

    async def type_keys(self, text: str, enter: bool = False) -> dict:
        async def fn(page):
            if text:
                await page.keyboard.type(text, delay=20)
            if enter:
                await page.keyboard.press("Enter")
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=8000)
                except Exception:
                    pass
            await page.wait_for_timeout(500)
            return await self.observe()
        return await self._act("type", fn)

    async def close(self) -> None:
        from search_brave_web import brave_session
        self._page = None
        await brave_session.close()


browser = BrowserSession()
