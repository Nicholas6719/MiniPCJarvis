"""What "it" means.

"Send it to my phone." "Give me the article." Neither sentence says what "it" is,
because a person would not — it is whatever he was just told about. So something
has to remember the last thing that had a subject: the words spoken, and any
links behind them.

Filled from the tool results themselves rather than from the model's answer, so a
headline he heard has the URL it came from, and asking for "the article" opens
the thing he actually heard about.
"""
from __future__ import annotations

import time

MAX_LINKS = 8
STALE_S = 900.0          # after fifteen minutes, "it" no longer means anything


class LastSeen:
    def __init__(self) -> None:
        self.text: str = ""
        self.links: list[dict] = []      # [{title, url, source}]
        self.at: float = 0.0

    @property
    def stale(self) -> bool:
        return not self.at or (time.time() - self.at) > STALE_S

    def note_reply(self, text: str) -> None:
        text = (text or "").strip()
        if text:
            self.text = text
            self.at = time.time()

    def note_result(self, result) -> None:
        """Harvest links out of whatever a tool just returned.

        Every source shapes its rows differently — search results carry `url` and
        `title`, news carries `url` and `headline`, company news the same — so
        take anything that looks like a link rather than teaching this about each.
        """
        found: list[dict] = []
        self._walk(result, found, depth=0)
        if found:
            self.links = found[:MAX_LINKS]
            self.at = time.time()

    def _walk(self, node, out: list[dict], depth: int) -> None:
        if depth > 4 or len(out) >= MAX_LINKS:
            return
        if isinstance(node, dict):
            url = node.get("url") or node.get("link") or node.get("href")
            if isinstance(url, str) and url.startswith("http"):
                title = (node.get("title") or node.get("headline")
                         or node.get("name") or "")
                out.append({"title": str(title)[:200], "url": url,
                            "source": str(node.get("source") or "")[:60]})
            for v in node.values():
                self._walk(v, out, depth + 1)
        elif isinstance(node, list):
            for v in node:
                self._walk(v, out, depth + 1)

    def clear(self) -> None:
        self.text, self.links, self.at = "", [], 0.0


last_seen = LastSeen()
