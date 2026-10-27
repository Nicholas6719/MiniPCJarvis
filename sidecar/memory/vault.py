"""What he remembers, as markdown files you can read and edit.

The memories lived only inside SQLite. That worked, and it was completely
opaque: no way to see what JARVIS believed about you without a database
client, no way to correct a memory he had misheard, no way to add one without
saying it out loud.

So each memory is a markdown file now: YAML frontmatter on top, the memory
itself as the body — which happens to be exactly what an Obsidian vault is.
Point Obsidian at the folder and you get search, links and a graph; do not,
and you still have a folder of readable text.

SQLite has not gone anywhere. It is the INDEX now rather than the truth: the
embeddings that make recall semantic live there, and rebuilding them from the
files is cheap. The files win every disagreement — deleting one is how you make
him forget, editing one is how you correct him, adding one is how he learns.

The folder lives in his Documents, deliberately OUT of %APPDATA%: these are his
notes, not the application's state. Under a gate (JARVIS_DB set) the folder is
beside the gate database instead, so a test can never touch the real one.
"""
from __future__ import annotations

import logging
import os
import re
import time
from datetime import date
from pathlib import Path

log = logging.getLogger("jarvis.vault")

_SLUG = re.compile(r"[^a-z0-9]+")
_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.S)

README = """# JARVIS memory

Every file in this folder is one thing JARVIS knows about you.

- **Delete a file** and he forgets it.
- **Edit a file** and he believes the new version.
- **Add a file** -- any markdown with a line of text in it -- and he learns it.

He re-reads this folder whenever he looks something up, so changes take effect
without restarting him. The lines between the `---` marks at the top are his
bookkeeping (which memory it is, when he learned it, whether it is pinned);
leave them alone and edit the text underneath.

This is an ordinary folder of markdown. Opening it as an Obsidian vault gives
you search, links and a graph across it, but nothing here needs Obsidian, and
nothing here is in a format only Obsidian can read. This README is never read
as a memory.
"""


def default_folder() -> Path:
    """Where the files live. Env `JARVIS_VAULT` first; under a gate (`JARVIS_DB`
    set) a folder beside that database, so no test can reach his real notes;
    otherwise config `memory.vault_path`, default Documents\\JARVIS\\Memory."""
    env = os.environ.get("JARVIS_VAULT")
    if env:
        return Path(env)
    gate = os.environ.get("JARVIS_DB")
    if gate:
        return Path(gate).parent / "Memory"
    try:
        from config import config
        cfg = config.get("memory", "vault_path", default=None)
        if cfg:
            return Path(str(cfg))
    except Exception:
        pass
    return Path.home() / "Documents" / "JARVIS" / "Memory"


def slugify(text: str, limit: int = 60) -> str:
    out = _SLUG.sub("-", (text or "").lower()).strip("-")
    return out[:limit].rstrip("-") or "memory"


def _bool(v: str) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def parse(path: Path) -> dict | None:
    """Read one memory file. None if there is nothing usable in it."""
    try:
        raw = path.read_text(encoding="utf-8")
    except Exception:
        log.debug("could not read %s", path, exc_info=True)
        return None
    meta: dict[str, str] = {}
    body = raw
    m = _FRONTMATTER.match(raw)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                key, _, value = line.partition(":")
                meta[key.strip().lower()] = value.strip().strip('"')
        body = m.group(2)
    # The memory is the first non-empty, non-heading line. Anything else in
    # the file is his own notes, and they are none of our business.
    content = ""
    for line in body.splitlines():
        line = line.strip().lstrip("-*").strip()
        if line and not line.startswith("#"):
            content = line
            break
    if not content:
        return None
    try:
        mid = int(meta.get("id", "") or 0) or None
    except ValueError:
        mid = None
    try:
        st = path.stat()
        mtime = st.st_mtime
    except OSError:
        mtime = 0.0
    return {
        "id": mid,
        "content": content,
        "category": meta.get("category") or "fact",
        "created": meta.get("created", ""),
        "source": meta.get("source") or "vault",
        "pinned": _bool(meta.get("pinned", "false")),
        "confidence": meta.get("confidence") or "medium",
        "path": str(path),
        "mtime": mtime,
    }


def _render(content: str, *, mid: int | None, category: str, created: str,
            source: str, pinned: bool, confidence: str) -> str:
    return ("---\n"
            f"id: {mid if mid is not None else ''}\n"
            f"category: {category}\n"
            f"created: {created}\n"
            f"source: {source}\n"
            f"pinned: {'true' if pinned else 'false'}\n"
            f"confidence: {confidence}\n"
            "---\n\n"
            f"{content}\n")


def ensure_readme(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    readme = folder / "README.md"
    if not readme.exists():
        readme.write_text(README, encoding="utf-8")


def write(folder: Path, content: str, *, mid: int | None = None, category: str = "fact",
          created: str = "", source: str = "conversation", pinned: bool = False,
          confidence: str = "medium", path: Path | None = None) -> Path:
    """Save one memory. Returns the file written. `path` rewrites an existing
    file in place; otherwise the name is a slug of the text, with -2, -3 on a
    collision with a DIFFERENT memory."""
    ensure_readme(folder)
    if path is None:
        stem = slugify(content)
        path = folder / f"{stem}.md"
        n = 2
        while path.exists():
            have = parse(path) or {}
            if have.get("id") == mid and mid is not None:
                break
            if have.get("content") == content and mid is None:
                break
            path = folder / f"{stem}-{n}.md"
            n += 1
    created = created or date.today().isoformat()
    text = _render(content, mid=mid, category=category, created=created,
                   source=source, pinned=pinned, confidence=confidence)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    return path


def scan(folder: Path) -> list[dict]:
    """Every memory currently in the vault. README.md is skipped by name: it
    explains the folder rather than being a thing he knows, and without this
    he would confidently recall that every file in the folder is one thing
    JARVIS knows about you."""
    if not folder.is_dir():
        return []
    out = []
    for path in sorted(folder.glob("*.md")):
        if path.name.lower() == "readme.md":
            continue
        fact = parse(path)
        if fact:
            out.append(fact)
    return out


def fingerprint(folder: Path) -> tuple:
    """Cheap signature of the folder, to notice edits made outside JARVIS.

    Names, modification times in NANOSECONDS and sizes — no file is opened.
    This runs before every recall, so it has to cost nothing when nothing has
    changed. Whole seconds were not enough: a file edited in the same second
    as the last scan looked untouched, and a correction made straight after a
    question is exactly when corrections get made.
    """
    if not folder.is_dir():
        return ()
    try:
        out = []
        for p in folder.glob("*.md"):
            if p.name.lower() == "readme.md":
                continue
            st = p.stat()
            out.append((p.name, st.st_mtime_ns, st.st_size))
        return tuple(sorted(out))
    except OSError:
        return (time.time(),)     # unreadable: force a re-sync
