"""JARVIS's own brain: a learned intent router that handles known requests
itself — no LLM — and keeps learning from every interaction.

How it works
- Every known phrasing is embedded (bge-small via fastembed, already bundled).
- A new utterance is embedded and compared (cosine) to all examples; the top
  neighbours vote. A confident, consistent vote = a reflex: run the skill's tool
  directly and speak a template. Otherwise the request goes to the LLM.
- Self-training: when the LLM resolves a request with exactly one known tool,
  the utterance is stored as a new example for that skill. Reflexes grow with use.
- Examples live in SQLite (brain_examples) so learning persists across restarts.

Latency: embedding ~10 ms, kNN ~0 ms, vs. 2-12 s for an LLM round.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import OrderedDict
import time

import numpy as np

from brain.skills import SKILLS, SKILL_BY_NAME, TOOL_TO_SKILL, Skill
from config import config, open_db

log = logging.getLogger("jarvis.brain")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS brain_examples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    text TEXT NOT NULL UNIQUE,
    skill TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'seed',   -- seed | learned | user
    embedding BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS brain_rejections (
    -- "did you mean X?" ... "no". Never offer X for this sentence again.
    text  TEXT NOT NULL,
    skill TEXT NOT NULL,
    ts    REAL NOT NULL,
    PRIMARY KEY (text, skill)
);
CREATE TABLE IF NOT EXISTS brain_commands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    phrase TEXT NOT NULL UNIQUE,     -- what the user says ("lights out")
    steps TEXT NOT NULL,             -- JSON [{"skill":..., "args":{...}}, ...]
    embedding BLOB NOT NULL
);
"""


