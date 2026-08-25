"""Phase 1 built-in tools: system stats, open/close app, web search, read file."""
from __future__ import annotations

import logging
import asyncio
import os
import re
import subprocess
from pathlib import Path

import httpx
import psutil

from config import config, secrets
from tools.registry import Risk, Tool, registry

log = logging.getLogger("jarvis.tools.builtin")

_HOME = Path.home()
_ALLOWED_READ_ROOTS = [
    _HOME / "Documents", _HOME / "Downloads", _HOME / "Desktop", _HOME / "Pictures",
]

# Friendly-name launch table; anything else falls through to PATH lookup.
_APP_ALIASES = {
    "chrome": "chrome.exe",
    "google chrome": "chrome.exe",
    "edge": "msedge.exe",
    "notepad": "notepad.exe",
    "calculator": "calc.exe",
    "explorer": "explorer.exe",
    "file explorer": "explorer.exe",
    "spotify": "spotify.exe",
    "vs code": "visual studio code",
    "vscode": "visual studio code",
    "code": "visual studio code",
    "terminal": "wt.exe",
    "settings": "ms-settings:",
    "steam": "steam.exe",
    "task manager": "taskmgr.exe",
}


psutil.cpu_percent(interval=None)   # prime the counter so callers never have to block


def get_system_stats() -> dict:
    vm = psutil.virtual_memory()
    disk = psutil.disk_usage("C:\\")
    batt = None
    try:
        b = psutil.sensors_battery()
        if b:
            batt = {"percent": b.percent, "plugged": b.power_plugged}
    except Exception:
        pass
    return {
        # interval=None -> average since the previous call (primed above): no 0.3 s block
        "cpu_percent": psutil.cpu_percent(interval=None),
        "ram_used_gb": round(vm.used / 1e9, 1),
        "ram_total_gb": round(vm.total / 1e9, 1),
        "ram_percent": vm.percent,
        "disk_c_free_gb": round(disk.free / 1e9, 1),
        "disk_c_percent": disk.percent,
        "battery": batt,
        "process_count": len(psutil.pids()),
    }


_START_MENUS = [
    _HOME / "AppData/Roaming/Microsoft/Windows/Start Menu/Programs",
    Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "Microsoft/Windows/Start Menu/Programs",
]


_store_apps_cache: dict[str, str] | None = None


def _store_apps() -> dict[str, str]:
    """Name -> AppUserModelId for everything Windows' Start search knows (incl. Store
    apps like Spotify). Cached; gathered once with a hidden PowerShell (~1 s)."""
    global _store_apps_cache
    if _store_apps_cache is not None:
        return _store_apps_cache
    apps: dict[str, str] = {}
    try:
        import json
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress"],
            capture_output=True, text=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        data = json.loads(out.stdout or "[]")
        for row in (data if isinstance(data, list) else [data]):
            if row.get("Name") and row.get("AppID"):
                apps[str(row["Name"]).lower()] = str(row["AppID"])
    except Exception as e:
        log.warning("Get-StartApps failed: %s", e)
    _store_apps_cache = apps
    return apps


def _best_match(key: str, names) -> str | None:
    exact = [n for n in names if n == key]
    if exact:
        return exact[0]
    subs = sorted((n for n in names if key in n and "uninstall" not in n), key=len)
    return subs[0] if subs else None


