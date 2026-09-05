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
    # Whether this reading may be run BEFORE he answers. True for a lookup —
    # that is the whole point of the engine, and a wasted read costs nothing.
    #
    # False for a reading that DOES something. "Slice it" is either a cross
    # section of the hologram or a run through PrusaSlicer, and neither is a
    # lookup: speculating would cut the model open on screen while he is still
    # being asked which he meant, and start a real slice for an answer he might
    # not give. A deferred branch is asked about and then run — the question
    # costs a round trip instead of saving one, which is the correct trade when
    # the alternative is doing both things.
    speculative: bool = True


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
# Words that carry no meaning in a two-word answer: "leave it", "yes please",
# "go ahead then, jarvis".
_FILLER = frozenset({"it", "that", "then", "please", "sir", "jarvis", "thanks",
                     "thank", "you", "the", "and", "just", "now"})


def choose(pending: Pending, text: str) -> Branch | str | None:
    """Which reading he meant: a Branch, "both", "drop", or None for "not an answer"."""
    t = (text or "").strip().lower()
    if not t:
        return None
    # An answer to "the company or the stock?" is two or three words. A whole
    # sentence is a new request that happens to contain one of them — without
    # this, "what's the stock market doing" answers a question about Tesla.
    names = {b.label for b in pending.amb.branches}
    # A yes may run to a few more words ("yes, go ahead and render it") and is
    # still safe, because the approval branch below only takes an utterance
    # made ENTIRELY of its own words. A choice between readings stays short.
    if len(t.split()) > (MAX_ANSWER_WORDS + 3 if names == {"go ahead", "leave it"}
                         else MAX_ANSWER_WORDS):
        return None
    # A COST QUESTION IS ANSWERED BY A YES OR A NO, NOT BY A WORD. "Shall I?"
    # used to be answered by counting word hits, and the approval branch's
    # words include "on", "do", "go", "please", "start" and "fine" — so "go to
    # sleep", "turn on the camera", "please open spotify" and "do I have
    # reminders" all counted as "go ahead", and a multi-minute render started
    # while he was asking for something else. For an approval the whole
    # utterance has to BE a yes or a no; anything with a subject in it is the
    # new request it sounds like.
    names = {b.label for b in pending.amb.branches}
    if names == {"go ahead", "leave it"}:
        from orchestrator import NO_WORDS, YES_WORDS     # lazy: orchestrator imports us
        go = next(b for b in pending.amb.branches if b.label == "go ahead")
        leave = next(b for b in pending.amb.branches if b.label == "leave it")
        # "Carry on" and "leave it" are answers too: an utterance made ENTIRELY
        # of one branch's words (plus filler) counts. "go to sleep" has "sleep"
        # in it, so it does not. A "never mind" here is a decline, not a
        # dropped question — declining is a real answer with its own line.
        toks = [w for w in re.findall(r"[a-z']+", t) if w not in _FILLER]

        def only(b) -> bool:
            return bool(toks) and all(w in b.words for w in toks)
        if NO_WORDS.match(t) or _DROP.search(t) or only(leave):
            return leave
        if YES_WORDS.match(t) or only(go):
            return go
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


_SLICE = re.compile(r"^(?:can you |could you |please )?slice (?:it|that|this|the model|the part)"
                    r"[.!?]?$", re.I)


