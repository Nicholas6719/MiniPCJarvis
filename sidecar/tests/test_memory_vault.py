"""The memories as markdown files: the files are the truth, the table is the index.

Delete a file and he forgets it. Edit a file and he believes the new version.
Add a file and he learns it. The README that says so is never itself a memory.
And looking costs nothing when nothing changed: the fingerprint is names,
mtimes and sizes, and no file is opened unless one of them moved.

Hermetic: the gate database and a temp folder. The real vault
(Documents\\JARVIS\\Memory) is never touched — under JARVIS_DB the folder is
beside the gate database, and test_isolation.py asserts that too.
Run: python tests/test_memory_vault.py
"""
import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "vault.db"))
os.environ["JARVIS_VAULT"] = os.path.join(tempfile.mkdtemp(), "Memory")

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


def _files(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.glob("*.md") if p.name.lower() != "readme.md")


def main() -> int:
    from memory import vault as V
    from memory.store import memory as M

    folder = M.vault
    real = Path.home() / "Documents" / "JARVIS" / "Memory"
    check("the vault under test is not his real one", folder != real and not str(folder).startswith(str(real)), folder)
    check("...and is not in %APPDATA% either", "AppData\\Roaming" not in str(folder), folder)

    print("\n-- learning writes a file --")
    mid = asyncio.run(M.remember("The kettle is in the garage.", category="fact"))
    check("README is written with the first memory", (folder / "README.md").exists())
    names = _files(folder)
    check("one file, named after the words", names == ["the-kettle-is-in-the-garage.md"], names)
    parsed = V.parse(folder / names[0])
    check("...carrying its id, category, created, source, pinned, confidence",
          parsed and parsed["id"] == mid and parsed["category"] == "fact" and parsed["created"]
          and parsed["source"] == "conversation" and parsed["pinned"] is False and parsed["confidence"] == "medium", parsed)

    print("\n-- the README is never a memory --")
    check("scan skips it", all(f["id"] != None or "JARVIS knows" not in f["content"] for f in V.scan(folder)))
    check("...and the table does not hold it",
          not any("Every file in this folder" in m["content"] for m in M.list_all()))

    print("\n-- edit a file and he believes the new version; pinned survives --")
    M.set_pinned(mid, True)
    path = folder / names[0]
    text = path.read_text(encoding="utf-8")
    check("pinning rewrote the frontmatter", "pinned: true" in text, text)
    time.sleep(0.02)
    path.write_text(text.replace("The kettle is in the garage.", "The kettle is on the workbench."), encoding="utf-8")
    rows = M.list_all()
    mine = [m for m in rows if m["id"] == mid]
    check("the edit is believed on the next look", mine and mine[0]["content"] == "The kettle is on the workbench.", mine)
    check("...with the same id, so it is a correction and not a new memory", len([m for m in rows if "kettle" in m["content"]]) == 1)
    check("...and still pinned", mine and mine[0]["pinned"] is True, mine)
    check("...and the edit is marked as his", mine and mine[0]["source"] == "edited", mine)
    hits = asyncio.run(M.search("where is the kettle", top_k=1, min_score=0.3))
    check("recall finds the corrected text, so it was re-embedded", hits and "workbench" in hits[0]["content"], hits)

    print("\n-- unchanged: nothing is opened --")
    opened = []
    real_parse = V.parse

    def counting(p):
        opened.append(p)
        return real_parse(p)

    V.parse = counting
    try:
        M.list_all()
        M.list_pinned()
        asyncio.run(M.search("kettle", top_k=1))
    finally:
        V.parse = real_parse
    check("three reads with no change open no file", opened == [], opened)

    print("\n-- add a file and he learns it --")
    (folder / "new-note.md").write_text("# a heading he wrote\n\nHis bike lock code is in the notes app.\n", encoding="utf-8")
    rows = M.list_all()
    added = [m for m in rows if "bike lock" in m["content"]]
    check("a plain markdown file becomes a memory (first line after the heading)", len(added) == 1, rows)
    stamped = V.parse(folder / "new-note.md")
    check("...and the file is stamped with its id", stamped and stamped["id"] == added[0]["id"], stamped)
    check("...and the folder's name for it is kept", (folder / "new-note.md").exists())

    print("\n-- delete a file and he forgets it --")
    (folder / "new-note.md").unlink()
    rows = M.list_all()
    check("the row is gone", not any("bike lock" in m["content"] for m in rows))
    check("...and the others are untouched", any(m["id"] == mid for m in rows))

    print("\n-- the store's own changes keep the files in step --")
    asyncio.run(M.update_content(mid, "The kettle is by the sink."))
    check("update_content rewrites the file", "by the sink" in path.read_text(encoding="utf-8"))
    M.set_pinned(mid, False)
    check("unpinning rewrites it", "pinned: false" in path.read_text(encoding="utf-8"))
    M.forget(mid)
    check("forgetting deletes the file", not path.exists())
    check("...and the row", not any(m["id"] == mid for m in M.list_all()))

    print("\n-- a slug collision gets -2 --")
    scratch = Path(tempfile.mkdtemp())
    p1 = V.write(scratch, "Dentist on Tuesday!", mid=101)
    p2 = V.write(scratch, "Dentist, on Tuesday", mid=102)
    p1b = V.write(scratch, "Dentist on Tuesday!", mid=101)
    check("two different memories never share a file",
          p1.name == "dentist-on-tuesday.md" and p2.name == "dentist-on-tuesday-2.md", (p1.name, p2.name))
    check("...and the same memory keeps its file", p1b == p1, p1b)

    print("\n-- first run: the table is moved out into files --")
    fresh = Path(tempfile.mkdtemp()) / "Memory2"
    M.db.execute("INSERT INTO memories (ts, category, content, source, confidence, pinned) VALUES (?,?,?,?,?,?)",
                 (time.time(), "preference", "He takes his coffee black.", "conversation", "high", 1))
    M.db.commit()
    M.vault, M._vault_seen = fresh, None
    rows = M.list_all()
    check("a folder that never existed gets every row as a file",
          (fresh / "README.md").exists() and "he-takes-his-coffee-black.md" in _files(fresh), _files(fresh))
    check("...and nothing was forgotten in the move", any("coffee" in m["content"] for m in rows))
    check("...pinned included", "pinned: true" in (fresh / "he-takes-his-coffee-black.md").read_text(encoding="utf-8"))

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
