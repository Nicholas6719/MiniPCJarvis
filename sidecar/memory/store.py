"""Persistent memory: SQLite records + ONNX embeddings (fastembed) semantic search."""
from __future__ import annotations

import asyncio
import logging
import time

import numpy as np

from config import open_db

log = logging.getLogger("jarvis.memory")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    category TEXT NOT NULL DEFAULT 'fact',
    content TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'conversation',
    confidence TEXT NOT NULL DEFAULT 'medium',
    embedding BLOB
);
CREATE TABLE IF NOT EXISTS transcript (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS turn_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    path TEXT NOT NULL,          -- routine | reflex | fact | tool_then_llm | llm_general | llm_tools
    skill TEXT,
    latency_ms INTEGER
);
CREATE TABLE IF NOT EXISTS deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    tier TEXT NOT NULL,
    outcome TEXT NOT NULL,       -- spoken | telegram | held ... | nothing
    why TEXT NOT NULL DEFAULT '',
    subject TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL DEFAULT ''
);
"""


class MemoryStore:
    def __init__(self) -> None:
        self.db = open_db()
        self.db.executescript(_SCHEMA)
        # migration: pinned flag
        cols = [r[1] for r in self.db.execute("PRAGMA table_info(memories)")]
        if "pinned" not in cols:
            self.db.execute("ALTER TABLE memories ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0")
        self.db.commit()
        self._embedder = None
        from collections import OrderedDict
        self._embed_cache: OrderedDict = OrderedDict()
        self._lock = asyncio.Lock()

    # The SAME utterance is embedded three times on every LLM turn - once by
    # facts.lookup, once by memory.search, once by the tool shortlist - at
    # ~40-55ms each, on the threads llama-server is trying to use. The router
    # already keeps a cache like this; this store had none.
    _EMBED_CACHE_MAX = 256

    def _embed(self, texts: list[str]) -> np.ndarray:
        if self._embedder is None:
            from fastembed import TextEmbedding
            self._embedder = TextEmbedding("BAAI/bge-small-en-v1.5")
        cache = self._embed_cache
        missing = [t for t in texts if t not in cache]
        if missing:
            fresh = np.array(list(self._embedder.embed(missing)), dtype=np.float32)
            for text, vec in zip(missing, fresh):
                cache[text] = vec
                cache.move_to_end(text)
            while len(cache) > self._EMBED_CACHE_MAX:
                cache.popitem(last=False)
        out = []
        for t in texts:
            cache.move_to_end(t)
            out.append(cache[t])
        return np.array(out, dtype=np.float32)

    async def embed_texts(self, texts: list[str]) -> np.ndarray:
        """Shared embedder for other stores (the fact store) — one ONNX model in RAM."""
        return await asyncio.to_thread(self._embed, texts)

    def _unlock(self) -> None:
        """Release the write lock after a failed write. Never raises.

        Every method here shares ONE connection, and a statement that raises
        leaves the implicit transaction it opened still open — holding SQLite's
        single write lock. So one broken table does not stay one broken table:
        it becomes "database is locked" for the transcript, the memories and
        the facts alike, which is exactly the shape of the 2026-08-31 outage.
        Rolling back is what keeps a local failure local.
        """
        try:
            self.db.rollback()
        except Exception:
            # A rollback that itself fails means the connection is in trouble,
            # and an unreleased write lock is exactly what stopped every turn
            # on 2026-08-31. Never learn about that one from the symptoms.
            log.warning('rollback failed on the shared connection', exc_info=True)

    # ---- turn-path instrumentation (brain roadmap stage 1) --------------------
    def log_turn_stat(self, path: str, skill: str | None, latency_ms: int) -> None:
        try:
            self.db.execute("INSERT INTO turn_stats (ts, path, skill, latency_ms) VALUES (?,?,?,?)",
                            (time.time(), path, skill, latency_ms))
            self.db.commit()
        except Exception:
            log.exception("turn stat insert failed")
            self._unlock()

    def turn_stats_summary(self, days: int = 7) -> dict:
        """What fraction of turns woke the LLM, and what they cost — the data that
        decides which brain investment pays next."""
        since = time.time() - days * 86400
        rows = self.db.execute(
            "SELECT path, COUNT(*), AVG(latency_ms), SUM(latency_ms) FROM turn_stats "
            "WHERE ts >= ? GROUP BY path ORDER BY COUNT(*) DESC", (since,)).fetchall()
        total = sum(r[1] for r in rows) or 1
        return {"days": days, "total_turns": total,
                "paths": [{"path": r[0], "turns": r[1], "share": round(r[1] / total, 3),
                           "avg_ms": int(r[2] or 0), "total_s": int((r[3] or 0) / 1000)}
                          for r in rows]}

    async def remember(self, content: str, category: str = "fact",
                       source: str = "conversation", confidence: str = "medium") -> int:
        async with self._lock:
            # dedupe: skip if a nearly-identical memory exists
            existing = await self.search(content, top_k=1, min_score=0.92)
            if existing:
                return existing[0]["id"]
            vec = await asyncio.to_thread(self._embed, [content])
            cur = self.db.execute(
                "INSERT INTO memories (ts, category, content, source, confidence, embedding) "
                "VALUES (?,?,?,?,?,?)",
                (time.time(), category, content, source, confidence, vec[0].tobytes()))
            self.db.commit()
            return cur.lastrowid

    async def search(self, query: str, top_k: int = 5,
                     min_score: float = 0.35) -> list[dict]:
        # Recall is an ENRICHMENT of a turn, not a precondition for one. Same
        # lesson as log_turn: on 2026-08-31 an un-guarded read in the turn path
        # was all it took to make JARVIS silent. Without memory he answers with
        # less; raising here means he does not answer at all.
        try:
            rows = self.db.execute(
                "SELECT id, ts, category, content, source, confidence, embedding "
                "FROM memories WHERE embedding IS NOT NULL").fetchall()
        except Exception:
            log.exception("memory search failed; answering without recall")
            return []
        if not rows:
            return []
        qv = (await asyncio.to_thread(self._embed, [query]))[0]
        qv = qv / (np.linalg.norm(qv) + 1e-9)
        out = []
        for r in rows:
            v = np.frombuffer(r[6], dtype=np.float32)
            v = v / (np.linalg.norm(v) + 1e-9)
            score = float(np.dot(qv, v))
            if score >= min_score:
                out.append({"id": r[0], "ts": r[1], "category": r[2],
                            "content": r[3], "source": r[4],
                            "confidence": r[5], "score": round(score, 3)})
        out.sort(key=lambda m: m["score"], reverse=True)
        return out[:top_k]

    def list_all(self, limit: int = 200) -> list[dict]:
        rows = self.db.execute(
            "SELECT id, ts, category, content, source, confidence, pinned FROM memories "
            "ORDER BY pinned DESC, ts DESC LIMIT ?", (limit,)).fetchall()
        return [{"id": r[0], "ts": r[1], "category": r[2], "content": r[3],
                 "source": r[4], "confidence": r[5], "pinned": bool(r[6])} for r in rows]

    def list_pinned(self, limit: int = 10) -> list[str]:
        # Also on the turn path, also enrichment, also must not raise.
        try:
            rows = self.db.execute(
                "SELECT content FROM memories WHERE pinned=1 ORDER BY ts DESC LIMIT ?",
                (limit,)).fetchall()
        except Exception:
            log.exception("could not read pinned memories")
            return []
        return [r[0] for r in rows]

    def set_pinned(self, memory_id: int, pinned: bool) -> bool:
        cur = self.db.execute("UPDATE memories SET pinned=? WHERE id=?",
                              (int(pinned), memory_id))
        self.db.commit()
        return cur.rowcount > 0

    async def update_content(self, memory_id: int, content: str) -> bool:
        vec = await asyncio.to_thread(self._embed, [content])
        cur = self.db.execute(
            "UPDATE memories SET content=?, embedding=?, source='edited' WHERE id=?",
            (content, vec[0].tobytes(), memory_id))
        self.db.commit()
        return cur.rowcount > 0

    def forget(self, memory_id: int) -> bool:
        cur = self.db.execute("DELETE FROM memories WHERE id=?", (memory_id,))
        self.db.commit()
        return cur.rowcount > 0

    def log_delivery(self, row: dict) -> None:
        """One thing JARVIS said, sent or held on his own initiative. Never raises.

        The ledger used to live only in memory, so "what did I miss" knew
        nothing after any restart - and a release restarts him. This is what
        the morning greeting and "catch me up" read after a night."""
        try:
            self.db.execute(
                "INSERT INTO deliveries (ts, tier, outcome, why, subject, text) VALUES (?,?,?,?,?,?)",
                (float(row.get("ts") or time.time()), str(row.get("tier") or ""),
                 str(row.get("outcome") or "nothing"), str(row.get("why") or ""),
                 str(row.get("subject") or "")[:120], str(row.get("text") or "")[:160]))
            self.db.commit()
        except Exception:
            log.exception("could not log a delivery")
            self._unlock()

    def recent_deliveries(self, since: float, limit: int = 200) -> list[dict]:
        try:
            rows = self.db.execute(
                "SELECT ts, tier, outcome, why, subject, text FROM deliveries "
                "WHERE ts >= ? ORDER BY ts DESC LIMIT ?", (float(since), int(limit))).fetchall()
        except Exception:
            log.exception("could not read the deliveries")
            return []
        return [{"ts": r[0], "tier": r[1], "outcome": r[2], "why": r[3],
                 "subject": r[4], "text": r[5]} for r in reversed(rows)]

    def last_user_turn_ts(self) -> float:
        """When he last said anything - across restarts, from the transcript."""
        try:
            row = self.db.execute(
                "SELECT ts FROM transcript WHERE role='user' ORDER BY ts DESC LIMIT 1").fetchone()
            return float(row[0]) if row else 0.0
        except Exception:
            return 0.0

    def log_turn(self, role: str, content: str) -> None:
        """Record a line of conversation. Never raises.

        On 2026-08-31 the `transcript` b-tree corrupted, and because this line
        sat un-guarded in the middle of `_converse`, EVERY turn died on it:
        health was green, routing was correct, and he got no reply to anything
        for hours. Writing the history is bookkeeping. Losing it costs him a
        transcript; letting it raise costs him the assistant.
        """
        if role == "assistant":
            # The model still slips markdown out now and then ("*Moby Dick*");
            # the ear never heard it and the screen should not read it either.
            try:
                from audio.speech_text import strip_markdown
                content = strip_markdown(content)
            except Exception:
                pass
        try:
            self.db.execute("INSERT INTO transcript (ts, role, content) VALUES (?,?,?)",
                            (time.time(), role, content))
            self.db.commit()
        except Exception:
            log.exception("could not log a %s turn to the transcript", role)
            self._unlock()

    def prune(self, keep_turns: int = 20000, keep_audit: int = 20000,
              stats_days: int = 120) -> dict:
        """Keep the database from growing forever. Called once at startup.

        The transcript and the tool audit log are append-only and had no bound at
        all: at a few hundred rows a day they are harmless for years, but "harmless
        for years" is how databases become slow in year three. Memories, facts and
        brain examples are NEVER pruned — those are the things he knows."""
        out = {}
        try:
            for table, keep in (("transcript", keep_turns), ("audit_log", keep_audit)):
                exists = self.db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                    (table,)).fetchone()
                if not exists:
                    continue
                cur = self.db.execute(
                    f"DELETE FROM {table} WHERE id NOT IN "
                    f"(SELECT id FROM {table} ORDER BY id DESC LIMIT ?)", (keep,))
                out[table] = cur.rowcount
            cur = self.db.execute("DELETE FROM turn_stats WHERE ts < ?",
                                  (time.time() - stats_days * 86400,))
            out["turn_stats"] = cur.rowcount
            self.db.commit()
        except Exception:
            log.exception("prune failed")
            self._unlock()
        if any(out.values()):
            log.info("pruned old rows: %s", out)
        return out

    def recent_transcript(self, n: int = 12) -> list[dict]:
        # id and ts ride along: anything that needs to know WHICH turn a row
        # belongs to cannot use content alone (the same question recurs), and
        # counting rows breaks silently once the window is full.
        # Same reasoning as log_turn: a damaged transcript must cost him his
        # history, not his ability to hold a conversation. He would rather be
        # answered without context than not answered at all.
        try:
            rows = self.db.execute(
                "SELECT id, ts, role, content FROM transcript ORDER BY id DESC LIMIT ?", (n,)
            ).fetchall()
        except Exception:
            log.exception("could not read recent transcript; continuing without history")
            return []
        return [{"id": r[0], "ts": r[1], "role": r[2], "content": r[3]}
                for r in reversed(rows)]


memory = MemoryStore()
