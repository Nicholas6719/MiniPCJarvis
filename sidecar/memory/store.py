"""Persistent memory: SQLite records + ONNX embeddings (fastembed) semantic search.

The `memories` table is an INDEX over a folder of markdown files (vault.py):
the files are the truth, and this store makes the table match them before
every read. Everything else here — transcript, turn stats, deliveries, the
readings history — is the database's own.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import deque
from datetime import date
from pathlib import Path

import numpy as np

from config import open_db
from recording import recording as _recording
from memory import vault

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
-- Readings kept over time, so he can answer "compared to what?". One row a
-- minute from proactive.run_checks, pruned past a fortnight. Deliberately
-- dumb: no rollups, no compaction. The moment this needs a maintenance job it
-- has become a worse idea than it was.
CREATE TABLE IF NOT EXISTS readings (
    ts           REAL NOT NULL,
    cpu          REAL,
    ram_pct      REAL,
    disk_free_gb REAL,
    battery      REAL,
    plugged      INTEGER
);
CREATE INDEX IF NOT EXISTS readings_ts ON readings (ts);
-- The application in front, by exe NAME only. Never the window title: titles
-- carry the subject of the email and the name of the document, none of which
-- belongs in a log on disk because he asked what he had been doing.
CREATE TABLE IF NOT EXISTS app_context (
    ts  REAL NOT NULL,
    exe TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS app_context_ts ON app_context (ts);
"""

READING_FIELDS = {"cpu": "the processor", "ram_pct": "memory",
                  "disk_free_gb": "free disk space", "battery": "the battery"}
_FIELD_ALIASES = {"ram": "ram_pct", "memory": "ram_pct", "processor": "cpu", "load": "cpu",
                  "disk": "disk_free_gb", "storage": "disk_free_gb", "drive": "disk_free_gb",
                  "space": "disk_free_gb", "power": "battery"}
READINGS_KEEP_DAYS = 14
# exe -> how a person says it, for "what have I been doing"
_APP_NAMES = {"code.exe": "VS Code", "brave.exe": "Brave", "chrome.exe": "Chrome", "msedge.exe": "Edge",
              "firefox.exe": "Firefox", "discord.exe": "Discord", "spotify.exe": "Spotify",
              "winword.exe": "Word", "excel.exe": "Excel", "powerpnt.exe": "PowerPoint",
              "handbrake.exe": "HandBrake", "blender.exe": "Blender", "steam.exe": "Steam",
              "windowsterminal.exe": "the terminal", "notepad.exe": "Notepad", "outlook.exe": "Outlook",
              "devenv.exe": "Visual Studio", "cursor.exe": "Cursor", "claude.exe": "Claude",
              "obsidian.exe": "Obsidian", "orcaslicer.exe": "OrcaSlicer", "bambustudio.exe": "Bambu Studio"}