def _resolve_app(name: str) -> str | None:
    """Find something launchable for a friendly app name, WITHOUT a shell:
    alias -> Start Menu shortcut -> PATH exe -> Start-search app list (Store apps).
    Returns None if nothing matches so the caller can report it (no stray windows)."""
    import shutil
    key = name.strip().lower()
    target = _APP_ALIASES.get(key)
    if target:
        if target.startswith("ms-settings") or target.endswith(":"):
            return target
        found = shutil.which(target)
        if found and not found.lower().endswith((".cmd", ".bat")):
            return found
    # Start Menu shortcuts (what the Start search launches)
    links: dict[str, str] = {}
    for root in _START_MENUS:
        if root.exists():
            for lnk in root.rglob("*.lnk"):
                links.setdefault(lnk.stem.lower(), str(lnk))
    keys = [key]
    if target and not target.startswith("ms-settings"):
        keys.append(target.lower().removesuffix(".exe"))
    for k in keys:
        m = _best_match(k, links)
        if m:
            return links[m]
    for k in keys:
        found = shutil.which(k) or shutil.which(k + ".exe")
        if found and not found.lower().endswith((".cmd", ".bat")):
            return found
    apps = _store_apps()
    for k in keys:
        m = _best_match(k, apps)
        if m:
            return "shell:AppsFolder\\" + apps[m]
    return None


# Well-known services people say by name. No installed app -> open the site inside JARVIS.
_KNOWN_SITES = {
    "youtube": "youtube.com", "netflix": "netflix.com", "gmail": "mail.google.com", "google": "google.com",
    "amazon": "amazon.com", "reddit": "reddit.com", "twitter": "x.com", "x": "x.com", "facebook": "facebook.com",
    "instagram": "instagram.com", "github": "github.com", "wikipedia": "wikipedia.org", "twitch": "twitch.tv",
    "hulu": "hulu.com", "disney plus": "disneyplus.com", "disney": "disneyplus.com", "hbo": "max.com", "max": "max.com",
    "prime video": "primevideo.com", "espn": "espn.com", "spotify": "open.spotify.com", "apple music": "music.apple.com",
    "chatgpt": "chatgpt.com", "claude": "claude.ai", "linkedin": "linkedin.com", "ebay": "ebay.com",
    "google maps": "maps.google.com", "maps": "maps.google.com", "google drive": "drive.google.com",
    "drive": "drive.google.com", "google docs": "docs.google.com", "docs": "docs.google.com",
    "outlook": "outlook.live.com", "yahoo": "yahoo.com", "bing": "bing.com", "pinterest": "pinterest.com",
    "tiktok": "tiktok.com", "steam store": "store.steampowered.com", "weather": "weather.com",
    "paypal": "paypal.com", "venmo": "venmo.com", "zillow": "zillow.com", "imdb": "imdb.com",
}


def site_for(name: str) -> str | None:
    return _KNOWN_SITES.get(name.strip().lower().removeprefix("the ").strip())


async def open_application(name: str) -> dict:
    key = name.strip().lower()
    if re.search(r"^https?://|\b[a-z0-9-]+\.(?:com|org|net|io|gov|edu|co|tv|ai)\b", key):
        # a website, not an app: caller should use open_url (opens the user's browser)
        return {"error": f"'{name}' is a website, not an app. Use open_url to open it in the browser."}
    # _resolve_app does Start-Menu rglob + a PowerShell Get-StartApps (up to ~15 s on first
    # use). It's sync, so run it off the event loop or it freezes audio/wake/TTS.
    target = await asyncio.to_thread(_resolve_app, name)
    if not target:
        site = site_for(name)
        if site:
            # no app installed by that name but it's a known service: open it for the user
            from tools.windows_tools import open_url
            r = open_url("https://" + site)
            return {"opened_site": site, **({"error": r["error"]} if r.get("error") else {})}
        return {"error": f"I can't find an app or site called '{name}' on this PC."}
    try:
        if target.startswith("shell:AppsFolder"):
            subprocess.Popen(["explorer.exe", target])   # Store app via its AppID, no console
        else:
            os.startfile(target)   # no shell, no console window
        return {"launched": name, "target": target}
    except Exception as e:
        return {"error": f"could not launch {name}: {e}"}


def _pids_for(exe: str) -> set[int]:
    return {p.pid for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower() == exe.lower()}


