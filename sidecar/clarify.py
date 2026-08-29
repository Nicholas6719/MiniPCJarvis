"""When a request splits two ways, ask — but don't stand still while asking.

"Any news on Tesla" means either the company (a new model, a recall, a factory)
or the stock (the price, the analysts). Guessing wastes a whole turn: he answers
the wrong half and has to be asked again. Asking costs a round trip.

So do both at once. The moment the question is put, every reading it could have
starts fetching in the background. By the time the answer comes back — "the
stock" — that branch is already warm and the losers are cancelled. The
clarification costs the time it takes to say two words, not the time it takes to
fetch an answer.

RULES, enforced rather than assumed:
  * only read-only lookups may run on speculation. A branch whose skill needs
    confirmation is refused when the ambiguity is built — nothing that sends,
    buys, opens or deletes can ever be run on a guess about what was meant.
  * at most 3 branches, so a vague question can't fan out into a stampede.
  * everything is cancelled on an answer, on an unrelated request, on sleep, or
    when the question goes stale.

The engine here is general; the list of ambiguities it knows about is short and
deliberately so. Asking a model "how many ways could this be read?" costs the
seconds this exists to save.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field

log = logging.getLogger("jarvis.clarify")

TTL_S = 75.0          # after this the question is stale and the fetches are dropped
MAX_BRANCHES = 3
MAX_ANSWER_WORDS = 4  # longer than this is a new sentence, not an answer


@dataclass(frozen=True)
class Branch:
    """One reading of an ambiguous request.

    It carries its own renderer rather than borrowing a reflex skill's, because
    the best source for a reading is not always a skill: "the company" is
    answered by a web search, which no skill speaks on its own.
    """
    label: str                    # how it is offered: "the stock"
    tool: str                     # what to fetch, on speculation
    args: dict                    # its arguments, already filled in
    words: tuple[str, ...]        # what he might say to pick it
    render: object                # (args, result) -> the line he hears


@dataclass
class Ambiguity:
    subject: str
    question: str
    branches: tuple[Branch, ...]


@dataclass
class Pending:
    """A question he has been asked, with its answers already on the way."""
    amb: Ambiguity
    tasks: dict[str, asyncio.Task] = field(default_factory=dict)
    asked_at: float = field(default_factory=time.time)

    @property
    def stale(self) -> bool:
        return time.time() - self.asked_at > TTL_S

    def cancel(self, keep: str | None = None) -> None:
        for label, t in self.tasks.items():
            if label != keep and not t.done():
                t.cancel()


# --- what he might say back ---------------------------------------------------
_BOTH = re.compile(r"\b(?:both|either|all of (?:it|them)|everything)\b", re.I)
_DROP = re.compile(r"\b(?:never ?mind|forget it|neither|nothing|no thanks|"
                   r"don'?t worry|skip it|cancel)\b", re.I)


def choose(pending: Pending, text: str) -> Branch | str | None:
    """Which reading he meant: a Branch, "both", "drop", or None for "not an answer"."""
    t = (text or "").strip().lower()
    if not t:
        return None
    # An answer to "the company or the stock?" is two or three words. A whole
    # sentence is a new request that happens to contain one of them — without
    # this, "what's the stock market doing" answers a question about Tesla.
    if len(t.split()) > MAX_ANSWER_WORDS:
        return None
    if _DROP.search(t):
        return "drop"
    hits = [(sum(1 for w in b.words if re.search(r"\b" + re.escape(w) + r"\b", t)), b)
            for b in pending.amb.branches]
    best = max(hits, key=lambda p: p[0])
    if _BOTH.search(t):
        return "both"
    if best[0] == 0:
        return None                     # he said something else entirely
    # two readings matched equally: that is not an answer, it is a new sentence
    if sum(1 for n, _ in hits if n == best[0]) > 1:
        return None
    return best[1]


# --- the ambiguities he knows about -------------------------------------------
# Companies whose name is ALSO an everyday subject of ordinary news. The point of
# the list is not "is this a company" — it is "would a person plausibly mean
# either". Extending it is one line.
_COMPANIES = {
    "tesla": "Tesla", "apple": "Apple", "amazon": "Amazon", "google": "Google",
    "alphabet": "Alphabet", "microsoft": "Microsoft", "meta": "Meta",
    "facebook": "Facebook", "nvidia": "Nvidia", "netflix": "Netflix",
    "openai": "OpenAI", "intel": "Intel", "amd": "AMD", "boeing": "Boeing",
    "ford": "Ford", "disney": "Disney", "starbucks": "Starbucks", "nike": "Nike",
    "uber": "Uber", "spotify": "Spotify", "coinbase": "Coinbase",
    "palantir": "Palantir", "rivian": "Rivian", "lucid": "Lucid",
    "samsung": "Samsung", "sony": "Sony", "walmart": "Walmart", "target": "Target",
}

# "any news on X", "what's happening with X", "anything on X", "update on X".
# Deliberately NOT "how is X stock doing" or "what's X trading at" — those say
# which half they want, and interrupting them to ask would be maddening.
_LEAD = r"^\s*(?:so\s+|and\s+|hey\s+)?"
_SUBJECT = r"(?P<subject>[A-Za-z][A-Za-z0-9&.' -]{1,40}?)\s*[.?!]*\s*$"
_VAGUE = (
    # "any news on X", "what's the latest with X", "give me an update on X"
    re.compile(_LEAD +
               r"(?:any(?:thing)?|what'?s|whats|give me|tell me|got any|is there any)?\s*"
               r"(?:an?\s+|the\s+)?(?:new|news|latest|update|updates|happening|"
               r"going on|word)\b[^.?!]*?\b(?:on|about|with|for|from)\s+" + _SUBJECT, re.I),
    # "anything on X", "what about X", "what's up with X" — no news word at all,
    # and every bit as ambiguous
    re.compile(_LEAD + r"(?:any ?thing|what about|what'?s up|whats up|how about)\s+"
               r"(?:on|about|with|for)?\s*" + _SUBJECT, re.I),
)

# If he already said which half he means, there is nothing to ask about.
_ALREADY_STOCK = re.compile(
    r"\b(?:stock|stocks|share|shares|price|quote|ticker|trading|analyst|analysts|"
    r"earnings|market cap|valuation|buy|sell|dividend)\b", re.I)
_ALREADY_COMPANY = re.compile(
    r"\b(?:car|cars|model|models|product|products|launch|launched|release|recall|"
    r"factory|lawsuit|ceo|hiring|layoffs|announcement|announced)\b", re.I)


def detect(text: str) -> Ambiguity | None:
    """An ambiguity worth one short question, or None to answer as usual."""
    t = (text or "").strip()
    if not t or _ALREADY_STOCK.search(t) or _ALREADY_COMPANY.search(t):
        return None
    m = next((hit for hit in (p.search(t) for p in _VAGUE) if hit), None)
    if not m:
        return None
    raw = re.sub(r"^(?:the|a|an)\s+", "", m.group("subject").strip(), flags=re.I)
    proper = _COMPANIES.get(raw.lower())
    if not proper:
        return None
    return Ambiguity(
        subject=proper,
        question="The company or the stock, sir?",
        branches=(
            # get_news SEARCHES for a named subject rather than sieving the
            # general feeds, so this returns actual stories about the company.
            # Finnhub's company news was the other candidate and is investor
            # commentary — "Most active S&P500 stocks" is not what anybody means
            # by news about Tesla.
            # two headlines, not three: three is about twenty seconds of talking
            # at him, and he can always ask for more
            Branch("the company", "get_news", {"query": proper, "count": 2},
                   ("company", "business", "product", "products", "car", "cars",
                    "firm", "them", "itself", "generally"),
                   render=lambda a, r, name=proper: _say_headlines(name, r)),
            Branch("the stock", "get_stock_quote", {"symbol": proper},
                   ("stock", "stocks", "share", "shares", "price", "ticker",
                    "trading", "market", "financial", "financially"),
                   render=lambda a, r: _say_quote(a, r)),
        ),
    )


def _say_headlines(subject: str, res: dict) -> str:
    """Headlines, spoken. The story and who ran it — a summary read aloud is a wall."""
    if not isinstance(res, dict) or res.get("error"):
        return f"Nothing came back on {subject} just now, sir."
    rows = [r for r in (res.get("items") or []) if r.get("headline")]
    if not rows:
        return f"Nothing came back on {subject} just now, sir."
    heads = " ".join(f"{str(r['headline']).rstrip('. ')}, from {r.get('source', 'the news')}."
                     for r in rows[:2])
    return f"On {subject}: {heads}"


def _say_quote(args: dict, res: dict) -> str:
    """The reflex skill's own wording, so a clarified answer sounds identical to
    the one he would have given if the question had been unambiguous."""
    from brain.skills import say_quote
    return say_quote(args, res)


def validate(amb: Ambiguity, risk_of) -> bool:
    """Refuse to speculate on anything that acts. `risk_of(tool) -> bool` says
    whether that tool needs confirmation; an unknown tool counts as unsafe."""
    if not amb.branches or len(amb.branches) > MAX_BRANCHES:
        return False
    for b in amb.branches:
        try:
            if risk_of(b.tool):
                log.warning("refusing to speculate on %r - it needs confirmation", b.tool)
                return False
        except Exception:
            return False
    return True
