"""Persistent memory: SQLite records + ONNX embeddings (fastembed) semantic search."""
from __future__ import annotations

import asyncio
import logging
import sqlite3
import time

import numpy as np

from config import DB_PATH

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
"""


class MemoryStore:
    def __init__(self) -> None:
        self.db = sqlite3.connect(DB_PATH, check_same_thread=False)
        self.db.executescript(_SCHEMA)
        self.db.commit()
        self._embedder = None
        self._lock = asyncio.Lock()

    def _embed(self, texts: list[str]) -> np.ndarray:
        if self._embedder is None:
            from fastembed import TextEmbedding
            self._embedder = TextEmbedding("BAAI/bge-small-en-v1.5")
        return np.array(list(self._embedder.embed(texts)), dtype=np.float32)

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
            "SELECT id, ts, category, content, source, confidence FROM memories "
            "ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
        return [{"id": r[0], "ts": r[1], "category": r[2], "content": r[3],
                 "source": r[4], "confidence": r[5]} for r in rows]

    def forget(self, memory_id: int) -> bool:
        cur = self.db.execute("DELETE FROM memories WHERE id=?", (memory_id,))
        self.db.commit()
        return cur.rowcount > 0

    def log_turn(self, role: str, content: str) -> None:
        self.db.execute("INSERT INTO transcript (ts, role, content) VALUES (?,?,?)",
                        (time.time(), role, content))
        self.db.commit()

    def recent_transcript(self, n: int = 12) -> list[dict]:
        rows = self.db.execute(
            "SELECT role, content FROM transcript ORDER BY id DESC LIMIT ?", (n,)
        ).fetchall()
        return [{"role": r[0], "content": r[1]} for r in reversed(rows)]


memory = MemoryStore()
