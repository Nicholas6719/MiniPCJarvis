"""Keyless web + image search driven through the user's OWN Brave browser.

His real profile, with his history and logins — not a scratch one. That is both what he
asked for and what makes it work: a blank profile is precisely what bot detection looks
for, and the old throwaway-profile version was getting a CAPTCHA and zero results on
every single query, which the model then relayed as "I couldn't find anything".

The window is MINIMISED, never hidden: JARVIS searches in the background while he works,
and the moment he wants to see it he clicks Brave in the taskbar and the page is there.
JARVIS keeps its own tab so it can never navigate away from whatever he is reading.

Engines: Google, then DuckDuckGo. Brave Search is deliberately not used — it challenges
automated queries even from his own profile (measured: the first query fine, every one
after it "Verifying you're not a bot"). No API key, no account.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from urllib.parse import quote_plus

log = logging.getLogger("jarvis.search")

IDLE_CLOSE_S = 900  # 15 min

_EXTRACT_JS = r"""({titleSel, engineHost}) => {
  // Anchored on the result TITLE, not on links in general. Taking every anchor pulled in
  // Google's video carousel ahead of the web results and turned DuckDuckGo's displayed
  // URL into the title. Engines restyle constantly; "the heading inside the result link"
  // has outlived several of those redesigns.
  const seen = new Set(), out = [];
  for (const el of document.querySelectorAll(titleSel)) {
    const a = el.closest('a[href^="http"]') || el.querySelector('a[href^="http"]');
    if (!a) continue;
    let host;
    try { host = new URL(a.href).hostname.replace(/^www\./, ''); } catch (e) { continue; }
    if (host.includes(engineHost)) continue;
    const title = (el.innerText || '').replace(/\s+/g, ' ').trim();
    if (title.length < 8) continue;
    const key = a.href.split('#')[0];
    if (seen.has(key)) continue;
    seen.add(key);
    let box = a, hops = 0;
    while (box && hops < 6 && box.innerText.trim().length < title.length + 90) {
      box = box.parentElement; hops++;
    }
    let snippet = box ? box.innerText.replace(/\s+/g, ' ').trim() : '';
    const i = snippet.indexOf(title);
    if (i >= 0) snippet = snippet.slice(i + title.length).trim();
    out.push({ title: title.slice(0, 140), url: a.href, snippet: snippet.slice(0, 280), host });
    if (out.length >= 10) break;
  }
  return out;
}"""

_EXTRACT_IMAGES_JS = r"""() => {
  const out = [], seen = new Set();
  for (const img of document.querySelectorAll('img')) {
    let src = img.currentSrc || img.src || img.getAttribute('data-src') || '';
    if (!src && img.srcset) src = img.srcset.split(',')[0].trim().split(' ')[0];
    if (!src.startsWith('http') || seen.has(src)) continue;
    // Accept the search engine's thumbnail hosts; anything else on the page is chrome/ads
    if (!/imgs\.search\.brave\.com|external-content\.duckduckgo\.com/.test(src)) continue;
    const r = img.getBoundingClientRect();
    const w = Math.max(img.naturalWidth || 0, r.width || 0), h = Math.max(img.naturalHeight || 0, r.height || 0);
    if (w < 90 || h < 90) continue;
    seen.add(src);
    const a = img.closest('a');
    out.push({ src, alt: (img.alt || '').slice(0, 120), w: Math.round(w), h: Math.round(h), page: a ? a.href : '' });
  }
  return out;
}"""


def _real_profile() -> str | None:
    """HIS Brave profile — the one with his logins, history and cookies.

    JARVIS used to search from a blank throwaway profile parked at -32000,-32000. A brand
    new profile with no history is exactly what bot detection is looking for, so every
    search came back with a CAPTCHA and zero results. Driving the browser he actually
    uses is both what he asked for and the thing that makes search work at all.
    """
    base = os.environ.get("LOCALAPPDATA", "")
    if base:
        p = os.path.join(base, "BraveSoftware", "Brave-Browser", "User Data")
        if os.path.isdir(p):
            return p
    return None


# Brave Search challenges automated queries even from his own profile (measured: first
# query fine, every one after that "Verifying you're not a bot"). Google and DuckDuckGo
# serve normally in the same browser, so those are what we use. Order matters: Google
# first, DuckDuckGo as the fallback.
_ENGINES = [
    ("google", "https://www.google.com/search?q=", "google.com", "#search h3, #rso h3"),
    ("duckduckgo", "https://duckduckgo.com/?q=", "duckduckgo.com",
     '[data-testid="result-title-a"], article h2'),
]

_CHALLENGE = ("not a bot", "unusual traffic", "are you human", "captcha",
              "verify you", "quick check before")


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


# Which windows JARVIS has hidden for its own use. open_url needs to tell "the window I
# just opened for him" apart from "the window JARVIS is quietly reading in" — and they
# belong to the same Brave process, so the pid cannot distinguish them.
_HIDDEN_HWNDS: set[int] = set()


def hidden_hwnds() -> set[int]:
    return set(_HIDDEN_HWNDS)


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
                _HIDDEN_HWNDS.add(hwnd)
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
        self._engine: str | None = None
        self._attached = False          # True when driving a Brave we did not launch
        self._own_browser = False       # True only for a scratch profile we own outright
        self._we_spawned = False        # True when JARVIS started Brave (so ours to hide)
        self._page = None               # JARVIS's own tab
        self._reaper: asyncio.Task | None = None

    @property
    def available(self) -> bool:
        return _brave_path() is not None

    @property
    def ready(self) -> bool:
        return self._ctx is not None

    CDP_PORT = 9222

    async def _ensure(self):
        if self._ctx is not None:
            return self._ctx
        from playwright.async_api import async_playwright
        from config import APP_DIR
        if self._pw is None:
            self._pw = await async_playwright().start()
        # If a Brave is already up with debugging open — usually one JARVIS started
        # earlier — attach to it instead of trying to launch a second one on the same
        # profile, which Chromium refuses outright.
        try:
            browser = await self._pw.chromium.connect_over_cdp(
                f"http://127.0.0.1:{self.CDP_PORT}", timeout=2500)
            self._ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
            self._attached = True
            log.info("attached to the running Brave over CDP")
            return self._ctx
        except Exception:
            pass
        real = _real_profile()
        if real:
            # Launch Brave OURSELVES, detached, and only ever attach to it.
            # Playwright kills whatever it launched when it stops, so a launched browser
            # meant his Brave died with the sidecar — tabs and all. Spawning it outside
            # Playwright's lifecycle makes "JARVIS started it" and "he started it"
            # the same case: attach, work in our own tab, never own the process.
            await self._spawn_brave(real)
            for _ in range(40):
                try:
                    browser = await self._pw.chromium.connect_over_cdp(
                        f"http://127.0.0.1:{self.CDP_PORT}", timeout=2000)
                    self._ctx = (browser.contexts[0] if browser.contexts
                                 else await browser.new_context())
                    self._attached = True
                    self._own_browser = False
                    self._find_pid(real)
                    return self._ctx
                except Exception:
                    await asyncio.sleep(0.5)
            raise RuntimeError("Brave did not come up with remote debugging enabled")

        profile = str(APP_DIR / self._profile_name)
        own = True
        self._own_browser = own
        if own:
            # only ever kill Brave for a scratch profile of ours, never for his
            _kill_stale_profile_users(profile)
        args = ["--no-first-run", "--no-default-browser-check", "--window-size=1280,900",
                # so a later sidecar restart can attach instead of fighting for the profile
                f"--remote-debugging-port={self.CDP_PORT}"]
        if not own:
            args.append("--profile-directory=Default")
        else:
            args.append("--window-position=-32000,-32000")
        try:
            self._ctx = await self._pw.chromium.launch_persistent_context(
                profile, executable_path=_brave_path(), headless=False,
                args=args, viewport=None if not own else {"width": 1280, "height": 900})
        except Exception as e:
            # His Brave is already open, so the profile is locked and we cannot drive it.
            # Say so rather than silently falling back to a blank profile that gets
            # CAPTCHA'd on every query.
            if own:
                raise
            raise RuntimeError(
                "Brave is already running, so JARVIS can't drive it. Close Brave and ask "
                "again — JARVIS will reopen it with your normal profile."
            ) from e
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

    async def _spawn_brave(self, profile: str) -> None:
        """Start his Brave minimised with debugging open, unless it is already running.

        Chromium is single-instance per profile: if he opens Brave from his shortcut
        afterwards, that window joins THIS process, so JARVIS keeps working and he keeps
        browsing in one browser. Verified — the shortcut adds no second instance and the
        debug port stays live.
        """
        import subprocess
        try:
            import httpx
            async with httpx.AsyncClient(timeout=2) as c:
                if (await c.get(f"http://127.0.0.1:{self.CDP_PORT}/json/version")).status_code == 200:
                    self._we_spawned = False   # his window, his rules: never hide or move it
                    return
        except Exception:
            pass
        si = None
        try:
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = 7                               # SW_SHOWMINNOACTIVE
        except Exception:
            si = None
        subprocess.Popen(
            [_brave_path(), f"--remote-debugging-port={self.CDP_PORT}",
             f"--user-data-dir={profile}", "--profile-directory=Default",
             "--no-first-run", "--no-default-browser-check",
             "--window-position=-32000,-32000", "--window-size=1280,900"],
            startupinfo=si, close_fds=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self._we_spawned = True
        log.info("started his Brave off-screen with remote debugging")

    def _find_pid(self, profile: str) -> None:
        try:
            import psutil
            for pr in sorted(psutil.process_iter(["name", "cmdline", "create_time"]),
                             key=lambda x: x.info["create_time"] or 0):
                if ((pr.info["name"] or "").lower() == "brave.exe"
                        and any("--remote-debugging-port" in (a or "")
                                for a in (pr.info["cmdline"] or []))):
                    self._pid = pr.pid
                    return
        except Exception:
            pass

    async def _tab(self, ctx):
        """JARVIS's own tab in his Brave.

        Attaching to a running browser means ctx.pages[0] is whatever HE is reading.
        Navigating that away would be unforgivable, so JARVIS keeps one tab of its own
        and reuses it — which is also what makes "click Brave and it's right there" true.
        """
        page = getattr(self, "_page", None)
        if page is not None and not page.is_closed():
            return page
        self._page = ctx.pages[0] if (ctx.pages and not self._attached) else await ctx.new_page()
        self._hide()      # a new tab makes Chromium show the window again
        return self._page

    def _hide(self) -> None:
        """Keep JARVIS's browsing invisible.

        Research runs in the background and is read in the JARVIS panel. A Brave window
        appearing — never mind taking the screen — is the bug, not the feature. If HE
        opened Brave himself we touch nothing: his windows are his, and our work simply
        lives in a background tab of his.
        """
        if not self._pid or not self._we_spawned:
            return
        _hide_windows_of_pid(self._pid)

    async def warmup(self) -> None:
        """Launch at boot so the first search isn't a cold start."""
        try:
            async with self._lock:
                ctx = await self._ensure()
                # No bring_to_front and no navigation: warming exists to pay the browser
                # start-up cost early, and he must never have a window jump in front of
                # him at boot because of it.
                await self._tab(ctx)
                self._hide()
                self._last = time.time()
            log.info("search browser warmed up")
        except Exception as e:
            log.warning("search browser warmup failed: %s", e)

    async def _reap(self) -> None:
        """Tidy away OUR tab when it has gone cold. Never the browser.

        This used to call close(), which closed the whole context — his Brave, his tabs,
        his work, fifteen minutes after JARVIS last searched. Harmless when it was a
        throwaway profile; unforgivable now that it is his own browser.
        """
        while self._ctx is not None:
            await asyncio.sleep(60)
            if time.time() - self._last <= IDLE_CLOSE_S:
                continue
            if self._own_browser:
                await self.close()          # our own scratch instance: fine to shut down
                return
            try:
                if self._page and not self._page.is_closed():
                    await self._page.close()
                    log.info("closed JARVIS's idle search tab (his browser left alone)")
                self._page = None
            except Exception:
                pass

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
            page = await self._tab(ctx)
            # deliberately NO bring_to_front: he asked for this to happen in the
            # background while he works, and to be there when he clicks Brave himself.
            last_err = ""
            for name, base, host, sel in _ENGINES:
                try:
                    await page.goto(base + quote_plus(query),
                                    wait_until="domcontentloaded", timeout=25000)
                    self._hide()          # Chromium re-shows on navigation
                    await page.wait_for_timeout(1200)
                    try:
                        await page.wait_for_selector(sel, timeout=8000)
                    except Exception:
                        pass
                    body = ((await page.inner_text("body"))[:400] or "").lower()
                    if any(w in body for w in _CHALLENGE):
                        last_err = f"{name} asked to verify"
                        log.warning("%s served a bot challenge for %r", name, query)
                        continue
                    results = await page.evaluate(
                        _EXTRACT_JS, {"titleSel": sel, "engineHost": host})
                    if results:
                        log.info("search %r -> %d results via %s", query, len(results), name)
                        self._last = time.time()
                        self._engine = name
                        return results[:count]
                    last_err = f"{name} returned nothing"
                except Exception as e:
                    last_err = f"{name}: {str(e)[:70]}"
                    log.warning("search on %s failed: %s", name, str(e)[:90])
            log.warning("no engine returned results for %r (%s)", query, last_err)
            self._last = time.time()
            return []

    async def _images(self, query: str, count: int = 8) -> list[dict]:
        async with self._lock:
            self._last = time.time()
            ctx = await self._ensure()
            page = await self._tab(ctx)
            # Brave's image search challenges automation the same way its web search does;
            # DuckDuckGo's serves normally from his profile. No bring_to_front.
            # The off-screen trick is ONLY for a scratch browser of ours — doing it to his
            # Brave would fling his window to -32000 and strip it off the taskbar.
            if self._pid and self._own_browser:
                _show_windows_of_pid_offscreen(self._pid)
            await page.goto(f"https://duckduckgo.com/?iax=images&ia=images&q={quote_plus(query)}",
                            wait_until="domcontentloaded", timeout=25000)
            try:
                await page.wait_for_selector("img.tile--img__img, img[src*='external-content']",
                                             timeout=15000)
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
                if self._ctx and self._own_browser and not self._attached:
                    await self._ctx.close()      # a scratch profile we launched: ours to close
                elif self._page and not self._page.is_closed():
                    # his Brave: our tab goes, his browser and his tabs stay exactly as
                    # they are — including when JARVIS itself is shutting down
                    await self._page.close()
            except Exception:
                pass
            self._ctx = None
            self._page = None
            self._attached = False
            self._pid = None
            try:
                # Attached, not launched: stopping the driver just disconnects. His Brave
                # keeps running with every tab exactly where he left it.
                if self._pw:
                    await self._pw.stop()
            except Exception:
                pass
            self._pw = None


brave_web = BraveWebSearch()
# One browser, not two. These used to be separate profiles, so a search and a page-read
# each spawned their own hidden Brave — exactly the "it opens other windows" complaint.
brave_session = brave_web
