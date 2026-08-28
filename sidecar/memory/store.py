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
        self._lock = asyncio.Lock()

    def _embed(self, texts: list[str]) -> np.ndarray:
        if self._embedder is None:
            from fastembed import TextEmbedding
            self._embedder = TextEmbedding("BAAI/bge-small-en-v1.5")
        return np.array(list(self._embedder.embed(texts)), dtype=np.float32)

    async def embed_texts(self, texts: list[str]) -> np.ndarray:
        """Shared embedder for other stores (the fact store) — one ONNX model in RAM."""
        return await asyncio.to_thread(self._embed, texts)

    # ---- turn-path instrumentation (brain roadmap stage 1) --------------------
    def log_turn_stat(self, path: str, skill: str | None, latency_ms: int) -> None:
        try:
            self.db.execute("INSERT INTO turn_stats (ts, path, skill, latency_ms) VALUES (?,?,?,?)",
                            (time.time(), path, skill, latency_ms))
            self.db.commit()
        except Exception:
            log.exception("turn stat insert failed")

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
        rows = self.db.execute(
            "SELECT id, ts, category, content, source, confidence, embedding "
            "FROM memories WHERE embedding IS NOT NULL").fetchall()
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
        rows = self.db.execute(
            "SELECT content FROM memories WHERE pinned=1 ORDER BY ts DESC LIMIT ?",
            (limit,)).fetchall()
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

    def log_turn(self, role: str, content: str) -> None:
        self.db.execute("INSERT INTO transcript (ts, role, content) VALUES (?,?,?)",
                        (time.time(), role, content))
        self.db.commit()

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
        if any(out.values()):
            log.info("pruned old rows: %s", out)
        return out

    def recent_transcript(self, n: int = 12) -> list[dict]:
        # id and ts ride along: anything that needs to know WHICH turn a row
        # belongs to cannot use content alone (the same question recurs), and
        # counting rows breaks silently once the window is full.
        rows = self.db.execute(
            "SELECT id, ts, role, content FROM transcript ORDER BY id DESC LIMIT ?", (n,)
        ).fetchall()
        return [{"id": r[0], "ts": r[1], "role": r[2], "content": r[3]}
                for r in reversed(rows)]


memory = MemoryStore()