def close_application(name: str) -> dict:
    """Close an app the way clicking its X does: post WM_CLOSE to its windows (so it can
    prompt to save), then fall back to terminate() only for windowless leftovers.

    Store/packaged apps (Notepad, Calculator, Paint) REFUSE terminate() with AccessDenied -
    the old kill-only path reported them as 'not running'. Their visible window also belongs
    to ApplicationFrameHost, so match by window title too. Never hard-kill something that
    still has a window: that loses unsaved work with no prompt.
    """
    import time as _time
    import win32con
    import win32gui
    import win32process
    from tools.windows_tools import _visible_windows

    key = name.strip().lower().removesuffix(".exe")
    for junk in (" browser", " app", " application", " window", "the "):
        key = key.replace(junk, "").strip()
    exe = _APP_ALIASES.get(key, key + ".exe").removesuffix(".exe") + ".exe"
    stem = exe.removesuffix(".exe").lower()
    # only match on real name words; drop stopwords so "close all windows"/"close the tab"
    # can never fan out to every window on the desktop
    _STOP = {"the", "all", "this", "that", "app", "window", "windows", "tab", "down", "up", "my", "a"}
    words = [w for w in stem.replace("-", " ").split() if len(w) >= 3 and w not in _STOP]
    if not words:
        return {"error": f"'{name}' isn't a specific app I can close"}

    # JARVIS's own hidden browsers are never "the user's Brave"
    protected: set[int] = set()
    for pr in psutil.process_iter(["name", "cmdline"]):
        try:
            cl = " ".join(pr.info["cmdline"] or []).lower()
            if "jarvis" in cl and ("browser-profile" in cl or "session-browser" in cl):
                protected.add(pr.pid)
        except Exception:
            continue

    def proc_matches(pname: str) -> bool:
        # exact stem/word match only. A prefix match ("steam".startswith) would also
        # kill steamwebhelper / edgeupdate / notepad++updater — collateral damage.
        pn = pname.lower().removesuffix(".exe")
        return pn == stem or pn in words

    pids = {pr.pid for pr in psutil.process_iter(["name"])
            if proc_matches(pr.info["name"] or "") and pr.pid not in protected}
    windows = _visible_windows()
    targets = []                       # (hwnd, pid) windows we will ask to close
    for hwnd, title in windows:
        try:
            wpid = win32process.GetWindowThreadProcessId(hwnd)[1]
        except Exception:
            continue
        if wpid in protected:
            continue
        tl = title.lower()
        title_words = set(re.findall(r"[a-z0-9]+", tl))
        if wpid in pids or any(w in title_words for w in words):
            targets.append((hwnd, wpid))
    if not pids and not targets:
        return {"error": f"no running process matched {name}"}

    asked = 0
    for hwnd, _wpid in targets:
        try:
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
            asked += 1
        except Exception:
            pass

    watched = pids | {w for _h, w in targets}
    deadline = _time.time() + (3.0 if asked else 0.0)
    while _time.time() < deadline and any(_window_alive(h) for h, _ in targets):
        _time.sleep(0.2)
    gone_windows = not any(_window_alive(h) for h, _ in targets)
    if gone_windows and not any(psutil.pid_exists(p) for p in pids):
        return {"closed": name, "processes": len(watched)}

    killed = 0
    for pid in list(pids):             # windowless leftovers only
        if any(w == pid and _window_alive(h) for h, w in targets):
            continue
        try:
            psutil.Process(pid).terminate()
            killed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    _time.sleep(0.4)
    if gone_windows:
        return {"closed": name, "processes": max(1, killed)}
    if asked:
        return {"asked_to_close": name,
                "note": "still open - it is probably asking whether to save"}
    return {"error": f"could not close {name} (protected process)"}


def _window_alive(hwnd: int) -> bool:
    import win32gui
    try:
        return bool(win32gui.IsWindow(hwnd) and win32gui.IsWindowVisible(hwnd))
    except Exception:
        return False