def _slice_ambiguity(t: str) -> Ambiguity | None:
    """"Slice it" means two completely different things once a model is up.

    A cross section — cut it open so he can see inside — or a run through
    PrusaSlicer to produce the G-code a printer eats. Both are reasonable
    readings of the same two words, and guessing wrong is expensive in opposite
    directions: cut it open when he wanted G-code and he waits for a file that
    is not coming; run the slicer when he wanted to look inside and he waits
    thirty seconds for nothing he asked for.

    It is ONLY ambiguous while something is on the stage. With no hologram up
    there is nothing to cross-section, so "slice it" plainly means the slicer
    and asking would be pedantry. Neither branch speculates: see Branch.
    """
    if not _SLICE.match(t):
        return None
    try:
        from tools.holo_tools import current
        up = current()
    except Exception:
        return None
    if not up.get("path"):
        return None
    name = up.get("name") or ""
    return Ambiguity(
        subject=name or "the model",
        question="A cross section, or slice it for the printer, sir?",
        branches=(
            Branch("a cross section", "holo_control",
                   {"action": "section", "axis": "z", "at": 0.5},
                   ("section", "cross", "cut", "open", "inside", "look", "view",
                    "visual", "see"),
                   render=lambda a, r: (r or {}).get("spoken") or "Cutting it open, sir.",
                   speculative=False),
            Branch("for the printer", "slice_part",
                   {"stl_path": up.get("path", "")},
                   ("printer", "print", "gcode", "g-code", "prusa", "slicer",
                    "properly", "real", "file"),
                   render=lambda a, r: _say_slice(r),
                   speculative=False),
        ),
    )


def _say_slice(res: dict) -> str:
    """What the slicer actually reported — never a number nobody produced."""
    if not isinstance(res, dict) or res.get("error"):
        return f"The slicer wouldn't take it, sir — {(res or {}).get('error', 'no idea why')}."
    bits = []
    if res.get("print_time"):
        bits.append(f"about {res['print_time']}")
    if res.get("filament_g"):
        bits.append(f"{res['filament_g']} grams of filament")
    line = "Sliced, sir" + (" — " + " and ".join(bits) if bits else "") + "."
    if res.get("mesh_warning"):
        line += f" Though {res['mesh_warning']}."
    return line


def detect(text: str) -> Ambiguity | None:
    """An ambiguity worth one short question, or None to answer as usual."""
    t = (text or "").strip()
    if not t:
        return None
    slice_amb = _slice_ambiguity(t)
    if slice_amb:
        return slice_amb
    if _ALREADY_STOCK.search(t) or _ALREADY_COMPANY.search(t):
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


def approval(subject: str, question: str, tool: str, args: dict, render,
             *, yes_words: tuple = (), no_words: tuple = ()) -> Ambiguity:
    """"About two minutes, sir. Shall I?" — a COST question, not a risk one.

    His correction: an estimate on its own is not enough, because he may not want
    to spend an hour. So JARVIS says how long and asks.

    Deliberately NOT the risk gate. `generate_part` writes a file and is honestly
    LOW; promoting it to MEDIUM to force a confirmation would corrupt what the
    tier means — the same error as `face_confirm` sitting at SAFE while able to
    switch the webcam on. This asks conversationally, through the machinery that
    already exists for asking, and the answer is his to give.

    Declining is a REAL answer with its own branch. It runs nothing — a branch
    with no tool is a branch that does nothing — so nothing is left half written.
    """
    return Ambiguity(
        subject=subject,
        question=question,
        branches=(
            Branch("go ahead", tool, dict(args),
                   ("yes", "yeah", "yep", "go", "ahead", "do", "please", "sure",
                    "ok", "okay", "fine", "start", "carry", "on") + tuple(yes_words),
                   render=render, speculative=False),
            Branch("leave it", "", {},
                   ("no", "nope", "don't", "dont", "skip", "leave", "later",
                    "not", "forget", "stop") + tuple(no_words),
                   render=lambda a, r: "Of course, sir.", speculative=False),
        ),
    )


def validate(amb: Ambiguity, risk_of) -> bool:
    """Refuse to speculate on anything that acts. `risk_of(tool) -> bool` says
    whether that tool needs confirmation; an unknown tool counts as unsafe."""
    if not amb.branches or len(amb.branches) > MAX_BRANCHES:
        return False
    for b in amb.branches:
        # A branch with no tool does nothing, which is exactly what declining is.
        # It has nothing to be unsafe about, and asking `risk_of("")` would raise
        # and refuse the whole question.
        if not b.tool:
            continue
        try:
            if risk_of(b.tool):
                log.warning("refusing to speculate on %r - it needs confirmation", b.tool)
                return False
        except Exception:
            return False
    return True
