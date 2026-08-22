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
    (r"\b(?:remind me|set a reminder|reminder)\b.*", "remind me at TIME to TASK"),
    (r"^(?:remember|note|keep in mind)\b.*", "remember that FACT"),
    (r"\b(?:open|show|browse|go to|list|look at|pull up|what's (?:in|on))\b.*\b(?:desktop|documents|docs|downloads|pictures|photos)\b(?!\s+(?:of|from)\b).*", "open my FOLDER folder"),
    (r"\b(?:find|look for|locate|where is|where's)\b.*\b(?:file|folder|document|resume|screenshot|invoice|report|notes?|photo|picture)s?\b(?!\s+(?:of|from)\b).*", "find the file called NAME"),
    (r".*\b(?:file|folder|document)s?\s+(?:called|named|with|containing)\b.*", "find the file called NAME"),
    (r"\bsearch (?:my )?(?:desktop|documents|downloads|pictures) for\b.*", "find the file called NAME"),
    (r"\b(?:show|find|pull up|get|display|bring up)\s+(?:me\s+)?(?:a\s+|some\s+|an\s+)?(?:picture|pictures|photo|photos|image|images|pic|pics)\s+of\s+.+", "show me pictures of THING"),
    (r"\b(?:search(?:\s+the\s+web|\s+online|\s+google)?\s+for|search(?:\s+the\s+web)?|look\s+up|google|find(?:\s+me)?(?:\s+online)?|web\s+search(?:\s+for)?|research)\s+.+", "search the web for THING"),
    (r"\b(?:volume|turn it|turn the volume|set the volume|set volume|make the volume|change the volume|lower the volume|raise the volume|put the volume)\b.*\d+.*", "set the volume to N percent"),
    (r"\b(?:open|go to|pull up|take me to|load|bring up|open up)\b.*\b[a-z0-9-]+\.(?:com|org|net|io|gov|edu|co|tv|ai|uk|ca)\b.*", "open the website SITE"),
    (r"\b(?:open|launch|start|run|fire up|bring up|put on)\s+(?:up\s+)?(?!(?:the\s+|my\s+)?(?:sound|audio|volume|music|pod bay|desktop|documents|docs|downloads|pictures|photos)\b)(?:the\s+|my\s+)?[a-z0-9 .+#-]{2,40}", "open APP"),
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
        self._cmd_phrases: list[str] = []
        self._cmd_steps: list[list[dict]] = []
        self._cmd_matrix: np.ndarray | None = None
        self.last_match: dict | None = None   # what the last decide() matched (for corrections)

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
            crows = self.db.execute("SELECT phrase, steps, embedding FROM brain_commands").fetchall()
            self._cmd_phrases = [r[0] for r in crows]
            self._cmd_steps = [json.loads(r[1]) for r in crows]
            self._cmd_matrix = (np.frombuffer(b"".join(r[2] for r in crows), dtype=np.float32)
                                .reshape(len(crows), -1)) if crows else None
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
        src = self.db.execute("SELECT source FROM brain_examples WHERE text=?",
                              (self._texts[order[0]],)).fetchone()
        self.last_match = {"text": self._texts[order[0]], "skill": best,
                           "source": src[0] if src else "seed", "query": text,
                           "confidence": round(max(0.0, confidence), 3)}
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