async def _ddg_search(query: str, count: int) -> dict:
    """Keyless search via DuckDuckGo's HTML endpoint (no API, no account)."""
    from lxml import html as _html
    headers = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/126.0.0.0 Safari/537.36")}
    async with httpx.AsyncClient(timeout=12, follow_redirects=True,
                                 headers=headers, http2=True) as c:
        r = await c.post("https://html.duckduckgo.com/html/", data={"q": query})
        r.raise_for_status()
    doc = _html.fromstring(r.text)
    results = []
    for res in doc.cssselect("div.result"):
        a = res.cssselect("a.result__a")
        sn = res.cssselect(".result__snippet")
        if not a:
            continue
        href = a[0].get("href", "")
        # DDG wraps links: //duckduckgo.com/l/?uddg=<url>&...
        if "uddg=" in href:
            from urllib.parse import parse_qs, unquote, urlparse
            href = unquote(parse_qs(urlparse(href).query).get("uddg", [href])[0])
        results.append({"title": a[0].text_content().strip(), "url": href,
                        "snippet": sn[0].text_content().strip() if sn else ""})
        if len(results) >= count:
            break
    return {"query": query, "results": results, "provider": "duckduckgo"}


# Wikipedia's robot policy requires a descriptive User-Agent with a contact address.
# A generic browser UA gets a 403 with "Please respect our robot policy" — this is
# compliance, not a workaround.
WIKI_UA = ("JARVIS-Personal-Assistant/1.0 (local desktop assistant; "
           "contact: nicholas.coppola67@gmail.com)")

# What the last real search actually did. Diagnostics used to report "Web Search: ok"
# purely because Brave was installed, while every search had been returning zero results
# for who knows how long. A health check that cannot fail is not a health check.
LAST_SEARCH: dict = {"ts": 0.0, "provider": None, "results": None, "error": None}

SEARCH_BLOCKED = (
    "Web search is unavailable: DuckDuckGo and Brave Search are both serving bot "
    "challenges to automated requests, and no Brave Search API key is configured. "
    "Add a Brave Search API key in Settings (the free tier covers 2,000 searches a "
    "month) to restore full web search."
)


async def _wikipedia_search(query: str, count: int = 5) -> dict:
    """Encyclopedic fallback that is actually allowed to be used by a program.

    Not a replacement for web search — it will never know today's GPU price — but it
    answers the factual half of "research X" without a key, and it is a documented,
    permitted API rather than a scrape of a page that does not want to be scraped.
    """
    async with httpx.AsyncClient(timeout=15, follow_redirects=True,
                                 headers={"User-Agent": WIKI_UA}) as c:
        r = await c.get("https://en.wikipedia.org/w/api.php",
                        params={"action": "query", "list": "search", "srsearch": query,
                                "format": "json", "srlimit": min(count, 5)})
        r.raise_for_status()
        hits = r.json().get("query", {}).get("search", [])
        results = []
        for h in hits:
            title = h.get("title", "")
            summary = ""
            try:
                sr = await c.get("https://en.wikipedia.org/api/rest_v1/page/summary/"
                                 + title.replace(" ", "_"))
                if sr.status_code == 200:
                    summary = (sr.json().get("extract") or "")[:400]
            except Exception:
                pass
            results.append({
                "title": title,
                "url": "https://en.wikipedia.org/wiki/" + title.replace(" ", "_"),
                "snippet": summary or re.sub(r"<[^>]+>", "", h.get("snippet", "")),
                "host": "en.wikipedia.org",
            })
    # Wikipedia will happily return five articles for "current price of an RTX 5090".
    # Without this note the model treats that as a successful search, finds nothing useful
    # in it, and answers from memory anyway — confidently and wrongly ("the top-rated 2026
    # mini PC is the Intel NUC 13 Extreme", a 2022 product). Say what this is and is not.
    return {"query": query, "results": results, "provider": "wikipedia",
            "note": ("ENCYCLOPEDIA ONLY. Live web search is unavailable, so these are "
                     "Wikipedia articles, not current web results. They cannot answer "
                     "anything about present-day prices, news, releases or availability. "
                     "If they do not contain the answer, tell the user that live web "
                     "search is unavailable and a Brave Search API key is needed in "
                     "Settings — do NOT answer from your own memory.")}


