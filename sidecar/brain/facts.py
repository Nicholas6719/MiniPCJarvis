"""The fact store — realm 1 of the brain roadmap (docs/BRAIN_ROADMAP.md).

Timeless, web-verified facts answered in ~0.3 s without waking the LLM.
Three hard rules, agreed with the user 2026-08-26:
  1. Only facts that can NEVER change are stored ("height of the Eiffel Tower").
     Changeable facts ("newest Spider-Man movie") are realm 2: live web every
     time, never served from here, never from the LLM's memory.
  2. A fact enters only from a SOURCED web answer (research/search with URLs),
     never from the LLM's own memory, and only after the temp-0 timeless check.
  3. When unsure, don't store; when a lookup is doubtful, don't serve. A fast
     wrong answer is worse than a slow right one.

The nightly audit (facts due_for_audit / mark_verified / demote) re-checks
stored facts against their original sources while JARVIS sleeps.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time

import numpy as np

from config import DB_PATH, config

log = logging.getLogger("jarvis.facts")

# ---- realm 2 triggers: these force the live web and BLOCK store and serve ----
# Conservative on purpose. "who directed X" is timeless; "who is the CEO" is not.
REALM2 = re.compile(
    r"\b(?:latest|newest|most recent|current(?:ly)?|today|tonight|yesterday|tomorrow|"
    r"this (?:week|month|year|season)|right now|so far|as of|"
    r"price|cost|worth|in stock|available|release date|coming out|next|upcoming|"
    r"weather|forecast|temperature outside|score|standings|won the|playing|"
    r"news|headlines|stock|market|version of|update[sd]?\b|"
    r"who is the (?:president|ceo|owner|coach|manager|champion|richest|leader)|"
    r"how (?:old|many (?:subscribers|followers|users|employees))|"
    r"best|top \d+|tallest building|richest|record for)\b", re.I)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    sources TEXT NOT NULL,        -- json [{url, title}]
    origin TEXT NOT NULL,         -- research | search
    created_ts REAL NOT NULL,
    verified_ts REAL NOT NULL,
    hits INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',   -- active | demoted
    embedding BLOB NOT NULL
);
"""

# The classifier prompt is deliberately blunt; measured at temp 0 it answers
# a single token reliably. Anything that isn't a clean YES is a NO.
_TIMELESS_PROMPT = (
    "You classify facts. Question: {q}\nAnswer given: {a}\n\n"
    "Will this answer still be true and unchanged twenty years from now, with no "
    "possibility of revision (physical constants, completed history, definitions, "
    "math, dimensions of finished structures)? Anything involving rankings, "
    "records, living people's roles, counts that grow, or ongoing series is NO. "
    "Reply with exactly one word: YES or NO.")


