"""Keyless web + image search using the user's installed Brave browser.

Runs Brave Search in a hidden persistent Brave window driven by Playwright, so
no API key or account is needed. Headless mode gets a captcha; a headed window
does not. The window is moved off-screen AND hidden from the taskbar, warmed
at boot so the first search is fast, and kept alive between searches.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from urllib.parse import quote_plus

log = logging.getLogger("jarvis.search")

IDLE_CLOSE_S = 900  # 15 min

_EXTRACT_JS = r"""() => {
  const out = [], seen = new Set();
  for (const a of document.querySelectorAll('a[href^="http"]')) {
    const parent = a.parentElement;
    if (!parent || !/result-content/.test(parent.className || '')) continue;
    const href = a.href;
    if (!href || seen.has(href) || href.includes('search.brave.com')) continue;
    seen.add(href);
    const lines = (a.innerText || '').split('\\n').map(s => s.trim()).filter(Boolean);
    const title = lines.length ? lines[lines.length - 1] : href;
    const body = parent.parentElement && parent.parentElement.parentElement;
    let snippet = '';
    if (body) {
      const leaves = [...body.querySelectorAll('*')].filter(e => e.children.length === 0 && !a.contains(e) && e.innerText && e.innerText.trim().length > 40);
      leaves.sort((x, y) => y.innerText.length - x.innerText.length);
      snippet = leaves.length ? leaves[0].innerText.trim() : '';
    }
    let host = '';
    try { host = new URL(href).hostname.replace(/^www\\./, ''); } catch (e) {}
    out.push({ title, url: href, snippet: snippet.slice(0, 240), host });
  }
  return out;
}"""

_EXTRACT_IMAGES_JS = r"""() => {
  const out = [], seen = new Set();
  for (const img of document.querySelectorAll('img')) {
    let src = img.currentSrc || img.src || img.getAttribute('data-src') || '';
    if (!src && img.srcset) src = img.srcset.split(',')[0].trim().split(' ')[0];
    if (!src.startsWith('http') || seen.has(src)) continue;
    // Brave's own result thumbnails live on imgs.search.brave.com; other hosts are chrome/ads
    if (!/imgs\.search\.brave\.com/.test(src)) continue;
    const r = img.getBoundingClientRect();
    const w = Math.max(img.naturalWidth || 0, r.width || 0), h = Math.max(img.naturalHeight || 0, r.height || 0);
    if (w < 90 || h < 90) continue;
    seen.add(src);
    const a = img.closest('a');
    out.push({ src, alt: (img.alt || '').slice(0, 120), w: Math.round(w), h: Math.round(h), page: a ? a.href : '' });
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


def _kill_stale_profile_users(profile: str) -> None:
    """A crashed sidecar can leave a Brave instance holding our profile lock,
    which makes every future launch fail with 'existing browser session'."""
    try:
        import psutil
        for pr in psutil.process_iter(["name", "cmdline"]):
            try:
                if (pr.info["name"] or "").lower() == "brave.exe" and any(
                        profile.lower() in (a or "").lower() for a in (pr.info["cmdline"] or [])):
                    pr.kill()
            except Exception:
                continue
    except Exception:
        pass


def _show_windows_of_pid_offscreen(pid: int) -> None:
    """Make the (off-screen, tool-style) window 'visible' so Chromium renders and
    lazy-loads images; it stays off-screen and off the taskbar."""
    try:
        import win32con
        import win32gui
        import win32process
        import psutil
        pids = {pid} | {c.pid for c in psutil.Process(pid).children(recursive=True)}
    except Exception:
        return

    def cb(hwnd, _):
        try:
            _, wpid = win32process.GetWindowThreadProcessId(hwnd)
            if wpid in pids and win32gui.GetWindowText(hwnd):
                ex = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
                win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE,
                                       (ex | win32con.WS_EX_TOOLWINDOW) & ~win32con.WS_EX_APPWINDOW)
                win32gui.SetWindowPos(hwnd, 0, -32000, -32000, 1200, 900,
                                      win32con.SWP_NOACTIVATE | win32con.SWP_NOZORDER)
                win32gui.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)
        except Exception:
            pass
        return True

    try:
        win32gui.EnumWindows(cb, None)
    except Exception:
        pass


def _hide_windows_of_pid(pid: int) -> int:
    """Hide (SW_HIDE) every top-level window owned by a process tree so the
    search browser has no taskbar button at all."""
    try:
        import win32con
        import win32gui
        import win32process
        import psutil
        pids = {pid} | {c.pid for c in psutil.Process(pid).children(recursive=True)}
    except Exception:
        return 0
    hidden = 0

    def cb(hwnd, _):
        nonlocal hidden
        try:
            _, wpid = win32process.GetWindowThreadProcessId(hwnd)
            if wpid in pids and win32gui.GetWindowText(hwnd):
                # tool-window style: no taskbar button, even if Chromium re-shows it
                ex = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
                ex = (ex | win32con.WS_EX_TOOLWINDOW) & ~win32con.WS_EX_APPWINDOW
                win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, ex)
                win32gui.ShowWindow(hwnd, win32con.SW_HIDE)
                hidden += 1
        except Exception:
            pass
        return True

    try:
        win32gui.EnumWindows(cb, None)
    except Exception:
        pass
    return hidden