def _note_search(out: dict) -> dict:
    import time as _t
    LAST_SEARCH.update({"ts": _t.time(), "provider": out.get("provider"),
                        "results": len(out.get("results") or []),
                        "error": out.get("error")})
    return out


async def _hn_search(query: str, count: int) -> list[dict]:
    """Hacker News via the Algolia API — official, keyless, and the results are links to
    the real articles (Tom's Hardware, Chips and Cheese, vendor pages), which fetch_page
    can then read. This is what makes tech questions answerable without a search key."""
    async with httpx.AsyncClient(timeout=15, headers={"User-Agent": WIKI_UA}) as c:
        r = await c.get("https://hn.algolia.com/api/v1/search",
                        params={"query": query, "tags": "story", "hitsPerPage": count})
        r.raise_for_status()
        out = []
        for h in r.json().get("hits", []):
            url = h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID')}"
            out.append({"title": h.get("title") or "", "url": url,
                        "snippet": (h.get("story_text") or "")[:240]
                                   or f"{h.get('points', 0)} points, {h.get('num_comments', 0)} comments"
                                      f" — discussed {(h.get('created_at') or '')[:10]}",
                        "host": "news.ycombinator.com"})
        return [o for o in out if o["title"]]


async def _stackexchange_search(query: str, count: int) -> list[dict]:
    """Stack Exchange's public API — keyless at low volume, and unbeatable for the
    "does hardware X support Y" questions that a manual never answers plainly."""
    async with httpx.AsyncClient(timeout=15, headers={"User-Agent": WIKI_UA}) as c:
        out = []
        for site in ("superuser", "stackoverflow"):
            try:
                r = await c.get("https://api.stackexchange.com/2.3/search/advanced",
                                params={"order": "desc", "sort": "relevance", "q": query,
                                        "site": site, "pagesize": count, "filter": "default"})
                if r.status_code != 200:
                    continue
                import html as _html
                for i in r.json().get("items", []):
                    # the API returns HTML-escaped titles ("Freeze on 9950X3D &amp; ...")
                    out.append({"title": _html.unescape(i.get("title", "")),
                                "url": i.get("link", ""),
                                "snippet": f"{i.get('score', 0)} votes"
                                           + (", answered" if i.get("is_answered") else ", unanswered"),
                                "host": f"{site}.com"})
            except Exception:
                continue
            if out:
                break
        return out