class MemoryStore:
    def __init__(self) -> None:
        self.db = open_db()
        self.db.executescript(_SCHEMA)
        # unrecorded (a test's) turns, readable but never written; see log_turn
        self._scratch: deque = deque(maxlen=200)
        self._scratch_id = 10 ** 9
        # migration: pinned flag
        cols = [r[1] for r in self.db.execute("PRAGMA table_info(memories)")]
        if "pinned" not in cols:
            self.db.execute("ALTER TABLE memories ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0")
        self.db.commit()
        self._embedder = None
        from collections import OrderedDict
        self._embed_cache: OrderedDict = OrderedDict()
        self._lock = asyncio.Lock()
        # THE VAULT. The memories live as markdown, one file each, so they can
        # be read and corrected without a database client; this table is the
        # index over them. None, not (): an empty tuple is what a folder that
        # does not exist yet fingerprints as, and starting there would make the
        # first sync decide nothing had changed and skip the migration.
        self.vault: Path = vault.default_folder()
        self._vault_seen: tuple | None = None
        self._vault_files: dict[int, str] = {}       # memory id -> file path
        self._last_readings_prune = 0.0

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
    def log_turn_stat(self, path: str, skill: str | None, latency_ms: int,
                      first_ms: int | None = None, prompt_tokens: int | None = None,
                      gen_tokens: int | None = None, reasoning_chars: int | None = None) -> None:
        """latency_ms is the WHOLE turn, speech included - a long weather
        sentence 'takes 4.5 s' with its first word at 160 ms. first_ms is the
        time to that first word, the number that is actually felt; the token
        counts say what the model spent (2026-09-18)."""
        if not _recording():
            return             # a test's turn: his numbers stay his (recording.py)
        try:
            self._turn_stats_columns()
            self.db.execute("INSERT INTO turn_stats (ts, path, skill, latency_ms, first_ms, "
                            "prompt_tokens, gen_tokens, reasoning_chars) VALUES (?,?,?,?,?,?,?,?)",
                            (time.time(), path, skill, latency_ms, first_ms, prompt_tokens,
                             gen_tokens, reasoning_chars))
            self.db.commit()
        except Exception:
            log.exception("turn stat insert failed")
            self._unlock()

    _stats_cols_ok = False

    def _turn_stats_columns(self) -> None:
        """The four columns added on 2026-09-18, on a table that already has
        ten thousand rows in his real database: ALTER, once, and never fail."""
        if self._stats_cols_ok:
            return
        have = {r[1] for r in self.db.execute("PRAGMA table_info(turn_stats)")}
        for col in ("first_ms", "prompt_tokens", "gen_tokens", "reasoning_chars"):
            if col not in have:
                self.db.execute(f"ALTER TABLE turn_stats ADD COLUMN {col} INTEGER")
        self.db.commit()
        self._stats_cols_ok = True

    def turn_stats_report(self, hours: float = 24) -> dict:
        """Yesterday, in numbers: how many turns, how fast the first word
        came on reflexes and on the model, the slowest turn, what the model
        spent. The morning brief speaks one sentence of it (self_report.py)."""
        self._turn_stats_columns()
        since = time.time() - hours * 3600
        rows = self.db.execute(
            "SELECT path, skill, latency_ms, first_ms, prompt_tokens, gen_tokens, reasoning_chars "
            "FROM turn_stats WHERE ts >= ?", (since,)).fetchall()

        def med(vals):
            vals = sorted(v for v in vals if v is not None)
            return int(vals[len(vals) // 2]) if vals else None

        reflex = [r for r in rows if r[0] in ("reflex", "fact", "routine")]
        model = [r for r in rows if r[0] not in ("reflex", "fact", "routine")]
        slow = max(rows, key=lambda r: r[2] or 0) if rows else None
        return {
            "hours": hours, "turns": len(rows), "reflex_turns": len(reflex), "model_turns": len(model),
            "reflex_first_ms": med(r[3] for r in reflex), "model_first_ms": med(r[3] for r in model),
            "reflex_total_ms": med(r[2] for r in reflex), "model_total_ms": med(r[2] for r in model),
            "model_prompt_tokens": med(r[4] for r in model), "model_gen_tokens": med(r[5] for r in model),
            "model_reasoning_chars": med(r[6] for r in model),
            "slowest": {"path": slow[0], "skill": slow[1], "ms": slow[2]} if slow else None,
        }

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

    # ---- the vault: files are the truth, this table is the index ----------------
    def _vault_plan(self) -> tuple[list[dict], dict[int, dict]] | None:
        """What the files say and what the table says, or None when the folder
        has not changed since the last look. The fingerprint is names, mtimes
        and sizes; nothing is opened unless one of them moved."""
        seen = vault.fingerprint(self.vault)
        if seen == self._vault_seen:
            return None
        files = vault.scan(self.vault)
        if not files and not (self.vault / "README.md").exists():
            # FIRST RUN: the folder has never been written. Whatever the table
            # holds goes out to files before anything can be compared, or every
            # memory he has would look like a file he deleted.
            self._migrate_into_vault()
            files = vault.scan(self.vault)
        rows = {r[0]: {"content": r[1], "pinned": bool(r[2]), "category": r[3]}
                for r in self.db.execute(
                    "SELECT id, content, pinned, category FROM memories").fetchall()}
        return files, rows

    def _vault_texts(self, plan) -> list[str]:
        """The texts a sync would have to embed: new files and edited ones."""
        files, rows = plan
        out = []
        for f in files:
            row = rows.get(f["id"]) if f["id"] is not None else None
            if row is None or row["content"] != f["content"]:
                out.append(f["content"])
        return out

    def _vault_apply(self, plan, vectors: dict[str, np.ndarray]) -> None:
        """Make the table match the files. Edited text is re-embedded; a file
        that vanished is a memory he wants forgotten; a new file is learned
        and given its id so later edits map back to the same row."""
        files, rows = plan
        claimed: set[int] = set()
        self._vault_files = {}
        for f in files:
            fid = f["id"]
            content = f["content"]
            row = rows.get(fid) if fid is not None else None
            if row is not None and fid not in claimed:
                claimed.add(fid)
                self._vault_files[fid] = f["path"]
                if row["content"] != content:
                    vec = vectors.get(content)
                    if vec is None:
                        vec = self._embed([content])[0]
                    self.db.execute(
                        "UPDATE memories SET content=?, embedding=?, source='edited', "
                        "pinned=?, category=? WHERE id=?",
                        (content, np.asarray(vec, dtype=np.float32).tobytes(),
                         int(f["pinned"]), f["category"], fid))
                elif row["pinned"] != f["pinned"] or row["category"] != f["category"]:
                    self.db.execute("UPDATE memories SET pinned=?, category=? WHERE id=?",
                                    (int(f["pinned"]), f["category"], fid))
                continue
            vec = vectors.get(content)
            if vec is None:
                vec = self._embed([content])[0]
            try:
                ts = time.mktime(time.strptime(f["created"], "%Y-%m-%d")) if f["created"] else time.time()
            except ValueError:
                ts = time.time()
            cur = self.db.execute(
                "INSERT INTO memories (ts, category, content, source, confidence, embedding, pinned) "
                "VALUES (?,?,?,?,?,?,?)",
                (ts, f["category"], content, f["source"], f["confidence"],
                 np.asarray(vec, dtype=np.float32).tobytes(), int(f["pinned"])))
            new_id = cur.lastrowid
            claimed.add(new_id)
            # stamp the id into the file so an edit later finds its row
            path = vault.write(self.vault, content, mid=new_id, category=f["category"],
                               created=f["created"], source=f["source"], pinned=f["pinned"],
                               confidence=f["confidence"], path=Path(f["path"]))
            self._vault_files[new_id] = str(path)
        for rid in rows:
            if rid not in claimed:
                self.db.execute("DELETE FROM memories WHERE id=?", (rid,))
        self.db.commit()
        self._vault_seen = vault.fingerprint(self.vault)

    def _sync_vault(self) -> None:
        """Make the index match the files, synchronously. Never raises: recall
        is an enrichment of a turn, and a broken folder must cost him the
        folder, not the assistant."""
        try:
            plan = self._vault_plan()
            if plan is None:
                return
            texts = self._vault_texts(plan)
            vectors = dict(zip(texts, self._embed(texts))) if texts else {}
            self._vault_apply(plan, vectors)
        except Exception:
            log.exception("could not sync the memory vault; using the index as it is")
            self._unlock()

    async def _sync_vault_async(self) -> None:
        """The same, with the embedding off the loop (embed_texts)."""
        try:
            plan = self._vault_plan()
            if plan is None:
                return
            texts = self._vault_texts(plan)
            vectors = dict(zip(texts, await self.embed_texts(texts))) if texts else {}
            self._vault_apply(plan, vectors)
        except Exception:
            log.exception("could not sync the memory vault; using the index as it is")
            self._unlock()

    def _migrate_into_vault(self) -> None:
        """First run: write out whatever the table already held."""
        rows = self.db.execute(
            "SELECT id, ts, category, content, source, confidence, pinned FROM memories").fetchall()
        vault.ensure_readme(self.vault)
        for r in rows:
            try:
                path = vault.write(self.vault, r[3], mid=r[0], category=r[2],
                                   created=date.fromtimestamp(float(r[1] or time.time())).isoformat(),
                                   source=r[4], pinned=bool(r[6]), confidence=r[5])
                self._vault_files[r[0]] = str(path)
            except Exception:
                log.warning("could not write memory %s to the vault", r[0], exc_info=True)
        if rows:
            log.info("moved %d memories into %s", len(rows), self.vault)

    def _write_file(self, memory_id: int) -> None:
        """The file for one row, after the row changed. Never raises."""
        try:
            r = self.db.execute(
                "SELECT id, ts, category, content, source, confidence, pinned FROM memories WHERE id=?",
                (memory_id,)).fetchone()
            if not r:
                return
            known = self._vault_files.get(memory_id)
            path = Path(known) if known and os.path.isfile(known) else None
            if path is None:
                # the index may be cold: find the file by its id
                for f in vault.scan(self.vault):
                    if f["id"] == memory_id:
                        path = Path(f["path"])
                        break
            created = date.fromtimestamp(float(r[1] or time.time())).isoformat()
            if path is not None:
                have = vault.parse(path) or {}
                created = have.get("created") or created
            path = vault.write(self.vault, r[3], mid=r[0], category=r[2], created=created,
                               source=r[4], pinned=bool(r[6]), confidence=r[5], path=path)
            self._vault_files[memory_id] = str(path)
            self._vault_seen = vault.fingerprint(self.vault)
        except Exception:
            log.warning("could not write memory %s to the vault", memory_id, exc_info=True)

    def _remove_file(self, memory_id: int) -> None:
        try:
            known = self._vault_files.pop(memory_id, None)
            paths = [known] if known else [f["path"] for f in vault.scan(self.vault)
                                           if f["id"] == memory_id]
            for p in paths:
                if p and os.path.isfile(p):
                    os.remove(p)
            self._vault_seen = vault.fingerprint(self.vault)
        except Exception:
            log.warning("could not remove memory %s from the vault", memory_id, exc_info=True)

    async def remember(self, content: str, category: str = "fact",
                       source: str = "conversation", confidence: str = "medium") -> int:
        async with self._lock:
            # dedupe: skip if a nearly-identical memory exists
            existing = await self.search(content, top_k=1, min_score=0.92)
            if existing:
                return existing[0]["id"]
            vec = await asyncio.to_thread(self._embed, [content])
            try:
                cur = self.db.execute(
                    "INSERT INTO memories (ts, category, content, source, confidence, embedding) "
                    "VALUES (?,?,?,?,?,?)",
                    (time.time(), category, content, source, confidence, vec[0].tobytes()))
                self.db.commit()
            except Exception:
                self._unlock()
                raise
            self._write_file(cur.lastrowid)
            return cur.lastrowid

    async def search(self, query: str, top_k: int = 5,
                     min_score: float = 0.35) -> list[dict]:
        # Recall is an ENRICHMENT of a turn, not a precondition for one. Same
        # lesson as log_turn: on 2026-08-31 an un-guarded read in the turn path
        # was all it took to make JARVIS silent. Without memory he answers with
        # less; raising here means he does not answer at all.
        await self._sync_vault_async()
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
        self._sync_vault()
        rows = self.db.execute(
            "SELECT id, ts, category, content, source, confidence, pinned FROM memories "
            "ORDER BY pinned DESC, ts DESC LIMIT ?", (limit,)).fetchall()
        return [{"id": r[0], "ts": r[1], "category": r[2], "content": r[3],
                 "source": r[4], "confidence": r[5], "pinned": bool(r[6])} for r in rows]

    def list_pinned(self, limit: int = 10) -> list[str]:
        # Also on the turn path, also enrichment, also must not raise.
        self._sync_vault()
        try:
            rows = self.db.execute(
                "SELECT content FROM memories WHERE pinned=1 ORDER BY ts DESC LIMIT ?",
                (limit,)).fetchall()
        except Exception:
            log.exception("could not read pinned memories")
            return []
        return [r[0] for r in rows]

    def set_pinned(self, memory_id: int, pinned: bool) -> bool:
        self._sync_vault()
        try:
            cur = self.db.execute("UPDATE memories SET pinned=? WHERE id=?",
                                  (int(pinned), memory_id))
            self.db.commit()
        except Exception:
            self._unlock()
            raise
        if cur.rowcount > 0:
            self._write_file(memory_id)
        return cur.rowcount > 0

    async def update_content(self, memory_id: int, content: str) -> bool:
        await self._sync_vault_async()
        vec = await asyncio.to_thread(self._embed, [content])
        try:
            cur = self.db.execute(
                "UPDATE memories SET content=?, embedding=?, source='edited' WHERE id=?",
                (content, vec[0].tobytes(), memory_id))
            self.db.commit()
        except Exception:
            self._unlock()
            raise
        if cur.rowcount > 0:
            self._write_file(memory_id)
        return cur.rowcount > 0

    def forget(self, memory_id: int) -> bool:
        self._sync_vault()
        try:
            cur = self.db.execute("DELETE FROM memories WHERE id=?", (memory_id,))
            self.db.commit()
        except Exception:
            self._unlock()
            raise
        self._remove_file(memory_id)
        return cur.rowcount > 0

    # ---- the delivery ledger -----------------------------------------------------
    _deliveries_cols_ok = False

    def _deliveries_columns(self) -> None:
        """`replayed`: a held message he has since been told about. ALTER, once,
        on a table his real database already has rows in — the same shape as
        _turn_stats_columns. The row is addressed by SQLite's rowid, which every
        version of this table has had whether or not it declared an id."""
        if self._deliveries_cols_ok:
            return
        have = {r[1] for r in self.db.execute("PRAGMA table_info(deliveries)")}
        if "replayed" not in have:
            self.db.execute("ALTER TABLE deliveries ADD COLUMN replayed INTEGER NOT NULL DEFAULT 0")
        self.db.commit()
        self._deliveries_cols_ok = True

    def log_delivery(self, row: dict) -> int | None:
        """One thing JARVIS said, sent or held on his own initiative. Never raises.

        The ledger used to live only in memory, so "what did I miss" knew
        nothing after any restart - and a release restarts him. This is what
        the morning greeting and "catch me up" read after a night. Returns the
        row's id so the ledger in memory can mark it later."""
        try:
            self._deliveries_columns()
            cur = self.db.execute(
                "INSERT INTO deliveries (ts, tier, outcome, why, subject, text, replayed) "
                "VALUES (?,?,?,?,?,?,?)",
                (float(row.get("ts") or time.time()), str(row.get("tier") or ""),
                 str(row.get("outcome") or "nothing"), str(row.get("why") or ""),
                 str(row.get("subject") or "")[:120], str(row.get("text") or "")[:300],
                 int(bool(row.get("replayed")))))
            self.db.commit()
            return cur.lastrowid
        except Exception:
            log.exception("could not log a delivery")
            self._unlock()
            return None

    def recent_deliveries(self, since: float, limit: int = 200) -> list[dict]:
        try:
            self._deliveries_columns()
            rows = self.db.execute(
                "SELECT rowid, ts, tier, outcome, why, subject, text, replayed FROM deliveries "
                "WHERE ts >= ? ORDER BY ts DESC LIMIT ?", (float(since), int(limit))).fetchall()
        except Exception:
            log.exception("could not read the deliveries")
            self._unlock()
            return []
        return [{"id": r[0], "ts": r[1], "tier": r[2], "outcome": r[3], "why": r[4],
                 "subject": r[5], "text": r[6], "replayed": bool(r[7])} for r in reversed(rows)]

    def mark_delivery(self, row_id: int, replayed: bool = True) -> bool:
        """He has now been told. Never raises."""
        try:
            self._deliveries_columns()
            cur = self.db.execute("UPDATE deliveries SET replayed=? WHERE rowid=?",
                                  (int(bool(replayed)), int(row_id)))
            self.db.commit()
            return cur.rowcount > 0
        except Exception:
            log.exception("could not mark delivery %s", row_id)
            self._unlock()
            return False

    # ---- readings history: "compared to what?" --------------------------------------
    def record_reading(self, cpu=None, ram_pct=None, disk_free_gb=None,
                       battery=None, plugged=None) -> None:
        """One sample. Never raises — history is a nicety, not a dependency.
        Pruned once an hour rather than once a sample: deleting nothing sixty
        times an hour is still sixty write transactions."""
        try:
            self.db.execute(
                "INSERT INTO readings (ts, cpu, ram_pct, disk_free_gb, battery, plugged) "
                "VALUES (?,?,?,?,?,?)",
                (time.time(), cpu, ram_pct, disk_free_gb, battery,
                 None if plugged is None else int(bool(plugged))))
            if time.time() - self._last_readings_prune > 3600:
                self._last_readings_prune = time.time()
                cutoff = time.time() - READINGS_KEEP_DAYS * 86400
                self.db.execute("DELETE FROM readings WHERE ts < ?", (cutoff,))
                self.db.execute("DELETE FROM app_context WHERE ts < ?", (cutoff,))
            self.db.commit()
        except Exception:
            log.debug("could not record a reading", exc_info=True)
            self._unlock()

    def record_app(self, exe: str) -> None:
        """The exe in front, by NAME ('code.exe'), never a window title."""
        name = os.path.basename(str(exe or "")).strip().lower()
        if not name or os.sep in name or "/" in name or " " in name:
            return          # a window title is not an exe name, and never goes on disk
        try:
            self.db.execute("INSERT INTO app_context (ts, exe) VALUES (?,?)", (time.time(), name[:80]))
            self.db.commit()
        except Exception:
            log.debug("could not record the foreground app", exc_info=True)
            self._unlock()

    @staticmethod
    def reading_field(name: str) -> str | None:
        f = (name or "ram_pct").lower().strip()
        f = _FIELD_ALIASES.get(f, f)
        return f if f in READING_FIELDS else None

    def _readings_window(self, field: str, start: float, end: float) -> dict | None:
        row = self.db.execute(
            f"SELECT AVG({field}), MIN({field}), MAX({field}), COUNT({field}) FROM readings "
            f"WHERE ts >= ? AND ts < ? AND {field} IS NOT NULL", (start, end)).fetchone()
        if not row or not row[3]:
            return None
        return {"avg": float(row[0]), "lo": float(row[1]), "hi": float(row[2]), "n": int(row[3])}

    def readings_baseline(self, field: str, days: float = 7.0, now: float | None = None) -> float | None:
        """The average over the PREVIOUS period of `days`, or None when there is
        not enough of one to be honest about. For the RAM alert's "up N points
        on last week"."""
        f = self.reading_field(field)
        if not f:
            return None
        try:
            now = now or time.time()
            span = max(0.25, float(days)) * 86400
            earlier = self._readings_window(f, now - 2 * span, now - span)
            if not earlier or earlier["n"] < 5:
                return None
            return earlier["avg"]
        except Exception:
            log.debug("no readings baseline", exc_info=True)
            return None

    def readings_trend(self, field: str = "ram_pct", days: float = 7.0,
                       now: float | None = None) -> str:
        """How it has been, and whether that is unusual.

        The comparison window is the same length immediately before, which is
        what makes the answer a conclusion rather than another reading. Without
        a baseline "memory averaged 58 percent" means nothing at all — and
        when there is none, say so rather than imply a comparison."""
        f = self.reading_field(field)
        if not f:
            return (f"I have no history for {field}. I keep the processor, memory, "
                    "free disk space and the battery.")
        label = READING_FIELDS[f]
        unit = " gigabytes" if f == "disk_free_gb" else " percent"
        try:
            now = now or time.time()
            span = max(0.25, float(days)) * 86400
            recent = self._readings_window(f, now - span, now)
        except Exception:
            log.debug("readings trend failed", exc_info=True)
            return "I have not been keeping readings, sir."
        if not recent or recent["n"] < 5:
            return f"I have not watched {label} for long enough to say, sir. Give it a few hours."
        earlier = self._readings_window(f, now - 2 * span, now - span)
        period = "today" if days <= 1 else f"over the last {int(days)} days"
        line = f"{label[0].upper()}{label[1:]} has averaged {recent['avg']:.0f}{unit} {period}"
        # "between 71 and 71" is a strange thing to say about a number that
        # has not moved. Only give the range when there is one.
        if recent["hi"] - recent["lo"] >= 2:
            line += f", between {recent['lo']:.0f} and {recent['hi']:.0f}."
        else:
            line += ", and barely moved."
        if not earlier or earlier["n"] < 5:
            return line + " I have nothing earlier to compare it against yet."
        change = recent["avg"] - earlier["avg"]
        if abs(change) < 3:
            return line + " Much the same as the period before."
        direction = "higher" if change > 0 else "lower"
        before = "yesterday" if days <= 1 else f"the {int(days)} days before"
        return line + f" That is {abs(change):.0f} points {direction} than {before}."

    def app_minutes(self, hours: float = 8.0, limit: int = 4, now: float | None = None) -> str:
        """What he has been in, most first. Samples are a minute apart, so
        counting them is counting minutes — near enough for "most of the
        afternoon" and far simpler than tracking spans across gaps and sleeps."""
        try:
            now = now or time.time()
            since = now - max(0.25, float(hours)) * 3600
            rows = self.db.execute(
                "SELECT exe, COUNT(*) FROM app_context WHERE ts >= ? AND ts <= ? "
                "GROUP BY exe ORDER BY COUNT(*) DESC", (since, now)).fetchall()
        except Exception:
            log.debug("app summary failed", exc_info=True)
            return "I have not been keeping track, sir."
        rows = [r for r in rows if r[1] >= 2 and r[0] not in ("jarvis.exe", "explorer.exe",
                                                               "lockapp.exe", "searchhost.exe")]
        if not rows:
            return "Nothing much, as far as I can tell, sir."
        parts = []
        for exe, n in rows[:limit]:
            name = _APP_NAMES.get(exe, exe[:-4] if exe.endswith(".exe") else exe)
            span = f"{n / 60:.0f} hours" if n >= 90 else f"{n} minutes"
            parts.append(f"{name} for {span}")
        line = ", ".join(parts[:-1])
        line = f"{line}, and {parts[-1]}" if len(parts) > 1 else parts[0]
        return f"Mostly {line}, sir."

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
        if not _recording():
            # A test's turn never enters his history (recording.py). It still
            # needs to be READABLE: the facts suite finds its own answer by
            # reading the transcript back. So it goes into a scratch ring in
            # memory, with ids that cannot collide with the table's, and
            # vanishes with the process.
            self._scratch_id += 1
            self._scratch.append({"id": self._scratch_id, "ts": time.time(),
                                  "role": role, "content": content})
            return
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

    def recent_transcript(self, n: int = 12, scratch: bool = False) -> list[dict]:
        # id and ts ride along: anything that needs to know WHICH turn a row
        # belongs to cannot use content alone (the same question recurs), and
        # counting rows breaks silently once the window is full.
        # Same reasoning as log_turn: a damaged transcript must cost him his
        # history, not his ability to hold a conversation. He would rather be
        # answered without context than not answered at all.
        if scratch:
            # the unrecorded turns (a test's), newest last, same shape
            return list(self._scratch)[-n:]
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