_CANON = [
    # (pattern, replacement) — applied to seeds AND queries so embeddings encode intent, not objects
    # meta-requests first: they contain other commands inside them
    (r"^(?:from now on[, ]*|ok[, ]*|okay[, ]*)?(?:when(?:ever)? i say|if i say|teach you).*", "when i say PHRASE do ACTION"),
    (r"^(?:no|nope|wrong|not that|that's wrong|that is wrong|that's not)\b.*", "no i meant ACTION"),
    (r"\b(?:tell me|let me know|warn me|alert me|notify me|ping me|keep an eye on)\b.*\b(?:cpu|processor|ram|memory|disk|storage|drive|space|battery)\b.*\b(?:above|over|exceeds|more than|higher than|hits|reaches|below|under|drops|less than|lower than|falls|passes)\b.*", "tell me if METRIC goes above N"),
    # "stop watching my HANDS" is not a system-monitor rule. Folded onto
    # "stop watching METRIC" the word `hands` was erased, and turning the
    # gesture tracker off collided head-on with cancelling a CPU alert. Same
    # shape as camera and hologram: the noun after the verb sometimes names a
    # subsystem rather than an argument.
    (r"\b(?:stop|quit|cancel|forget about)\b(?!.*\b(?:hands?|gestures?)\b).*\b(?:watching|monitoring|alert|alerts|warning|telling me|rule|rules)\b.*", "stop watching METRIC"),
    (r".*\b(?:weather|forecast|temperature outside|going to rain|raining|snowing|umbrella|hot out|cold out|hot is it|cold is it|will it (?:rain|snow)|is it (?:going to )?(?:rain|snow|be hot|be cold))\b.*", "what's the weather in PLACE"),
    (r".*\b(?:tab|tabs|panel|panels|menu|navigation)\b.*", "show the VIEW tab"),
    # "hide the panels" is a UI request; "hide YOURSELF" is a dismissal — without the
    # exclusion this rewrote it to "hide everything" and it hit the UI skill at 1.00.
    # ...and "hide the camera" names a subsystem, not "everything". Folded in, it
    # became the UI's hide-all canon and camera_off could not compete for its own
    # phrasing. Found by test_canon_erasure, not by him, which is the point of it.
    # ...and "hide the hologram" names a subsystem too, for exactly the reason
    # the camera does. Rewritten to "hide everything" it dismissed the whole
    # stage through the UI skill, and hide_hologram — the tool that actually
    # takes a model down — never saw the sentence that was about it.
    (r"^(?:hide|dismiss|clear)\b(?!\s+(?:yourself|himself|jarvis))(?!.*\b(?:camera|webcam|hologram|holo|layers|toolpath)\b)\b.*", "hide everything"),
    (r"^(?:pin|unpin)\b.*", "pin that"),
    # "what does cpu stand for" / "what is a cpu" are questions ABOUT a thing, not requests
    # to measure it — without this they land within 0.05 of the "what's the cpu at" seed
    # and JARVIS answers a definition question with a system-stats report.
    (r"^(?:so\s+)?what(?:'s|s| is| does| do| are)\b.*\b(?:stand for|stands for|short for|mean|means|acronym)\b.*", "explain what SOMETHING means"),
    # "a/an" only, never "the": "what's THE time" / "what's THE date" are live readings.
    (r"^(?:so\s+)?what(?:'s|s| is| are)\s+an?\s+[a-z0-9 -]{2,30}\??$", "explain what SOMETHING means"),
    (r"\b(?:remind me(?!\s+(?:what|who|where|when|which|how)\b)|set a reminder|reminder)\b.*", "remind me at TIME to TASK"),
    # "remember my face" is ENROLLMENT, not a fact to store. Folding it onto the
    # memory canon left it stranded: the memory skill's guard refused it, and the
    # rewrite had already destroyed the word "face", so the fallthrough had
    # nothing above threshold to land on and the whole utterance did NOTHING.
    # Excluded here, it keeps its own words and reaches face_learn on meaning.
    (r"^(?:remember|note|keep in mind)\b(?!.*\b(?:my face|what i look like|my appearance)\b).*", "remember that FACT"),
    (r".*\b[a-z0-9-]+\.(?:com|org|net|io|gov|edu|co|tv|ai|uk|ca)\b.*\b(?:and tell|tell me|read|summar\w*|what does|what's on|what is on|look at|check)\b.*", "read the website SITE and tell me"),
    (r".*\b(?:read|summar\w*|what does|what's on|look at|check)\b.*\b[a-z0-9-]+\.(?:com|org|net|io|gov|edu|co|tv|ai|uk|ca)\b.*", "read the website SITE and tell me"),
    # "go back to the previous version" is not a window. Folded onto "switch to
    # APP" it went looking for something called `previous version` to focus,
    # and the words that said which VERSION he meant were already gone. Same
    # shape as camera and hologram: the noun after the verb is sometimes a
    # subsystem rather than an argument.
    (r"(?!.*\b(?:version|edit|revision)\b)\b(?:switch (?:over )?to|focus on|focus|go back to|jump to|bring me to)\s+(?:the\s+|my\s+)?[a-z0-9 .+#-]{2,40}(?:\s+window|\s+app)?$", "switch to APP"),
    (r"\b(?:open|show|browse|go to|list|look at|pull up|what's (?:in|on))\b.*\b(?:desktop|documents|docs|downloads|pictures|photos)\b(?!\s+(?:of|from)\b).*", "open my FOLDER folder"),
    (r"\b(?:find|look for|locate|where is|where's)\b.*\b(?:file|folder|document|resume|screenshot|invoice|report|notes?|photo|picture)s?\b(?!\s+(?:of|from)\b).*", "find the file called NAME"),
    (r".*\b(?:file|folder|document)s?\s+(?:called|named|with|containing)\b.*", "find the file called NAME"),
    (r"\bsearch (?:my )?(?:desktop|documents|downloads|pictures) for\b.*", "find the file called NAME"),
    # "...in my browser" decides WHERE the pictures appear, so it must survive
    # the rewrite. Folded in, "show me pictures of X in my browser" became the
    # plain image canon and rendered into the HUD panel he had just asked to
    # bypass — the third rewrite today to delete the word carrying the intent.
    # ...and "as a hologram" / "in 3d" decides WHAT KIND of thing appears, so it
    # has to survive too. "show me pictures of a bracket as a hologram" folded
    # onto the plain image canon and rendered flat pictures into the panel — the
    # same erasure as "in my browser", one noun along. His rule, verbatim: images
    # unless he says hologram.
    (r"(?!.*\bin\s+(?:my\s+|the\s+)?(?:browser|brave|chrome|firefox|edge)\b)(?!.*\b(?:hologram|holo|3d|holographic)\b)\b(?:show|find|pull up|get|display|bring up)\s+(?:me\s+)?(?:a\s+|some\s+|an\s+)?(?:picture|pictures|photo|photos|image|images|pic|pics)\s+of\s+.+", "show me pictures of THING"),
    # Something to WATCH is not a web search. Without this exclusion "find me a
    # youtube video of X" folded onto the search canon at cosine 1.000, so the
    # only way to answer it was to search the web, let the model read the
    # results, and recite a URL into the side panel — which is exactly what he
    # got, and exactly what he does not want. The media words survive now, and
    # the request reaches the skill that OPENS it in his browser.
    # ...and "in my browser" is excluded for the same reason: it is the one
    # phrase that decides WHERE the answer appears, and folding it into the
    # search canon deleted it before anything could act on it.
    (r"(?!.*\b(?:youtube|you\s?tube|video|videos|clip|trailer|gameplay|netflix|spotify)\b)(?!.*\bin\s+(?:my\s+|the\s+)?(?:browser|brave|chrome|firefox|edge)\b)\b(?:search(?:\s+the\s+web|\s+online|\s+google)?\s+for|search(?:\s+the\s+web)?|look\s+up|google|find(?:\s+me)?(?:\s+online)?|web\s+search(?:\s+for)?|research)\s+.+", "search the web for THING"),
    (r"\b(?:volume|turn it|turn the volume|set the volume|set volume|make the volume|change the volume|lower the volume|raise the volume|put the volume)\b.*\d+.*", "set the volume to N percent"),
    (r"\b(?:open|go to|pull up|take me to|load|bring up|open up)\b.*\b[a-z0-9-]+\.(?:com|org|net|io|gov|edu|co|tv|ai|uk|ca)\b.*", "open the website SITE"),
    # The launch verbs are ordinary English words, so the exclusions matter: "run along"
    # and "off you go" are dismissals, not a request to open an app called "along".
    # "camera"/"webcam" are excluded for the same reason "music" is: he is naming a
    # SUBSYSTEM, not an executable. Rewritten to "open APP", "open the camera" —
    # his own words for this — matched open_app at cosine 1.000 and tried to launch
    # the Windows Camera app instead of the HUD view.
    # `hologram`/`holo` join `camera` in both launch lists for the same reason:
    # they name a SUBSYSTEM, not an executable. "open the hologram" became
    # "open APP" and went looking for a program called hologram to run.
    (r"\b(?:open|launch|start|run|fire up|bring up|put on)\s+(?:up\s+)?(?!(?:the\s+|my\s+|that\s+|this\s+|some\s+|a\s+|an\s+)?(?:sound|audio|volume|music|tunes|camera|webcam|hologram|holo|pod bay|desktop|documents|docs|downloads|pictures|photos|along|away|off|over|back|ahead|again|yourself|himself|article|articles|story|link|links|source|page)\b)(?:the\s+|my\s+)?[a-z0-9 .+#-]{2,40}", "open APP"),
    (r"\b(?:close|quit|exit|kill)\s+(?!(?:the\s+|my\s+)?(?:sound|audio|volume|music|camera|webcam|hologram|holo|speakers|pc|computer)\b)(?:the\s+|my\s+)?[a-z0-9 .+#-]{2,40}", "close APP"),
    # "3d" must survive the digit rule below. It is the word that distinguishes
    # "create a 3D image of that" — which builds a mesh in the background — from
    # any other kind of image, and "Nd" is not a word anything can route on.
    # Normalised first so "3-D" and "3 d" reach the same token.
    (r"\b3\s*-?\s*d\b", "3d"),
    # ...and dictation writes it as a WORD at least as often. "Create me a three
    # D image of Spider-Man's spider emblem" is what came out of the recogniser;
    # without this it is a different sentence from "create me a 3d image" as far
    # as the index is concerned.
    (r"\bthree\s*-?\s*d\b", "3d"),
    # "IMAGE NUMBER FOUR" IS "IMAGE NUMBER 4". Digits collapse to N below and
    # number words did not, so the same request scored 1.00 spoken with a digit
    # and 0.81 spoken aloud — and 0.81 is close enough to the threshold to be a
    # coin toss. Only after "number", so ordinary counting words elsewhere
    # ("another one", "the second one") are untouched.
    (r"\bnumber\s+(?:one|two|three|four|five|six|seven|eight|nine|ten|"
     r"eleven|twelve)\b", "number N"),
    (r"\d+(?!\s*-?\s*d\b)", "N"),
]