async def _keyless_search(query: str, count: int) -> dict:
    """Everything still open to a program, merged.

    General web search is bot-blocked, but these three are documented public APIs that
    are meant to be called. Between them they cover most of what he actually asks:
    encyclopedic facts, hardware/tech news, and "does X support Y".
    """
    import asyncio as _a
    # Keyword indexes match terms, not sentences: "latest news about AMD Strix Halo"
    # scored nothing on Hacker News while "AMD Strix Halo" returned Tom's Hardware and
    # Chips and Cheese. Strip the framing and search the subject.
    terms = re.sub(
        r"^\s*(?:please\s+)?(?:can you\s+)?(?:search(?:\s+the\s+web)?(?:\s+for)?|look\s+up|"
        r"find\s+(?:out|me)?|research|tell me about|what(?:'s| is| are)|show me|get me)\s+",
        "", query, flags=re.I)
    terms = re.sub(r"\b(?:the\s+)?(?:latest|current|recent|newest|today'?s)\s+"
                   r"(?:news|price|prices|info|information|updates?)\s+(?:on|about|for|of)\s+",
                   "", terms, flags=re.I).strip() or query
    jobs = {
        "wikipedia": _wikipedia_search(query, count),
        "hackernews": _hn_search(terms, count),
        "stackexchange": _stackexchange_search(terms, count),
    }
    done = await _a.gather(*jobs.values(), return_exceptions=True)
    lanes, used = [], []
    for name, res in zip(jobs.keys(), done):
        if isinstance(res, Exception):
            log.warning("%s search failed: %s", name, str(res)[:80])
            continue
        items = res.get("results", []) if isinstance(res, dict) else res
        if items:
            used.append(name)
            lanes.append(items)
    # Round-robin, not concatenate. Wikipedia always returns five articles for anything,
    # so appending buried the Hacker News hits — the ones that actually answered "latest
    # news about Strix Halo" — below the fold where the model never read them.
    merged, seen = [], set()
    for i in range(max((len(x) for x in lanes), default=0)):
        for lane in lanes:
            if i < len(lane) and lane[i].get("url") and lane[i]["url"] not in seen:
                seen.add(lane[i]["url"])
                merged.append(lane[i])
    if not merged:
        return {}
    return {"query": query, "results": merged[: count * 2], "provider": "+".join(used),
            "note": ("NO GENERAL WEB SEARCH. These come from Wikipedia, Hacker News and "
                     "Stack Exchange only. They are real sources and you may use and cite "
                     "them, and you may open one with fetch_page to read it. They will NOT "
                     "contain today's prices, stock or availability. If they do not answer "
                     "the question, say live web search is unavailable and that a Brave "
                     "Search API key is needed in Settings — never answer from memory.")}


async def web_search(query: str, count: int = 5) -> dict:
    return _note_search(await _web_search(query, count))


async def _web_search(query: str, count: int = 5) -> dict:
    key = secrets.get("brave_api_key")
    from events import bus
    await bus.emit("web", stage="searching", query=query)
    if not key:
        # No key needed: drive the user's installed Brave browser (hidden).
        from search_brave_web import brave_web
        if brave_web.available:
            try:
                results = await brave_web.search(query, count)
                if results:
                    await bus.emit("web", stage="results", query=query, results=results)
                    return {"query": query, "results": results, "provider": "brave-browser"}
                await bus.emit("web", stage="empty", query=query)
            except Exception as e:
                log.warning("brave browser search failed: %s", e)
                await bus.emit("web", stage="error", query=query, error=str(e)[:120])
        try:
            ddg = await _ddg_search(query, count)
            if ddg.get("results"):
                await bus.emit("web", stage="results", query=query, results=ddg["results"])
                return ddg
        except Exception as e:
            log.warning("ddg search failed: %s", e)
        try:
            keyless = await _keyless_search(query, count)
            if keyless.get("results"):
                await bus.emit("web", stage="results", query=query, results=keyless["results"])
                return keyless
        except Exception as e:
            log.warning("keyless search failed: %s", e)
        # Every keyless route is bot-blocked. Say so plainly instead of returning an empty
        # list, which the model reads as "nothing exists" and reports as "I couldn't find
        # reliable information" — leaving nobody any idea that search is simply switched off.
        await bus.emit("web", stage="blocked", query=query, error=SEARCH_BLOCKED)
        return {"query": query, "results": [], "error": SEARCH_BLOCKED, "blocked": True}
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": min(count, 10)},
            headers={"X-Subscription-Token": key, "Accept": "application/json"},
        )
        r.raise_for_status()
        data = r.json()
    results = [
        {"title": w.get("title"), "url": w.get("url"),
         "snippet": w.get("description")}
        for w in (data.get("web", {}).get("results") or [])[:count]
    ]
    return {"query": query, "results": results}