class FactStore:
    def __init__(self, db_path: str | None = None) -> None:
        import sqlite3
        self.db = sqlite3.connect(db_path or DB_PATH, check_same_thread=False)
        self.db.executescript(_SCHEMA)
        cols = [r[1] for r in self.db.execute("PRAGMA table_info(facts)")]
        if "strikes" not in cols:   # audit: two UNCLEAR verdicts demote
            self.db.execute("ALTER TABLE facts ADD COLUMN strikes INTEGER NOT NULL DEFAULT 0")
        self.db.commit()
        self.last_served: dict | None = None   # for "how do you know that"
        self.stats = {"served": 0, "stored": 0, "rejected": 0}
        self._evidence: list[dict] = []        # this turn's web sources

    # ---- per-turn evidence: the web tools record what they fetched, so the
    # turn's answer can be traced to real sources before it may become a fact
    def record_evidence(self, query: str, sources: list[dict], origin: str) -> None:
        self._evidence.append({"query": query, "sources": sources, "origin": origin})

    def reset_evidence(self) -> None:
        self._evidence.clear()

    def take_evidence(self) -> list[dict]:
        ev, self._evidence = self._evidence, []
        return ev

    # ------------------------------------------------------------------ serve
    async def lookup(self, text: str) -> dict | None:
        """Serve a stored fact for this utterance, or None. Stricter threshold
        than routing (0.90 vs 0.82): a wrong cached answer is worse than a
        misroute, so doubt means silence."""
        if not config.get("facts", "enabled", default=True):
            return None
        if REALM2.search(text):
            return None                     # changeable realm: live web, always
        rows = self.db.execute(
            "SELECT id, question, answer, sources, verified_ts, embedding FROM facts "
            "WHERE status='active'").fetchall()
        if not rows:
            return None
        from memory.store import memory
        qv = (await memory.embed_texts([text]))[0]
        qv = qv / (np.linalg.norm(qv) + 1e-9)
        best, best_score = None, 0.0
        for r in rows:
            v = np.frombuffer(r[5], dtype=np.float32).copy()
            v /= (np.linalg.norm(v) + 1e-9)
            s = float(np.dot(qv, v))
            if s > best_score:
                best, best_score = r, s
        thr = float(config.get("facts", "serve_threshold", default=0.90))
        if best is None or best_score < thr:
            return None
        self.db.execute("UPDATE facts SET hits = hits + 1 WHERE id=?", (best[0],))
        self.db.commit()
        fact = {"id": best[0], "question": best[1], "answer": best[2],
                "sources": json.loads(best[3]), "verified_ts": best[4],
                "score": round(best_score, 3)}
        self.last_served = fact
        self.stats["served"] += 1
        return fact

    # ------------------------------------------------------------------ store
    async def consider(self, question: str, answer: str,
                       sources: list[dict], origin: str,
                       classify=None) -> bool:
        """Background candidate intake. Both gates must pass: realm-2 triggers
        absent, and the temp-0 timeless check says YES. `classify` is injectable
        for offline tests."""
        from tools.query_clean import clean_search_query
        # store the question as keywords ("look up how tall X is" -> "how tall X is")
        # so future paraphrases land closer in embedding space
        q, a = clean_search_query(question.strip()), answer.strip()
        if not q or not a or not sources:
            self.stats["rejected"] += 1
            return False
        if REALM2.search(q) or REALM2.search(a):
            self.stats["rejected"] += 1
            return False
        if len(a) > 400:
            self.stats["rejected"] += 1      # a fact is a sentence, not an essay
            return False
        verdict = await (classify or self._classify_timeless)(q, a)
        if not verdict:
            self.stats["rejected"] += 1
            return False
        from memory.store import memory
        vec = (await memory.embed_texts([q]))[0]
        # dedupe: an existing near-identical question keeps the OLD verified fact
        if self._similar_exists(vec):
            return False
        self.db.execute(
            "INSERT INTO facts (question, answer, sources, origin, created_ts, "
            "verified_ts, embedding) VALUES (?,?,?,?,?,?,?)",
            (q, a, json.dumps(sources[:4]), origin, time.time(), time.time(),
             vec.astype(np.float32).tobytes()))
        self.db.commit()
        self.stats["stored"] += 1
        log.info("fact stored: %r", q[:80])
        return True

    def _similar_exists(self, qv: np.ndarray, thr: float = 0.92) -> bool:
        qn = qv / (np.linalg.norm(qv) + 1e-9)
        for (blob,) in self.db.execute("SELECT embedding FROM facts WHERE status='active'"):
            v = np.frombuffer(blob, dtype=np.float32).copy()
            v /= (np.linalg.norm(v) + 1e-9)
            if float(np.dot(qn, v)) >= thr:
                return True
        return False

    async def _classify_timeless(self, q: str, a: str) -> bool:
        from llm.provider import local_llm
        out = ""
        try:
            # gpt-oss reasons before it answers: give it room, or the YES/NO
            # gets truncated away and everything reads as NO
            async for ch in local_llm.stream(
                    [{"role": "user", "content": _TIMELESS_PROMPT.format(q=q, a=a)}],
                    max_tokens=600, sampling={"temperature": 0.0}):
                out += ch.text
                if ch.done:
                    break
        except Exception:
            log.exception("timeless classify failed — not storing")
            return False
        # the model may reason before answering; judge only the final word
        words = re.findall(r"\b(YES|NO)\b", out.upper())
        verdict = bool(words) and words[-1] == "YES"
        log.info("timeless classify %r -> %s (%r)", q[:60], verdict, out[-80:])
        return verdict

    # ------------------------------------------------------------------ audit
    def due_for_audit(self, limit: int = 40) -> list[dict]:
        rows = self.db.execute(
            "SELECT id, question, answer, sources, verified_ts, strikes FROM facts "
            "WHERE status='active' ORDER BY verified_ts ASC LIMIT ?", (limit,)).fetchall()
        return [{"id": r[0], "question": r[1], "answer": r[2],
                 "sources": json.loads(r[3]), "verified_ts": r[4], "strikes": r[5]} for r in rows]

    def strike(self, fact_id: int) -> int:
        """An UNCLEAR audit verdict. Two strikes -> demoted (default distrust)."""
        self.db.execute("UPDATE facts SET strikes = strikes + 1 WHERE id=?", (fact_id,))
        self.db.commit()
        n = self.db.execute("SELECT strikes FROM facts WHERE id=?", (fact_id,)).fetchone()[0]
        if n >= 2:
            self.demote(fact_id, "two unclear audits")
        return n

    def mark_verified(self, fact_id: int) -> None:
        self.db.execute("UPDATE facts SET verified_ts=? WHERE id=?", (time.time(), fact_id))
        self.db.commit()

    def demote(self, fact_id: int, reason: str = "") -> None:
        self.db.execute("UPDATE facts SET status='demoted' WHERE id=?", (fact_id,))
        self.db.commit()
        log.info("fact %s demoted: %s", fact_id, reason or "audit")

    def delete(self, fact_id: int) -> None:
        self.db.execute("DELETE FROM facts WHERE id=?", (fact_id,))
        self.db.commit()

    def list_all(self, limit: int = 200) -> list[dict]:
        rows = self.db.execute(
            "SELECT id, question, answer, sources, origin, created_ts, verified_ts, "
            "hits, status FROM facts ORDER BY created_ts DESC LIMIT ?", (limit,)).fetchall()
        return [{"id": r[0], "question": r[1], "answer": r[2], "sources": json.loads(r[3]),
                 "origin": r[4], "created_ts": r[5], "verified_ts": r[6],
                 "hits": r[7], "status": r[8]} for r in rows]


facts = FactStore()


def record_evidence(query: str, sources: list[dict], origin: str) -> None:
    """Module-level convenience for the web tools."""
    facts.record_evidence(query, sources, origin)