def _light(t: str) -> str:
    """Lowercase, strip the wake phrase and politeness — keeps all content words."""
    import re
    t = t.lower().strip()
    t = re.sub(r"^(?:hey|hi|ok|okay)?[,\s]*jarvis[,.!?\s]*", "", t)
    t = re.sub(r"\b(?:please|for me|can you|could you|would you|will you)\b", " ", t)
    # Speech recognition writes it as two words. His actual sentence was "find me
    # a You Tube video of someone playing Iron Man PS3", and every pattern
    # matching "youtube" missed it — the request was right, the transcript was
    # right, and the routing failed on a space.
    t = re.sub(r"\byou\s+tube\b", "youtube", t)
    t = re.sub(r"[^\w\s%':.-]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


# A ticker is an object like any other, and the embedder cannot see that: NVDA,
# AAPL and PLTR are three unrelated rare tokens, so seeding one teaches it
# nothing about the next. Typed from his phone that is how he writes them, and
# "what's AAPL trading at" missed the brain entirely and went to the model,
# which answered it off a scraped web page instead of the live quote.
#
# CAPITALS are the whole signal, so this runs before _light lowercases: without
# them "how is mom doing" is the same shape as "how is AMD doing".
_TICKER_CTX = re.compile(
    r"\b(?:price|quote|stock|stocks|shares?|trading|ticker|worth)\b"
    r"|\b(?:what'?s|what is|how'?s|how is)\b.{0,20}?\b(?:at|doing)\b", re.I)
# A ticker is not followed by a model number and does not take an article:
# "the price of an RTX 5090" is a graphics card, and rewriting it to "an apple
# 5090" sent a research question to the stock tool. Both guards are cheap and
# neither costs a real ticker anything — nobody says "an AAPL".
_TICKER_TOK = re.compile(r"(?<![A-Za-z0-9])([A-Z]{2,5})(?![A-Za-z0-9])(?!\s+\d)")
_ARTICLE_BEFORE = re.compile(r"\b(?:a|an|the)\s+$", re.I)
# ...and an explicit request to go and LOOK is never a request for a quote
_SEARCH_VERB = re.compile(
    r"\b(?:look up|search|research|google|find out|browse|read about)\b", re.I)
# capitalised things he says that are emphatically not companies
_NOT_TICKERS = {"OK", "PC", "AI", "TV", "US", "UK", "USA", "CPU", "RAM", "GPU",
                "SSD", "USB", "PDF", "CEO", "CFO", "AM", "PM", "EV", "IT", "ID",
                "URL", "API", "FBI", "NASA", "HTTP", "WIFI", "GPS", "SMS", "DVD"}


def _ticker_to_company(t: str) -> str:
    """"what's AAPL trading at" -> "what's apple trading at", so it matches the
    seeds by SHAPE. The real ticker is untouched in the text the slots read."""
    if not _TICKER_CTX.search(t) or _SEARCH_VERB.search(t):
        return t

    def one(m: "re.Match") -> str:
        tok = m.group(1)
        if tok in _NOT_TICKERS or _ARTICLE_BEFORE.search(t[:m.start()]):
            return tok
        return "apple"
    return _TICKER_TOK.sub(one, t, count=1)


def _norm(t: str) -> str:
    """Canonical intent form used for embeddings (objects -> placeholders)."""
    import re
    t = _light(_ticker_to_company(t))
    for pat, rep in _CANON:
        t2 = re.sub(pat, rep, t, count=1)
        if t2 != t:
            t = t2
            break
    return re.sub(r"\s+", " ", t).strip()


# ---------------------------------------------------------------- context
# WHAT IS ON SCREEN DECIDES WHAT THE WORDS MEAN. These are the skills whose
# sentences are ambiguous without it - see this module's history for the
# measured scores that put each one here.
_STAGE_SKILLS = frozenset({
    "holo_move", "holo_edit", "holo_check", "holo_hide", "holo_revert",
    "holo_again", "holo_show",
})
_PROJECT_SKILLS = frozenset({"project_note", "project_recall"})
_RENDER_SKILLS = frozenset({"render_stop", "render_how"})

# Sized from the real gaps: the widest one a bonus has to close is 0.122
# ("make it bigger": ui@1.00 against holo_move@0.88). Big enough to win a
# genuine tie, small enough that it cannot invent a match out of nothing.
CONTEXT_BONUS = 0.15
CONTEXT_PENALTY = 0.20


# A SENTENCE THAT POINTS BACKWARDS. These carry no subject of their own: they
# only mean anything next to what came before. "What about now" embedded alone
# is nearest whatever seeds mention "now", which is why it reached `time`
# two seconds after a question about fingers.
_FOLLOW_UP = re.compile(
    r"^\s*(?:and |so |ok(?:ay)?,? |well |but )?"
    r"(?:what about|how about|and now|what if|and you|"
    r"(?:do|try) (?:it|that) again|again|once more|same again|"
    r"the (?:second|third|fourth|first|last|other|next) one|"
    r"that one|this one|those|the rest)\b", re.I)

# How long a subject stays live. Long enough for him to look at the screen and
# ask the obvious next thing; short enough that it can never reach across a
# break in the conversation.
FOLLOW_UP_WINDOW_S = 90.0

# Words that add nothing after the reference. "What about NOW" is still a bare
# reference; "what about the WEATHER" is not.
_FOLLOW_TAIL = frozenset((
    "now", "then", "please", "sir", "too", "as", "well", "instead",
    "one", "it", "that", "this", "though", "again", "here", "there",
))

# Purely referential: nothing but the reference, so the last subject is the
# ONLY subject on offer. Sized against the measured noise floor - the top four
# skills for "what about now" sit within 0.06 of each other and 0.23 above the
# right answer, so this has to clear a gap that means nothing in the first place.
FOLLOW_UP_BONUS_BARE = 0.35
# Partly referential: it opens backwards but carries its own subject. A nudge,
# and the sentence still wins or loses on what it actually says.
FOLLOW_UP_BONUS = 0.12


def _follow_up_pull(text: str) -> float:
    """How strongly this sentence points at whatever came before."""
    m = _FOLLOW_UP.match(text or "")
    if not m:
        return 0.0
    rest = re.sub(r"[^a-z0-9\s]", " ", (text or "")[m.end():].lower())
    left = [w for w in rest.split() if w not in _FOLLOW_TAIL]
    return FOLLOW_UP_BONUS_BARE if not left else FOLLOW_UP_BONUS


# A NAMED FEATURE IS AN EDIT. Enough to win a near tie against the view, and
# no more - "eyes" appearing in a sentence should tip a close call, not drag
# holo_edit in from nowhere.
EDIT_TARGET_BONUS = 0.10


def _lexical_delta(skill: str, text: str) -> float:
    """Word-level evidence, as opposed to the state of the screen."""
    if skill != "holo_edit":
        return 0.0
    try:
        from brain.skills import _EDIT_MEASURE, _EDIT_TARGET
    except Exception:
        return 0.0
    return EDIT_TARGET_BONUS if (_EDIT_TARGET.search(text)
                                 or _EDIT_MEASURE.search(text)) else 0.0


def _skill_context_delta(skill: str, ctx: dict) -> float:
    """How much the current state argues for or against this skill."""
    d = 0.0
    if skill in _STAGE_SKILLS:
        d += CONTEXT_BONUS if ctx.get("stage") else -CONTEXT_PENALTY
    if skill in _RENDER_SKILLS:
        d += CONTEXT_BONUS if ctx.get("render") else -CONTEXT_PENALTY
    if skill in _PROJECT_SKILLS:
        d += CONTEXT_BONUS if ctx.get("project") else -CONTEXT_PENALTY
    return d


class Brain:
    def __init__(self) -> None:
        self.db = open_db()
        self.db.executescript(_SCHEMA)
        self.db.commit()
        self._embedder = None
        self._vec_cache: "OrderedDict[str, np.ndarray]" = OrderedDict()
        self._texts: list[str] = []
        self._skills: list[str] = []
        self._matrix: np.ndarray | None = None
        self._lock = asyncio.Lock()
        self.stats = {"reflex": 0, "llm": 0, "learned": 0}
        self._cmd_phrases: list[str] = []
        self._cmd_steps: list[list[dict]] = []
        self._cmd_matrix: np.ndarray | None = None
        self.last_match: dict | None = None   # what the last decide() matched (for corrections)
        # The near-miss from the last decide(): the right idea, scored
        # too low to act on alone, kept so the turn can ASK.
        self.unsure: dict | None = None
        # (sentence, skill) pairs he has already said no to.
        self._rejected: set = set()
        # Context deltas are the same for every example until the state
        # changes, so they are computed once per state rather than per turn.
        self._ctx_key: tuple | None = None
        self._ctx_vec = None

    # ---------- embeddings ----------

    def _embed(self, texts: list[str]) -> np.ndarray:
        """Embed a batch. Single texts are cached: one spoken turn asks for the same
        vector up to three times (custom-command match, classify, general_level), and
        the embedding is essentially the whole cost of a brain decision."""
        if len(texts) == 1:
            hit = self._vec_cache.get(texts[0])
            if hit is not None:
                self._vec_cache.move_to_end(texts[0])
                return hit
        if self._embedder is None:
            from fastembed import TextEmbedding
            # One short phrase is far too little work to be worth fanning across cores:
            # measured 39 ms single-threaded vs 57 ms default vs 80 ms on 8 threads.
            # It also leaves the CPU to llama-server, which needs it far more.
            threads = int(config.get("brain", "embed_threads", default=1))
            self._embedder = TextEmbedding("BAAI/bge-small-en-v1.5",
                                           threads=threads if threads > 0 else None)
        vecs = np.array(list(self._embedder.embed(texts)), dtype=np.float32)
        vecs /= (np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-9)
        if len(texts) == 1:
            self._vec_cache[texts[0]] = vecs
            self._vec_cache.move_to_end(texts[0])
            while len(self._vec_cache) > 256:
                self._vec_cache.popitem(last=False)
        return vecs

    # ---------- lifecycle ----------

    async def load(self) -> None:
        """Seed on first run, then load all examples into memory."""
        async with self._lock:
            # canonical (text -> skill); first skill listed wins a collision
            canon: dict[str, str] = {}
            for sk in SKILLS:
                for u in sk.seeds:
                    canon.setdefault(_norm(u), sk.name)
            # drop seeds that are stale or mislabeled under the current canonical map
            dropped = 0
            for text, skill in self.db.execute("SELECT text, skill FROM brain_examples WHERE source='seed'").fetchall():
                if canon.get(text) != skill:
                    self.db.execute("DELETE FROM brain_examples WHERE text=?", (text,))
                    dropped += 1
            # seeds are ground truth: a learned row that contradicts a seed is a mislabel
            for text, skill in self.db.execute("SELECT text, skill FROM brain_examples WHERE source!='seed'").fetchall():
                if text in canon and canon[text] != skill:
                    self.db.execute("DELETE FROM brain_examples WHERE text=?", (text,))
                    dropped += 1
                    log.info("brain: dropped mislabeled learned example %r (%s)", text, skill)
                elif not self._executable(text, skill):
                    self.db.execute("DELETE FROM brain_examples WHERE text=?", (text,))
                    dropped += 1
                    log.info("brain: dropped unusable learned example %r (%s)", text, skill)
            # COMMIT THE DELETIONS HERE. This single missing line was the root of
            # the 2026-08-31 outage. The commit below sits inside `if missing:`,
            # so on any start where rows were dropped and nothing needed seeding,
            # this connection kept an open write transaction — and with it
            # SQLite's one write lock — for the entire life of the process.
            # Every other writer then got "database is locked" forever: turns
            # stopped being recorded, and the scheduler could not move a due
            # reminder on, so it re-announced Nicholas's 9 p.m. retainer reminder
            # every ten seconds and sent him ~2,600 messages overnight.
            #
            # It also has to happen BEFORE the `await` below: holding a write
            # transaction across a thread hop hands the lock to a coroutine that
            # may never come back.
            if dropped:
                self.db.commit()
                log.info("brain: dropped %d stale example(s)", dropped)
            have = {r[0] for r in self.db.execute("SELECT text FROM brain_examples")}
            missing = [(sk, t) for t, sk in canon.items() if t not in have]
            if missing:
                vecs = await asyncio.to_thread(self._embed, [t for _, t in missing])
                self.db.executemany(
                    "INSERT OR IGNORE INTO brain_examples (ts, text, skill, source, embedding) VALUES (?,?,?,?,?)",
                    [(time.time(), t, sk, "seed", v.tobytes()) for (sk, t), v in zip(missing, vecs)])
                self.db.commit()
            rows = self.db.execute("SELECT text, skill, embedding FROM brain_examples").fetchall()
            self._texts = [r[0] for r in rows]
            self._skills = [r[1] for r in rows]
            # .copy(): np.frombuffer returns a READ-ONLY view over the bytes, and both of
            # these matrices are written in place later (re-teaching a phrase assigns a
            # row). Without it, teaching a command that already existed died with
            # "assignment destination is read-only" and took the whole turn with it.
            self._matrix = (np.frombuffer(b"".join(r[2] for r in rows), dtype=np.float32)
                            .reshape(len(rows), -1).copy()) if rows else None
            crows = self.db.execute("SELECT phrase, steps, embedding FROM brain_commands").fetchall()
            self._cmd_phrases = [r[0] for r in crows]
            self._cmd_steps = [json.loads(r[1]) for r in crows]
            try:
                self._rejected = {
                    (r[0], r[1]) for r in
                    self.db.execute("SELECT text, skill FROM brain_rejections")}
            except Exception:
                log.debug("could not read the rejections", exc_info=True)
            self._cmd_matrix = (np.frombuffer(b"".join(r[2] for r in crows), dtype=np.float32)
                                .reshape(len(crows), -1).copy()) if crows else None
            log.info("brain loaded: %d examples across %d skills, %d custom commands",
                     len(rows), len(SKILLS), len(crows))

    @property
    def example_count(self) -> int:
        return len(self._texts)

    def status(self) -> dict:
        per: dict[str, int] = {}
        for sk in self._skills:
            per[sk] = per.get(sk, 0) + 1
        recent = self.db.execute(
            "SELECT ts, text, skill, source FROM brain_examples WHERE source != 'seed' "
            "ORDER BY ts DESC LIMIT 25").fetchall()
        return {
            "examples": self.example_count,
            "skills": [{"name": s.name, "tool": s.tool, "examples": per.get(s.name, 0),
                        "llm_after": s.llm_after} for s in SKILLS if s.name != "general"],
            "stats": dict(self.stats),
            "threshold": float(config.get("brain", "threshold", default=0.82)),
            "recent": [{"ts": r[0], "text": r[1], "skill": r[2], "source": r[3]} for r in recent],
            "commands": self.commands(),
        }

    # ---------- classification ----------

    def _context_vector(self, ctx: dict):
        """Per-example score deltas for the current state, cached by state."""
        key = tuple(sorted(ctx.items()))
        if (self._ctx_key == key and self._ctx_vec is not None
                and len(self._ctx_vec) == len(self._skills)):
            return self._ctx_vec
        per_skill = {sk: _skill_context_delta(sk, ctx) for sk in set(self._skills)}
        vec = np.array([per_skill[sk] for sk in self._skills], dtype=np.float32)
        self._ctx_key, self._ctx_vec = key, vec
        return vec

    async def classify(self, text: str, k: int = 5,
                       exclude: set | None = None,
                       context: dict | None = None) -> tuple[str | None, float]:
        """Return (skill, confidence).

        Top-match wins; confidence is the top similarity, penalized when the best
        example of a *different* skill is nearly as close (ambiguity).

        `context` is what is on screen — a model on the stage, a project open, a
        render running. It NUDGES rather than filters: nothing is ever removed
        for want of context, so he can still ask about a project with none open
        and be told so."""
        if self._matrix is None or not text.strip():
            return None, 0.0
        q = (await asyncio.to_thread(self._embed, [_norm(text)]))[0]
        sims = self._matrix @ q
        if context:
            sims = sims + self._context_vector(context)
        # Cheap enough to do per turn: one regex over one sentence, and only
        # the examples of a single skill move.
        lex = _lexical_delta("holo_edit", text)
        if lex:
            sims = sims + np.array(
                [lex if sk == "holo_edit" else 0.0 for sk in self._skills],
                dtype=np.float32)
        # STILL TALKING ABOUT THE SAME THING. "What about now" means nothing on
        # its own; next to a question about fingers it plainly means fingers.
        last = (context or {}).get("last_skill")
        pull = _follow_up_pull(text) if last else 0.0
        if pull:
            sims = sims + np.array(
                [pull if sk == last else 0.0 for sk in self._skills],
                dtype=np.float32)
        order = np.argsort(-sims)
        if exclude:
            # decide() asks again without a skill whose slot extractor refused the
            # phrasing, so a guarded top match hands over to the runner-up instead
            # of dropping the whole turn on the LLM ("that's it thanks" is a
            # DISMISSAL that merely looks like a thank-you).
            order = [i for i in order if self._skills[i] not in exclude]
            if not order:
                return None, 0.0
        best = self._skills[order[0]]
        top = float(sims[order[0]])
        rival = 0.0
        for i in order[1:k * 4]:
            if self._skills[i] != best:
                rival = float(sims[i])
                break
        margin = top - rival
        confidence = top if margin >= 0.06 else top - (0.06 - margin) * 3.0
        # THE RUNNER-UP IS THE QUESTION. When two readings are this close the
        # honest answer is "which of these did you mean", and that cannot be
        # asked unless the second one is kept. It used to be computed, used to
        # shrink the confidence, and thrown away — so a near-tie became silence.
        rival_skill = None
        for i in order[1:k * 4]:
            if self._skills[i] != best:
                rival_skill = self._skills[i]
                break
        src = self.db.execute("SELECT source FROM brain_examples WHERE text=?",
                              (self._texts[order[0]],)).fetchone()
        self.last_match = {"text": self._texts[order[0]], "skill": best,
                           "source": src[0] if src else "seed", "query": text,
                           "confidence": round(max(0.0, confidence), 3),
                           "rival": rival_skill, "margin": round(margin, 3),
                           "top": round(top, 3)}
        self._last = (best, round(max(0.0, confidence), 3))
        if best == "general":
            return None, round(max(0.0, confidence), 3)
        return best, round(max(0.0, confidence), 3)

    async def general_level(self, text: str) -> str | None:
        """How sure the brain is that this is a knowledge/creative question the LLM
        should answer from its own head: "sure" (block tools on the first round),
        "likely" (hint the model not to search), or None."""
        threshold = float(config.get("brain", "threshold", default=0.82))
        soft = float(config.get("brain", "general_hint_threshold", default=0.7))
        await self.classify(text)
        best, conf = getattr(self, "_last", (None, 0.0))
        if best != "general":
            return None
        return "sure" if conf >= threshold else ("likely" if conf >= soft else None)

    # ---------- learning ----------

    @staticmethod
    def _executable(text: str, skill: str) -> bool:
        """A phrasing is only worth learning if the skill could act on it by itself."""
        sk = SKILL_BY_NAME.get(skill)
        if sk is None or skill == "general":
            return False
        try:
            return sk.slots(_light(text)) is not None
        except Exception:
            return False

    async def learn(self, text: str, skill: str, source: str = "learned") -> bool:
        t = _norm(text)
        if not t or skill not in SKILL_BY_NAME or len(t.split()) > 14:
            return False
        if source != "user":
            if not self._executable(text, skill):
                return False  # the LLM used a tool the skill couldn't run from this phrasing
            best, conf = await self.classify(text)
            if best is None and getattr(self, "_last", (None, 0))[0] == "general" and conf >= 0.7:
                return False  # brain is fairly sure this was a plain question; the tool use was a whim
        async with self._lock:
            if t in self._texts:
                return False
            v = (await asyncio.to_thread(self._embed, [t]))[0]
            self.db.execute(
                "INSERT OR IGNORE INTO brain_examples (ts, text, skill, source, embedding) VALUES (?,?,?,?,?)",
                (time.time(), t, skill, source, v.tobytes()))
            self.db.commit()
            self._texts.append(t)
            self._skills.append(skill)
            self._matrix = v[None, :] if self._matrix is None else np.vstack([self._matrix, v])
            self.stats["learned"] += 1
            log.info("brain learned: %r -> %s", t, skill)
            return True

    def reject(self, text: str, skill: str) -> None:
        """He answered no to "did you mean <skill>?" — do not offer it again.

        Persisted, because being asked the same rejected question tomorrow is
        the same annoyance as being asked it twice in a row. Keyed on sentence
        AND skill: saying no to one reading leaves the others available.
        """
        t = _norm(text)
        if not t or not skill:
            return
        try:
            self.db.execute(
                "INSERT OR IGNORE INTO brain_rejections (text, skill, ts) "
                "VALUES (?,?,?)", (t, skill, time.time()))
            self.db.commit()
            self._rejected.add((t, skill))
            log.info("brain will not offer %s for %r again", skill, t)
        except Exception:
            # A rejection that cannot be stored must never cost him the turn.
            log.debug("could not store the rejection", exc_info=True)

    def was_rejected(self, text: str, skill: str) -> bool:
        return (_norm(text), skill) in self._rejected

    def learned_from_tool(self, tool_name: str) -> str | None:
        return TOOL_TO_SKILL.get(tool_name)

    # ---------- custom commands (taught by voice) ----------

    async def match_command(self, text: str) -> list[dict] | None:
        """A phrase the user taught ("lights out") -> its steps. Near-exact match only."""
        if self._cmd_matrix is None:
            return None
        q = (await asyncio.to_thread(self._embed, [_light(text)]))[0]
        sims = self._cmd_matrix @ q
        i = int(np.argmax(sims))
        if float(sims[i]) >= 0.92:
            self.last_match = {"text": self._cmd_phrases[i], "skill": "command",
                               "source": "user", "query": text, "confidence": float(sims[i])}
            return self._cmd_steps[i]
        return None

    async def teach_command(self, phrase: str, steps: list[dict]) -> None:
        phrase = _light(phrase)
        async with self._lock:
            v = (await asyncio.to_thread(self._embed, [phrase]))[0]
            self.db.execute("INSERT OR REPLACE INTO brain_commands (ts, phrase, steps, embedding) VALUES (?,?,?,?)",
                            (time.time(), phrase, json.dumps(steps), v.tobytes()))
            self.db.commit()
            if phrase in self._cmd_phrases:
                i = self._cmd_phrases.index(phrase)
                self._cmd_steps[i] = steps
                self._cmd_matrix[i] = v
            else:
                self._cmd_phrases.append(phrase)
                self._cmd_steps.append(steps)
                self._cmd_matrix = v[None, :] if self._cmd_matrix is None else np.vstack([self._cmd_matrix, v])
        log.info("brain taught command %r -> %s", phrase, steps)

    async def forget_command(self, phrase: str) -> bool:
        phrase = _light(phrase)
        async with self._lock:
            if phrase not in self._cmd_phrases:
                return False
            i = self._cmd_phrases.index(phrase)
            self.db.execute("DELETE FROM brain_commands WHERE phrase=?", (phrase,))
            self.db.commit()
            self._cmd_phrases.pop(i); self._cmd_steps.pop(i)
            self._cmd_matrix = np.delete(self._cmd_matrix, i, axis=0) if len(self._cmd_phrases) else None
        return True

    async def unlearn(self, match: dict | None) -> str | None:
        """Correction: the last reflex was wrong. Drop the learned example (or taught
        command) that caused it. Seeds are never dropped; they return None."""
        if not match:
            return None
        if match.get("skill") == "command":
            await self.forget_command(match["text"])
            return "command"
        if match.get("source") in ("learned", "user"):
            async with self._lock:
                t = match["text"]
                if t in self._texts:
                    i = self._texts.index(t)
                    self.db.execute("DELETE FROM brain_examples WHERE text=?", (t,))
                    self.db.commit()
                    self._texts.pop(i); self._skills.pop(i)
                    self._matrix = np.delete(self._matrix, i, axis=0) if self._texts else None
                    log.info("brain unlearned %r (%s)", t, match["skill"])
                    return match["skill"]
        return None

    def commands(self) -> list[dict]:
        return [{"phrase": p, "steps": st} for p, st in zip(self._cmd_phrases, self._cmd_steps)]

    # ---------- decision ----------

    async def decide(self, text: str,
                     context: dict | None = None) -> tuple[Skill, dict, float] | None:
        """If confident and the slots extract cleanly, return (skill, args, confidence).

        A skill whose extractor REFUSES the phrasing steps aside and the next-best
        skill gets a turn (bounded, so a chain of refusals still ends at the LLM).
        Guards exist to say "this is not mine" — before this, saying so threw the
        whole utterance to the model even when the right skill was ranked second.

        `context` is what is on screen. It reaches classify, which is where the
        genuinely ambiguous sentences get settled: "make it bigger" is the
        interface with an empty stage and the model with something on it."""
        threshold = float(config.get("brain", "threshold", default=0.82))
        refused: set = set()
        light = _light(text)
        self.unsure = None
        for _ in range(3):
            name, conf = await self.classify(text, exclude=refused or None,
                                             context=context)
            if not name or conf < threshold:
                # NEARLY KNEW IS NOT THE SAME AS NO IDEA, and returning None for
                # both is what makes him look stupid: "make me a duck" ranked
                # holo_make top and was binned for scoring 0.68. Keep the
                # near-misses so the turn above can ask instead of guessing.
                m = self.last_match or {}
                top = float(m.get("top") or 0.0)
                if name and top >= float(config.get(
                        "brain", "ask_threshold", default=0.66)):
                    sk = SKILL_BY_NAME.get(name)
                    slots = sk.slots(light) if sk else None
                    if sk is not None and slots is not None:
                        self.unsure = {
                            "skill": name, "rival": m.get("rival"),
                            "confidence": conf, "top": top,
                            "margin": m.get("margin"),
                            "args": {**sk.fixed_args, **slots},
                        }
                return None
            skill = SKILL_BY_NAME[name]
            slots = skill.slots(light)
            if slots is not None:
                return skill, {**skill.fixed_args, **slots}, conf
            refused.add(name)
        return None


brain = Brain()
