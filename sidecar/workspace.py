"""The folder JARVIS keeps his work in, and is responsible for.

His words: *"in the movies when Tony was first creating the Mark 2 he asked
JARVIS to open up a new project and he said should I store this on your private
server or the public server... I created a folder in my documents. We give him
access to this folder entirely — he's in charge of organizing it, of maintaining
it. So when I say pull up Spider-Man suit Mark 2, he can still have the context
and understand what we were working on, because he would have all the
information he put in there — not only the models themselves but actual notes he
took away from that conversation."*

    C:\\Users\\nicho\\Documents\\J.A.R.V.I.S
        Iron Man Arc Reactor/
            project.json      what it is, when it started, what it is made of
            notes.md          what was decided and why, in the order it happened
            models/           the .stl, the .scad, every named part
            references/       the pictures the research actually used

WHY THE FOLDER AND NOT THE DATABASE. `tools/projects.py` already tracks projects
and their progress, and it stays the index — but the artefacts belong on disk, in
his own Documents, in folders he can open and read without JARVIS running. The
database has been reported corrupt twice this week (once wrongly, once as a
sandbox artefact) and neither time should a fortnight of design work have been at
risk. Every project folder carries its own `project.json`, so the index can be
rebuilt from the disk and never the other way round.

NOTES ARE THE POINT, not the models. A .stl he can find himself. What he cannot
reconstruct is why the ring ended up 76 mm, which reference was rejected and what
for, and what he said he wanted to do next — and that is exactly what is gone
when a conversation ends. `note()` is called as those things are decided.

WHAT THIS MODULE WILL NOT DO. It writes only under the root, through `safe_name`,
so a project called `../../Windows` is a folder called `windows`. It never
deletes a project — archiving moves it into `_archive/`, because "delete the
Spider-Man suit" is not a sentence that should be able to destroy a fortnight of
work on a mishearing.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
import shutil

from config import config

log = logging.getLogger("jarvis.workspace")

DEFAULT_ROOT = r"C:\Users\nicho\Documents\J.A.R.V.I.S"

# Where a project keeps things. Fixed names, because he should be able to open
# any project folder and find the same shape without being told.
MODELS = "models"
REFERENCES = "references"
NOTES = "notes.md"
META = "project.json"
ARCHIVE = "_archive"


def root() -> str:
    """The workspace folder, created if it is not there yet."""
    p = config.get("workspace", "root", default="") or DEFAULT_ROOT
    try:
        os.makedirs(p, exist_ok=True)
    except OSError:
        log.warning("could not open the workspace at %s", p, exc_info=True)
    return p


_RESERVED = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"COM{i}" for i in range(1, 10)]
    + [f"LPT{i}" for i in range(1, 10)])


def folder_name(name: str) -> str:
    """A folder he would recognise, from something he said.

    Not `safe_name` from fabrication: that produces `iron-man-arc-reactor`,
    which is right for a filename beside twenty others and wrong for a folder he
    is going to open himself. Spaces and capitals are kept; everything the
    filesystem objects to is not.
    """
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", (name or "").strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    cleaned = cleaned[:80]

    # THE DEVICE NAMES. Windows still reserves these, and NUL is the one that
    # matters: `mkdir NUL` reports success and then `is_dir` is False, because
    # the name resolves to the null device rather than to a folder. A project
    # called that would look opened and then swallow every model filed into it
    # without an error anywhere — which is the exact failure the open-project
    # label on the stage exists to prevent, arriving by a different door.
    # Checked against the stem, since CON.suit is reserved too.
    if cleaned and cleaned.split(".")[0].upper() in _RESERVED:
        cleaned += " project"
    return cleaned or "Untitled project"


def path_for(name: str) -> str:
    """Where a project lives. Always inside the root, whatever he called it."""
    return os.path.join(root(), folder_name(name))


# The words he puts in front of a project's name when he asks for it, which
# are not part of what it is called. Dropped from BOTH sides, so a project
# genuinely called "The Mark 2" still matches itself.
_FILLER = frozenset(("the", "a", "an", "my", "our", "that", "this", "then",
                     "up", "pull", "open", "please", "sir", "project", "one"))


def _key(text: str) -> str:
    """A name reduced to letters and digits, for comparing two transcriptions.

    Joined rather than compared word by word, because the whole point is that
    "Spider-Man", "Spiderman" and "Spider Man" are one name — and those do not
    tokenise alike. The filler words come out first, or "the spiderman suit"
    keys to "thespidermansuit" and stops being a substring of the project.
    """
    words = [w for w in re.split(r"[^a-z0-9]+", (text or "").lower())
             if w and w not in _FILLER]
    return "".join(words)


def resolve(said: str) -> tuple[str, list[str]]:
    """The project he means -> (its real name, or "" and the near misses).

    SPEECH DOES NOT SPELL CONSISTENTLY. The folder gets created from one
    transcription and recalled from another, and a plain substring test on the
    raw words misses over a single hyphen: "spiderman suit mark 2" is not a
    substring of "Spider-Man suit Mark 2", so "pull up the Spider-Man suit"
    answered "I don't have a project called that" about a project that was
    right there. Compared on letters and digits only, so Spider-Man, Spiderman
    and Spider Man are one name.

    Both directions, because he is as likely to say less than the folder is
    called ("the Spider-Man suit") as more ("the Spider-Man suit we started").
    """
    said = (said or "").strip()
    if not said:
        return "", []
    if exists(said):
        # THE NAME ON DISK, not the one he happened to say. Windows paths are
        # case-insensitive, so "iron man mark 3" finds the folder and would
        # otherwise be echoed back in his casing rather than the project's.
        want = folder_name(said)
        for p in projects():
            if p["name"].lower() == want.lower():
                return p["name"], []
        return want, []
    k = _key(said)
    if not k:
        return "", []
    names = [p["name"] for p in projects()]
    near = [n for n in names if k in _key(n)]
    if len(near) != 1:
        wider = [n for n in names if _key(n) and _key(n) in k]
        if len(wider) == 1:
            return wider[0], []
        near = near or wider
    if len(near) == 1:
        return near[0], []
    return "", near


def _as_existing(name: str) -> str:
    """The project this name already means, or the name unchanged.

    A NEAR MISS IS THE SAME PROJECT. `note` and `keep_model` create the folder
    when it is absent, which is right for a genuinely new project and wrong for
    another transcription of one he already has: "spiderman suit mark 2" would
    make a second folder beside "Spider-Man suit Mark 2" and put the note in
    the empty one, splitting his design log in two without an error anywhere.
    Only resolves when it is unambiguous, so this can never quietly pick
    between two real candidates.
    """
    got, _ = resolve(name)
    return got or name


def exists(name: str) -> bool:
    return os.path.isdir(path_for(name))


def create(name: str, kind: str = "", about: str = "") -> dict:
    """Open a project folder and its skeleton. Safe to call on one that exists."""
    d = path_for(name)
    try:
        for sub in ("", MODELS, REFERENCES):
            os.makedirs(os.path.join(d, sub), exist_ok=True)
    except OSError as e:
        return {"error": f"I couldn't open that folder: {e}"}

    meta_path = os.path.join(d, META)
    if not os.path.exists(meta_path):
        _write_json(meta_path, {
            "name": folder_name(name),
            "said_as": (name or "").strip(),
            "kind": kind or "",
            "about": about or "",
            "started": _now(),
            "status": "active",
        })
    notes = os.path.join(d, NOTES)
    if not os.path.exists(notes):
        _write_text(notes, f"# {folder_name(name)}\n\n"
                           f"Opened {_now()}.\n")
    return {"name": folder_name(name), "path": d, "created": True}


def meta(name: str) -> dict:
    """Everything the folder knows about itself."""
    d = path_for(name)
    got = _read_json(os.path.join(d, META))
    got.setdefault("name", folder_name(name))
    got["path"] = d
    got["models"] = _listdir(os.path.join(d, MODELS))
    got["references"] = _listdir(os.path.join(d, REFERENCES))
    got["notes"] = _read_text(os.path.join(d, NOTES))
    return got


def note(name: str, text: str, heading: str = "") -> dict:
    """Write down something decided, with the date, in the order it happened.

    Appended, never rewritten. A design log that can be edited by the thing
    writing it is a design log that can quietly disagree with what happened.
    """
    body = (text or "").strip()
    if not body:
        return {"error": "note what, sir?"}
    name = _as_existing(name)
    if not exists(name):
        create(name)
    line = f"\n## {heading.strip()}\n" if heading.strip() else "\n"
    line += f"*{_now()}* — {body}\n"
    p = os.path.join(path_for(name), NOTES)
    try:
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError as e:
        return {"error": f"I couldn't write that down: {e}"}
    return {"noted": True, "project": folder_name(name), "path": p}


def keep(name: str, src: str, kind: str = MODELS) -> dict:
    """Copy a file JARVIS made into the project, and say where it went.

    COPIED, not moved. The work folder is where a render lands and where the
    stage reads from; taking the file out from under a hologram that is
    currently up would blank it.
    """
    if not os.path.exists(src):
        return {"error": "there's no such file, sir"}
    if kind not in (MODELS, REFERENCES):
        kind = MODELS
    name = _as_existing(name)
    if not exists(name):
        create(name)
    dst_dir = os.path.join(path_for(name), kind)
    try:
        os.makedirs(dst_dir, exist_ok=True)
        dst = os.path.join(dst_dir, os.path.basename(src))
        shutil.copy2(src, dst)
    except OSError as e:
        return {"error": f"I couldn't file that away: {e}"}
    return {"kept": dst, "project": folder_name(name)}


def keep_model(name: str, stl_path: str) -> dict:
    """File a model AND everything that belongs with it.

    A part on its own is not the work. The source is what makes it editable, the
    manifest is what makes it an assembly, and the per-part files are what he
    zooms into — filing the .stl alone would keep the picture and throw away the
    thing that can be changed.
    """
    import assembly

    kept = []
    r = keep(name, stl_path)
    if r.get("error"):
        return r
    kept.append(r["kept"])

    base = stl_path[:-4] if stl_path.lower().endswith(".stl") else stl_path
    for extra in (base + ".scad", assembly.manifest_path(stl_path)):
        if os.path.exists(extra):
            got = keep(name, extra)
            if got.get("kept"):
                kept.append(got["kept"])
    for part_name, part_path in assembly.read_manifest(stl_path):
        got = keep(name, part_path)
        if got.get("kept"):
            kept.append(got["kept"])
        del part_name
    return {"kept": kept, "count": len(kept), "project": folder_name(name)}


def projects() -> list[dict]:
    """Every project on disk, newest first. The folder is the source of truth."""
    out = []
    r = root()
    try:
        names = os.listdir(r)
    except OSError:
        return out
    for n in names:
        d = os.path.join(r, n)
        if not os.path.isdir(d) or n == ARCHIVE:
            continue
        got = _read_json(os.path.join(d, META))
        out.append({
            "name": got.get("name", n),
            "said_as": got.get("said_as", n),
            "started": got.get("started", ""),
            "status": got.get("status", "active"),
            "models": len(_listdir(os.path.join(d, MODELS))),
            "path": d,
        })
    out.sort(key=lambda p: p.get("started", ""), reverse=True)
    return out


def archive(name: str) -> dict:
    """Put a project away. NEVER deletes.

    "Delete the Spider-Man suit" is not a sentence that should be able to
    destroy a fortnight of work on a mishearing, so there is no code here that
    can.
    """
    name = _as_existing(name)
    if not exists(name):
        return {"error": f"I don't have a project called {name}, sir"}
    dst_dir = os.path.join(root(), ARCHIVE)
    try:
        os.makedirs(dst_dir, exist_ok=True)
        dst = os.path.join(dst_dir, folder_name(name))
        if os.path.exists(dst):
            dst = f"{dst} ({_now().replace(':', '-')})"
        shutil.move(path_for(name), dst)
    except OSError as e:
        return {"error": f"I couldn't archive that: {e}"}
    return {"archived": dst, "name": folder_name(name)}


# ---------------------------------------------------------------- small stuff
def _now() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M")


def _listdir(p: str) -> list[str]:
    try:
        return sorted(f for f in os.listdir(p)
                      if os.path.isfile(os.path.join(p, f)))
    except OSError:
        return []


def _read_json(p: str) -> dict:
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh) or {}
    except (OSError, ValueError):
        return {}


def _write_json(p: str, data: dict) -> None:
    try:
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1)
    except OSError:
        log.warning("could not write %s", p, exc_info=True)


def _read_text(p: str) -> str:
    try:
        with open(p, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def _write_text(p: str, text: str) -> None:
    try:
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)
    except OSError:
        log.warning("could not write %s", p, exc_info=True)
