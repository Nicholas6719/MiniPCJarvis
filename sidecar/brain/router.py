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
import logging
import sqlite3
import time

import numpy as np

from brain.skills import SKILLS, SKILL_BY_NAME, TOOL_TO_SKILL, Skill
from config import DB_PATH, config

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
"""


_CANON = [
    # (pattern, replacement) — applied to seeds AND queries so embeddings encode intent, not objects
    (r"\b(?:remind me|set a reminder|reminder)\b.*", "remind me at TIME to TASK"),
    (r"^(?:remember|note|keep in mind)\b.*", "remember that FACT"),
    (r"\b(?:show|find|pull up|get|display|bring up)\s+(?:me\s+)?(?:a\s+|some\s+|an\s+)?(?:picture|pictures|photo|photos|image|images|pic|pics)\s+of\s+.+", "show me pictures of THING"),
    (r"\b(?:search(?:\s+the\s+web|\s+online|\s+google)?\s+for|search(?:\s+the\s+web)?|look\s+up|google|find(?:\s+me)?(?:\s+online)?|web\s+search(?:\s+for)?|research)\s+.+", "search the web for THING"),
    (r"\b(?:volume|turn it|turn the volume|set the volume|set volume|make the volume|change the volume|lower the volume|raise the volume|put the volume)\b.*\d+.*", "set the volume to N percent"),
    (r"\b(?:open|go to|pull up|take me to|load|bring up|open up)\b.*\b[a-z0-9-]+\.(?:com|org|net|io|gov|edu|co|tv|ai|uk|ca)\b.*", "open the website SITE"),
    (r"\b(?:open|launch|start|run|fire up|bring up|put on)\s+(?:up\s+)?(?!(?:the\s+|my\s+)?(?:sound|audio|volume|music|pod bay)\b)(?:the\s+|my\s+)?[a-z0-9 .+#-]{2,40}", "open APP"),
    (r"\b(?:close|quit|exit|kill|shut down|shut)\s+(?!(?:the\s+|my\s+)?(?:sound|audio|volume|music|speakers|pc|computer)\b)(?:the\s+|my\s+)?[a-z0-9 .+#-]{2,40}", "close APP"),
    (r"\d+", "N"),
]


def _light(t: str) -> str:
    """Lowercase, strip the wake phrase and politeness — keeps all content words."""
    import re
    t = t.lower().strip()
    t = re.sub(r"^(?:hey|hi|ok|okay)?[,\s]*jarvis[,.!?\s]*", "", t)
    t = re.sub(r"\b(?:please|for me|can you|could you|would you|will you)\b", " ", t)
    t = re.sub(r"[^\w\s%':.-]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _norm(t: str) -> str:
    """Canonical intent form used for embeddings (objects -> placeholders)."""
    import re
    t = _light(t)
    for pat, rep in _CANON:
        t2 = re.sub(pat, rep, t, count=1)
        if t2 != t:
            t = t2
            break
    return re.sub(r"\s+", " ", t).strip()


class Brain:
    def __init__(self) -> None:
        self.db = sqlite3.connect(DB_PATH, check_same_thread=False)
        self.db.executescript(_SCHEMA)
        self.db.commit()
        self._embedder = None
        self._texts: list[str] = []
        self._skills: list[str] = []
        self._matrix: np.ndarray | None = None
        self._lock = asyncio.Lock()
        self.stats = {"reflex": 0, "llm": 0, "learned": 0}

    # ---------- embeddings ----------

    def _embed(self, texts: list[str]) -> np.ndarray:
        if self._embedder is None:
            from fastembed import TextEmbedding
            self._embedder = TextEmbedding("BAAI/bge-small-en-v1.5")
        vecs = np.array(list(self._embedder.embed(texts)), dtype=np.float32)
        vecs /= (np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-9)
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
            for text, skill in self.db.execute("SELECT text, skill FROM brain_examples WHERE source='seed'").fetchall():
                if canon.get(text) != skill:
                    self.db.execute("DELETE FROM brain_examples WHERE text=?", (text,))
            # seeds are ground truth: a learned row that contradicts a seed is a mislabel
            for text, skill in self.db.execute("SELECT text, skill FROM brain_examples WHERE source!='seed'").fetchall():
                if text in canon and canon[text] != skill:
                    self.db.execute("DELETE FROM brain_examples WHERE text=?", (text,))
                    log.info("brain: dropped mislabeled learned example %r (%s)", text, skill)
                elif not self._executable(text, skill):
                    self.db.execute("DELETE FROM brain_examples WHERE text=?", (text,))
                    log.info("brain: dropped unusable learned example %r (%s)", text, skill)
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
            self._matrix = (np.frombuffer(b"".join(r[2] for r in rows), dtype=np.float32)
                            .reshape(len(rows), -1)) if rows else None
            log.info("brain loaded: %d examples across %d skills", len(rows), len(SKILLS))

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
        }

    # ---------- classification ----------

    async def classify(self, text: str, k: int = 5) -> tuple[str | None, float]:
        """Return (skill, confidence).

        Top-match wins; confidence is the top similarity, penalized when the best
        example of a *different* skill is nearly as close (ambiguity)."""
        if self._matrix is None or not text.strip():
            return None, 0.0
        q = (await asyncio.to_thread(self._embed, [_norm(text)]))[0]
        sims = self._matrix @ q
        order = np.argsort(-sims)
        best = self._skills[order[0]]
        top = float(sims[order[0]])
        rival = 0.0
        for i in order[1:k * 4]:
            if self._skills[i] != best:
                rival = float(sims[i])
                break
        margin = top - rival
        confidence = top if margin >= 0.06 else top - (0.06 - margin) * 3.0
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

    def learned_from_tool(self, tool_name: str) -> str | None:
        return TOOL_TO_SKILL.get(tool_name)

    # ---------- decision ----------

    async def decide(self, text: str) -> tuple[Skill, dict, float] | None:
        """If confident and the slots extract cleanly, return (skill, args, confidence)."""
        threshold = float(config.get("brain", "threshold", default=0.82))
        name, conf = await self.classify(text)
        if not name or conf < threshold:
            return None
        skill = SKILL_BY_NAME[name]
        slots = skill.slots(_light(text))
        if slots is None:
            return None  # couldn't extract what the tool needs -> let the LLM handle it
        return skill, {**skill.fixed_args, **slots}, conf


brain = Brain()