def read_file(path: str, max_chars: int = 4000) -> dict:
    p = Path(path).expanduser()
    if not p.is_absolute():
        for root in _ALLOWED_READ_ROOTS:
            cand = root / path
            if cand.exists():
                p = cand
                break
    try:
        p = p.resolve()
    except OSError:
        return {"error": "invalid path"}
    if not any(str(p).lower().startswith(str(r.resolve()).lower()) for r in _ALLOWED_READ_ROOTS):
        return {"error": f"reading outside allowed folders (Documents/Downloads/Desktop/Pictures) "
                         f"is not permitted: {p}"}
    if not p.exists() or not p.is_file():
        return {"error": f"file not found: {p}"}
    if p.stat().st_size > 5_000_000:
        return {"error": "file too large to read directly"}
    try:
        text = p.read_text("utf-8", errors="replace")
    except Exception as e:
        return {"error": f"could not read file: {e}"}
    truncated = len(text) > max_chars
    return {"path": str(p), "truncated": truncated, "content": text[:max_chars]}


def watch_metric(metric: str, op: str, value: float, for_min: int = 0, message: str = "") -> dict:
    """User-defined proactive rule: JARVIS speaks up when a system metric crosses a line."""
    from proactive import proactive
    if metric not in proactive.METRICS or op not in (">", "<"):
        return {"error": "metric must be cpu|ram|disk_free_gb|battery and op > or <"}
    rule = {"metric": metric, "op": op, "value": float(value), "for_min": int(for_min or 0)}
    if message:
        rule["message"] = message
    proactive.add_rule(rule)
    return {"watching": f"I'll tell you if {proactive.describe(rule)}.", "rules": len(proactive.rules())}


def unwatch_metric(metric: str | None = None) -> dict:
    from proactive import proactive
    return {"removed": proactive.remove_rules(metric)}


def register_all() -> None:
    registry.register(Tool(
        name="watch_metric",
        description="Set a standing alert: JARVIS will tell the user when a system metric crosses a "
                    "threshold (cpu/ram/battery in percent, disk_free_gb in gigabytes), optionally "
                    "only after it has held for for_min minutes.",
        parameters={"type": "object", "properties": {
            "metric": {"type": "string", "enum": ["cpu", "ram", "disk_free_gb", "battery"]},
            "op": {"type": "string", "enum": [">", "<"]},
            "value": {"type": "number"}, "for_min": {"type": "integer"}, "message": {"type": "string"}},
            "required": ["metric", "op", "value"]},
        risk=Risk.LOW, handler=watch_metric))
    registry.register(Tool(
        name="unwatch_metric",
        description="Remove standing metric alerts (all, or for one metric).",
        parameters={"type": "object", "properties": {"metric": {"type": "string"}}, "required": []},
        risk=Risk.LOW, handler=unwatch_metric))
    registry.register(Tool(
        name="get_system_stats",
        description="Get current CPU, RAM, disk, battery, and process stats for this PC.",
        parameters={"type": "object", "properties": {}, "required": []},
        risk=Risk.SAFE, handler=get_system_stats))
    registry.register(Tool(
        name="open_application",
        description="Launch an application on this PC by name, e.g. 'chrome', 'spotify', 'notepad'.",
        parameters={"type": "object", "properties": {
            "name": {"type": "string", "description": "Application name"}},
            "required": ["name"]},
        risk=Risk.LOW, handler=open_application))
    registry.register(Tool(
        name="close_application",
        description="Close a running application by name.",
        parameters={"type": "object", "properties": {
            "name": {"type": "string", "description": "Application name"}},
            "required": ["name"]},
        risk=Risk.LOW, handler=close_application))
    registry.register(Tool(
        name="web_search",
        description="Search the web for current information. Returns titles, URLs and snippets.",
        parameters={"type": "object", "properties": {
            "query": {"type": "string"},
            "count": {"type": "integer", "minimum": 1, "maximum": 10}},
            "required": ["query"]},
        risk=Risk.LOW, handler=web_search, timeout=45))
    registry.register(Tool(
        name="read_file",
        description="Read a text file from the user's Documents, Downloads, Desktop or Pictures folders.",
        parameters={"type": "object", "properties": {
            "path": {"type": "string", "description": "File path or name"}},
            "required": ["path"]},
        risk=Risk.LOW, handler=read_file))
