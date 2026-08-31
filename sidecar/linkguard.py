"""No link reaches him that a tool did not actually return.

On 2026-08-31 he asked for Amazon links for a 3D-printer build. JARVIS ran six
real searches, got ten results each, ignored every one of them, and invented the
URLs from the model's memory:

    https://www.amazon.com/Adhesive-Sheet-3D-Printer/dp/B08XYZ1234
    https://www.amazon.com/Ultimaker-2-Plus-Printer/dp/B07V4ZK5YB

`B08XYZ1234` is not an ASIN, it is a placeholder - which is the tell. The others
are the same thing wearing a better disguise. He was going to click them.

This is the worst shape a mistake can take: specific, confident, trivially
checkable, and wrong. And unlike a wrong opinion it cannot be argued with - a
fabricated link either resolves or it does not, and his trust in every other link
goes with it.

So the rule here is mechanical, not persuasive. Prompting a model to "only use
real URLs" is a request; this is a gate. Every URL a tool hands back during a
turn is written down, and any URL in the reply that is not on that list does not
go out. A missing link is a small disappointment. A fabricated one is a lie.
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger("jarvis.linkguard")

# Deliberately broad: better to examine a URL and allow it than to miss one.
URL_RE = re.compile(r"https?://[^\s\)\]\}<>\"'`]+", re.I)

# Trailing punctuation a sentence leaves stuck to a URL.
TRAILING = ".,;:!?)]}’”'\""


def _canon(url: str) -> str:
    """Compare links the way a browser would, not byte for byte."""
    u = str(url or "").strip().rstrip(TRAILING).lower()
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"^www\.", "", u)
    u = u.rstrip("/")
    return u


class LinkLedger:
    """What the tools really returned this turn."""

    def __init__(self) -> None:
        self._seen: set[str] = set()
        self._hosts: set[str] = set()
        # url -> the title it came with, so a real link can be offered by name
        self._titled: dict[str, str] = {}
        self._saw_price = False

    def clear(self) -> None:
        self._seen.clear()
        self._hosts.clear()
        self._titled.clear()
        self._saw_price = False

    def note(self, result) -> None:
        """Record every URL anywhere in a tool result, at any depth."""
        self._walk(result, 0)
        self._harvest(result, 0)

    def _harvest(self, node, depth: int) -> None:
        """Pair a url with its title, and notice whether any tool saw a price."""
        if depth > 6:
            return
        if isinstance(node, dict):
            url = node.get("url") or node.get("link") or node.get("href")
            if isinstance(url, str) and url.startswith("http"):
                title = (node.get("title") or node.get("headline")
                         or node.get("name") or "")
                # Store the URL AS GIVEN. Keying on the canonical form and
                # rebuilding from it handed him "amazon.com/dp/b08663txws" -
                # lower-cased, and an ASIN is case-sensitive, so the repaired
                # link was as dead as the invented one it replaced.
                self._titled.setdefault(_canon(url), (url, str(title or "")[:120]))
            for k, v in node.items():
                if "price" in str(k).lower() and v not in (None, "", 0):
                    self._saw_price = True
                self._harvest(v, depth + 1)
        elif isinstance(node, (list, tuple)):
            for v in node:
                self._harvest(v, depth + 1)

    @property
    def saw_price(self) -> bool:
        """Did any tool actually return a price this turn?"""
        return self._saw_price

    def title_for(self, url: str) -> str:
        """What the SOURCE calls this page, as opposed to what the model called it."""
        c = _canon(url)
        hit = self._titled.get(c)
        if hit:
            return hit[1]
        for known, (_orig, title) in self._titled.items():
            if c.startswith(known) or known.startswith(c):
                return title
        return ""

    def offer(self, limit: int = 5) -> list[tuple[str, str]]:
        """Real (url, title) pairs, for when he asked for links and got none."""
        out = []
        for original, title in self._titled.values():
            out.append((original, title))
            if len(out) >= limit:
                break
        return out

    def _walk(self, node, depth: int) -> None:
        if depth > 6:
            return
        if isinstance(node, str):
            if node.startswith(("http://", "https://")):
                self._add(node)
            return
        if isinstance(node, dict):
            for v in node.values():
                self._walk(v, depth + 1)
        elif isinstance(node, (list, tuple)):
            for v in node:
                self._walk(v, depth + 1)

    def _add(self, url: str) -> None:
        c = _canon(url)
        if not c:
            return
        self._seen.add(c)
        host = c.split("/")[0]
        if host:
            self._hosts.add(host)

    def allows(self, url: str) -> bool:
        """True if a tool returned this, or the page it obviously came from.

        A model habitually shortens a result URL to the article it points at, or
        drops a tracking query. Matching on prefix in either direction keeps
        those, while an invented `/dp/B08XYZ1234` still has nothing to match.
        """
        c = _canon(url)
        if not c:
            return False
        if c in self._seen:
            return True
        for known in self._seen:
            if c.startswith(known) or known.startswith(c):
                return True
        # A bare host he was genuinely shown ("amazon.com") is not a claim about
        # a specific product, so it is allowed on its own. A PATH under a host is
        # a specific claim and has to have been returned.
        return c in self._hosts

    @property
    def count(self) -> int:
        return len(self._seen)


def check(text: str, ledger: LinkLedger) -> tuple[str, list[str]]:
    """(text with invented links removed, the links that were removed).

    The surrounding sentence is kept. He asked what the things are and what they
    cost; losing the link is a smaller loss than losing the answer, and the note
    added by `explain()` tells him why it is missing rather than leaving him to
    wonder - which is what happened the first time, when he had to ask twice.
    """
    if not text:
        return text, []
    removed: list[str] = []

    def _sub(m: re.Match) -> str:
        raw = m.group(0)
        bare = raw.rstrip(TRAILING)
        tail = raw[len(bare):]
        if ledger.allows(bare):
            return raw
        removed.append(bare)
        return tail

    cleaned = URL_RE.sub(_sub, text)
    # tidy the punctuation left where a link used to be: "printer: ," -> "printer"
    cleaned = re.sub(r"[:\-–—]\s*(?=[,.;]|$)", "", cleaned, flags=re.M)
    cleaned = re.sub(r"\(\s*\)", "", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r" +([,.;:])", r"\1", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip(), removed


def explain(removed: list[str]) -> str:
    """What to tell him in place of the links, honestly."""
    if not removed:
        return ""
    n = len(removed)
    return (" I left out "
            + ("a link" if n == 1 else f"{n} links")
            + " I couldn't verify — I'd rather send none than send you a dead one.")


# Did he ask to be handed something he can click? "Thank you Jarvis but there's
# no links I can click on" was him asking a second time for what he had already
# asked for the first time.
WANTS_LINKS = re.compile(
    r"\b(?:links?|url|urls|listing|listings|where to buy|link me|clickable)\b",
    re.I)


def wanted_links(text: str) -> bool:
    return bool(WANTS_LINKS.search(str(text or "")))


def supply(reply: str, ledger: LinkLedger, limit: int = 5) -> str:
    """He asked for links and the reply carries none. Offer the real ones.

    Blocking the invented links is only half the job. On its own it would leave
    him exactly where he started - a list of products with nothing to click -
    which is what made him ask twice. This hands over what the searches actually
    returned, or says plainly that there was nothing worth standing behind.
    """
    already = {_canon(u) for u in URL_RE.findall(reply or "")}
    # Not "does the reply have A link" - he asked about five things and the guard
    # left one standing. Bailing on the first survivor would have handed him one
    # link out of five and called it done. Offer whatever is still missing.
    have = [(u, t) for u, t in ledger.offer(limit) if _canon(u) not in already]
    if not have:
        if already:
            return reply
        return (reply.rstrip()
                + " I couldn't find links I can stand behind for those, sir.")
    lines = [f"{title or url}: {url}" for url, title in have]
    return reply.rstrip() + "\n\n" + "\n".join(lines)


# Words too common to prove that a caption and a page are about the same thing.
_DULL = {"the", "and", "for", "with", "from", "your", "our", "best", "top", "new",
         "this", "that", "here", "free", "download", "online", "official", "site",
         "page", "com", "www", "http", "https", "amazon", "shop", "store", "buy",
         "price", "prices", "deal", "deals", "review", "reviews", "guide", "how"}


def _meaningful(text: str) -> set[str]:
    return {w for w in re.sub(r"[^a-z0-9 ]", " ", str(text or "").lower()).split()
            if len(w) > 3 and w not in _DULL}


def check_captions(reply: str, ledger: LinkLedger) -> tuple[str, int]:
    """Does the link point at what he was told it points at?

    The guard above proves a URL came out of a tool. It cannot prove the model
    described it correctly - a search returns a page about beginner 3D printers
    and the model captions it "PLA filament, $30", and every check so far is
    perfectly happy. The link is real, the sentence is not.

    So each surviving link is compared against the title the SOURCE gave itself.
    Where they share nothing, the source's own title is added rather than the
    caption being rewritten: a machine guessing at his sentence is how this went
    wrong in the first place, and he can see both and judge.
    """
    if not reply:
        return reply, 0
    flagged = 0
    out_lines = []
    for line in reply.split("\n"):
        annotated = line
        for url in URL_RE.findall(line):
            title = ledger.title_for(url)
            if not title:
                continue
            caption = line[:line.find(url)]
            if _meaningful(caption) & _meaningful(title):
                continue                      # they are talking about the same thing
            if _meaningful(title) <= _meaningful(caption):
                continue
            annotated = annotated.replace(url, f"{url} [{title.strip()}]", 1)
            flagged += 1
        out_lines.append(annotated)
    return "\n".join(out_lines), flagged


def price_caveat(reply: str, ledger: LinkLedger) -> str:
    """Prices he was quoted that no tool actually looked up.

    The Ultimaker 2+ was quoted at $1,500; it is discontinued. Stating a price as
    current fact when it came out of the model's memory is the same class of
    mistake as the invented links, only harder for him to catch.
    """
    if ledger.saw_price or not re.search(r"[$£€]\s?\d", reply or ""):
        return reply
    body = (reply or "").rstrip()
    # If the reply ends in a list of links, a sentence stuck on the end of the
    # last one reads as part of the URL. Give it its own line.
    sep = "\n\n" if URL_RE.search(body.rsplit("\n", 1)[-1]) else " "
    return body + sep + "Those prices are from memory, not looked up just now."