class BraveWebSearch:
    def __init__(self, profile_name: str = "browser-profile") -> None:
        self._profile_name = profile_name
        self._pw = None
        self._ctx = None
        self._pid: int | None = None
        self._lock = asyncio.Lock()
        self._last = 0.0
        self._reaper: asyncio.Task | None = None

    @property
    def available(self) -> bool:
        return _brave_path() is not None

    @property
    def ready(self) -> bool:
        return self._ctx is not None

    async def _ensure(self):
        if self._ctx is not None:
            return self._ctx
        from playwright.async_api import async_playwright
        from config import APP_DIR
        if self._pw is None:
            self._pw = await async_playwright().start()
        profile = str(APP_DIR / self._profile_name)
        _kill_stale_profile_users(profile)
        self._ctx = await self._pw.chromium.launch_persistent_context(
            profile, executable_path=_brave_path(), headless=False,
            args=["--window-position=-32000,-32000", "--window-size=1200,900",
                  "--no-first-run", "--no-default-browser-check"],
            viewport={"width": 1200, "height": 900})
        # hide from taskbar (needs the window to exist first)
        self._pid = None
        try:
            import psutil
            # newest brave.exe with our profile dir in its command line
            for pr in sorted(psutil.process_iter(["name", "cmdline", "create_time"]),
                             key=lambda x: x.info["create_time"] or 0, reverse=True):
                if (pr.info["name"] or "").lower() == "brave.exe" and any(
                        profile.lower() in (a or "").lower() for a in (pr.info["cmdline"] or [])):
                    self._pid = pr.pid
                    break
        except Exception:
            pass
        asyncio.get_running_loop().call_later(1.5, self._hide)
        asyncio.get_running_loop().call_later(5.0, self._hide)
        if self._reaper is None or self._reaper.done():
            self._reaper = asyncio.create_task(self._reap())
        return self._ctx

    def _hide(self) -> None:
        if self._pid:
            n = _hide_windows_of_pid(self._pid)
            if n:
                log.info("search browser hidden (%d window(s))", n)

    async def warmup(self) -> None:
        """Launch at boot so the first search isn't a cold start."""
        try:
            async with self._lock:
                ctx = await self._ensure()
                page = ctx.pages[0] if ctx.pages else await ctx.new_page()
                await page.bring_to_front()
                await page.goto("https://search.brave.com/", wait_until="domcontentloaded",
                                timeout=20000)
                self._hide()
                self._last = time.time()
            log.info("search browser warmed up")
        except Exception as e:
            log.warning("search browser warmup failed: %s", e)

    async def _reap(self) -> None:
        while self._ctx is not None:
            await asyncio.sleep(60)
            if time.time() - self._last > IDLE_CLOSE_S:
                await self.close()

    async def _reset(self) -> None:
        """Browser died or was killed: drop the dead handles, relaunch next call."""
        log.warning("search browser unusable — relaunching")
        await self.close()

    async def search(self, query: str, count: int = 5) -> list[dict]:
        try:
            return await self._search(query, count)
        except Exception as e:
            log.warning("search failed (%s) — retrying with a fresh browser", str(e)[:80])
            await self._reset()
            return await self._search(query, count)

    async def images(self, query: str, count: int = 8) -> list[dict]:
        try:
            return await self._images(query, count)
        except Exception as e:
            log.warning("image search failed (%s) — retrying with a fresh browser", str(e)[:80])
            await self._reset()
            return await self._images(query, count)

    async def _search(self, query: str, count: int = 5) -> list[dict]:
        async with self._lock:
            self._last = time.time()
            ctx = await self._ensure()
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            await page.bring_to_front()
            await page.goto(f"https://search.brave.com/search?q={quote_plus(query)}&source=web",
                            wait_until="domcontentloaded", timeout=25000)
            self._hide()
            try:
                await page.wait_for_selector(".result-content", timeout=15000)
            except Exception:
                title = await page.title()
                log.warning("no results rendered for %r (page: %s)", query, title)
            results = await page.evaluate(_EXTRACT_JS)
            self._last = time.time()
            return results[:count]

    async def _images(self, query: str, count: int = 8) -> list[dict]:
        async with self._lock:
            self._last = time.time()
            ctx = await self._ensure()
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            await page.bring_to_front()
            # Chromium won't lazy-load images in a hidden window: show it
            # off-screen (invisible, no taskbar button) just for this load.
            if self._pid:
                _show_windows_of_pid_offscreen(self._pid)
            await page.goto(f"https://search.brave.com/images?q={quote_plus(query)}",
                            wait_until="domcontentloaded", timeout=25000)
            try:
                await page.wait_for_selector("img[src*='imgs.search.brave.com']", timeout=15000)
            except Exception:
                pass
            for _ in range(3):
                await page.mouse.wheel(0, 900)
                await page.wait_for_timeout(500)
            await page.wait_for_timeout(800)
            imgs = await page.evaluate(_EXTRACT_IMAGES_JS)
            self._hide()
            self._last = time.time()
            return imgs[:count]

    async def close(self) -> None:
        # serialize with search/images/warmup: the idle reaper and retry _reset()
        # must not tear down a context that an in-flight request is holding.
        async with self._lock:
            try:
                if self._ctx:
                    await self._ctx.close()
            except Exception:
                pass
            self._ctx = None
            self._pid = None
            try:
                if self._pw:
                    await self._pw.stop()
            except Exception:
                pass
            self._pw = None


brave_web = BraveWebSearch()                      # web + image search tab
brave_session = BraveWebSearch("session-browser")  # JARVIS's interactive in-app browser
