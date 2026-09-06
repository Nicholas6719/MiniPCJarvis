"""Opening a project, remembering it, and finding it again a week later.

His words: *"if I say 'pull up Spider-Man suit Mark 2', he can still have the
context and understand what we were working on, because he would have all the
information he put in there — not only the models themselves but actual notes he
took away from that conversation."*

THE NOTES ARE THE PART THAT DOES NOT SURVIVE OTHERWISE. A model is a file; he
can find a file. What disappears when a conversation ends is why the ring ended
up 76 mm, which reference was rejected and what for, and what he said he wanted
to do next — and that is exactly what `recall_project` has to be able to hand
back.

TWO PLACES, ON PURPOSE. `tools/projects.py` keeps the index in the database —
what exists, how far along, when it last moved. This keeps the WORK, in his own
Documents, in folders he can open without JARVIS running at all. The index can
be rebuilt from the folders; the folders cannot be rebuilt from the index.

THE ACTIVE PROJECT IS WHERE THINGS LAND. Once one is open, a model that gets
made is filed into it — with its source, its manifest and its named parts,
because a .stl on its own is a picture of the work rather than the work.
"""
from __future__ import annotations

import logging
import os

from tools.registry import Risk, Tool, registry

log = logging.getLogger("jarvis.tools.workspace")

# Which project the work belongs to right now. Kept on disk as well as in
# memory: he opens a project on Tuesday and asks for a part on Thursday, and a
# restart in between should not quietly start filing things nowhere.
_ACTIVE = "_active.txt"


def active() -> str:
    import workspace
    p = os.path.join(workspace.root(), _ACTIVE)
    try:
        with open(p, encoding="utf-8") as fh:
            name = fh.read().strip()
        return name if name and workspace.exists(name) else ""
    except OSError:
        return ""


def set_active(name: str) -> None:
    import workspace
    try:
        with open(os.path.join(workspace.root(), _ACTIVE), "w",
                  encoding="utf-8") as fh:
            fh.write((name or "").strip())
    except OSError:
        log.warning("could not record the active project", exc_info=True)


async def _announce(action: str, project: str, path: str = "") -> None:
    """Tell the HUD which project is open. Only `start_project` used to say
    so: "pull up the suit" a week later left the chip on the last thing he
    STARTED, and closing one left it lit (2026-09-06)."""
    import workspace
    try:
        from events import bus
        await bus.emit("workspace", action=action, project=project or None,
                       path=path, projects=workspace.projects())
    except Exception:
        log.debug("could not announce the project to the HUD", exc_info=True)


async def start_project(name: str = "", about: str = "",
                        confirmed: bool = False) -> dict:
    """Open a project folder, once he has agreed to what it will be called."""
    import workspace

    said = (name or "").strip()
    if not said:
        return {"error": "what should I call it, sir?"}
    already = workspace.exists(said)
    folder = workspace.folder_name(said)

    # THE NAME IS READ BACK BEFORE THE FOLDER EXISTS. Not for safety — the
    # sanitiser handles that — but because "Spider-Man Suit Mark 2" is easily
    # heard as something else, and this is the one moment where correcting it
    # costs nothing. Reopening one he already has does not ask: he approved
    # that name when it was made.
    if not confirmed and not already:
        changed = folder.lower() != said.lower()
        q = f"I'll call it {folder}"
        if changed:
            # Said out loud precisely because it differs from what he said.
            q += f" — I had to drop a character or two from \"{said}\""
        q += ", sir. Shall I open it?"
        return {"_ask": {
            "subject": folder,
            "question": q,
            "tool": "start_project",
            "args": {"name": said, "about": about, "confirmed": True},
        },
            "proposed": folder, "said_as": said, "adjusted": changed,
            "instruction": ("Read the FOLDER NAME back to him and let him "
                            "correct it. If he answers with a different name "
                            "rather than yes or no, call start_project again "
                            "with that name — a correction is an answer, not a "
                            "refusal.")}

    r = workspace.create(said, about=about)
    if r.get("error"):
        return r
    # The FOLDER name, not what he said: the index below is filed under the
    # folder name, and "Mark 3: the good one" made two rows otherwise.
    set_active(r["name"])

    # The index too, so "how's the suit going" and the completion estimate have
    # something to work from. Creating it here rather than on first progress
    # means a project he opened and has not touched yet still exists.
    try:
        from tools.projects import log_progress
        await log_progress(r["name"], note=f"opened{': ' + about if about else ''}")
    except Exception:
        log.debug("could not index the new project", exc_info=True)

    if about:
        workspace.note(said, about, heading="What we are making")

    # ON SCREEN, not just spoken. His words: "he can show it to me inside of his
    # OS like he's supposed to" — a folder he has agreed to should be visible
    # afterwards, the same way the stage opens when a model is ready.
    await _announce("open", r["name"], r["path"])

    return {"project": r["name"], "path": r["path"], "reopened": already,
            "spoken": (f"Reopened {r['name']}, sir." if already
                       else f"Project open, sir — {r['name']}. "
                            f"Everything we make goes in there."),
            "instruction": "Say it is open and that work will be filed there. "
                           "Do not read out the path unless he asks."}


async def project_note(text: str = "", project: str = "",
                       heading: str = "") -> dict:
    """Write something down in the project's log — a decision, a finding, a why."""
    import workspace

    name = (project or "").strip() or active()
    if not name:
        return {"error": "no project is open, sir — shall I start one?"}
    r = workspace.note(name, text, heading=heading)
    if r.get("error"):
        return r
    try:
        from tools.projects import log_progress
        await log_progress(name, note=(text or "")[:200])
    except Exception:
        log.debug("could not index that note", exc_info=True)
    return {**r, "spoken": "Noted, sir."}


async def recall_project(name: str = "") -> dict:
    """Everything we know about a project: what was made, and what was decided.

    This is the answer to "pull up the Spider-Man suit". The models are listed
    because they are what he will want on the stage; the NOTES are returned in
    full because they are the part he cannot reconstruct.
    """
    import workspace

    said = (name or "").strip() or active()
    if not said:
        return {"error": "which project, sir?"}
    # ONE resolver, tolerant of how speech spells things — workspace.resolve.
    got, near = workspace.resolve(said)
    if got:
        said = got
    else:
        if near:
            return {"error": f"I have {len(near)} that could be that, sir: "
                             + ", ".join(near[:4]),
                    "candidates": near}
        return {"error": f"I don't have a project called {said}, sir"}

    m = workspace.meta(said)
    set_active(m["name"])
    await _announce("open", m["name"], m.get("path", ""))
    models = [f for f in m["models"] if f.lower().endswith((".stl", ".obj"))]
    return {
        "project": m["name"], "started": m.get("started", ""),
        "about": m.get("about", ""),
        "models": models, "model_count": len(models),
        "references": len(m["references"]),
        "notes": m["notes"],
        "spoken": (f"{m['name']}, sir — {len(models)} model"
                   f"{'s' if len(models) != 1 else ''} and the notes we took."),
        "instruction": ("The notes are the context: read them and answer from "
                        "them. Remind him where we left off and what was "
                        "decided, in his own terms, briefly."),
    }


async def file_in_project(stl_path: str = "", project: str = "") -> dict:
    """Put a model and everything that belongs with it into the project."""
    import workspace

    name = (project or "").strip() or active()
    if not name:
        return {"error": "no project is open, sir"}
    if not stl_path:
        from tools.holo_tools import current
        stl_path = (current() or {}).get("path", "")
    if not stl_path or not os.path.exists(stl_path):
        return {"error": "which model, sir?"}
    r = workspace.keep_model(name, stl_path)
    if r.get("error"):
        return r
    return {**r, "spoken": f"Filed under {r['project']}, sir."}


async def list_workspace() -> dict:
    """What is in the workspace, newest first."""
    import workspace
    got = workspace.projects()
    if not got:
        return {"projects": [], "spoken": "Nothing in the workspace yet, sir."}
    return {"projects": got, "active": active(),
            "spoken": (f"{len(got)} project{'s' if len(got) != 1 else ''}, sir — "
                       + ", ".join(p["name"] for p in got[:4]) + ".")}


async def close_project(name: str = "", archive: bool = False) -> dict:
    """Put the project down (or away). Nothing is ever deleted.

    "Close the project file" clears the active pointer so new work stops
    being filed there; "archive the arc reactor" moves the folder into
    `_archive/`, from where `workspace.resolve` no longer finds it. The
    folder and everything in it survive both.
    """
    import workspace

    said = (name or "").strip() or active()
    if not said:
        return {"error": "nothing is open, sir"}
    got, near = workspace.resolve(said)
    if not got:
        if near:
            return {"error": f"I have {len(near)} that could be that, sir: "
                             + ", ".join(near[:4]), "candidates": near}
        return {"error": f"I don't have a project called {said}, sir"}
    was_active = active().lower() == got.lower()
    if archive:
        r = workspace.archive(got)
        if r.get("error"):
            return r
    if was_active:
        set_active("")
        await _announce("close", "", "")
    return {"project": got, "archived": bool(archive), "was_active": was_active,
            "spoken": (f"{got} is archived, sir — it's in the archive folder if "
                       f"you want it back." if archive else
                       f"{got} is closed, sir. New work won't be filed there.")}


async def project_status(name: str = "") -> dict:
    """How is it going: what exists, when it last moved, and the last note.

    The completion estimate in `tools/projects.py` wants percentages nobody
    ever says out loud; this answers from what is actually in the folder.
    """
    import datetime
    import workspace

    said = (name or "").strip() or active()
    if not said:
        return {"error": "which project, sir? Nothing is open."}
    got, near = workspace.resolve(said)
    if not got:
        if near:
            return {"error": f"I have {len(near)} that could be that, sir: "
                             + ", ".join(near[:4]), "candidates": near}
        return {"error": f"I don't have a project called {said}, sir"}
    m = workspace.meta(got)
    models = [f for f in m["models"] if f.lower().endswith((".stl", ".obj"))]
    notes = (m.get("notes") or "").strip()
    # the last dated entry in the log, if any
    last_line = ""
    for line in reversed(notes.splitlines()):
        line = line.strip()
        if line and not line.startswith("#"):
            last_line = line
            break
    started = m.get("started", "")
    days = ""
    try:
        d0 = datetime.datetime.fromisoformat(started[:19])
        n = (datetime.datetime.now() - d0).days
        days = "today" if n == 0 else ("yesterday" if n == 1 else f"{n} days ago")
    except (TypeError, ValueError):
        pass
    parts = [f"{len(models)} model{'s' if len(models) != 1 else ''}"]
    if m.get("references"):
        parts.append(f"{len(m['references'])} reference"
                     f"{'s' if len(m['references']) != 1 else ''}")
    spoken = f"{m['name']}, sir — started {days + ', ' if days else ''}{', '.join(parts)}."
    if last_line:
        spoken += f" The last note says: {last_line[:160]}"
    return {"project": m["name"], "started": started, "models": models,
            "model_count": len(models), "references": len(m.get("references") or []),
            "last_note": last_line, "active": active().lower() == m["name"].lower(),
            "spoken": spoken,
            "instruction": "Answer from this; do not invent progress percentages."}


def register_all() -> None:
    registry.register(Tool(
        name="close_project",
        description="Close the open project (new work stops being filed "
                    "there), or archive a project by name. Never deletes.",
        parameters={"type": "object", "properties": {
            "name": {"type": "string"},
            "archive": {"type": "boolean", "description": "move it to the "
                                                          "archive folder"}}},
        risk=Risk.LOW, handler=close_project, timeout=30))

    registry.register(Tool(
        name="project_status",
        description="How a project is going: what has been made, when it "
                    "started, and the last note. Use for 'how's the suit "
                    "going', 'where are we with the arc reactor'.",
        parameters={"type": "object", "properties": {
            "name": {"type": "string"}}},
        risk=Risk.SAFE, handler=project_status, timeout=30))

    registry.register(Tool(
        name="start_project",
        description="Open a new project folder for a piece of work he is "
                    "starting — a suit, a reactor, anything with more than one "
                    "session in it. Everything made afterwards is filed there. "
                    "It reads the folder name back and waits, so he can correct "
                    "a mishearing before the folder exists; if he answers with "
                    "a different name, call it again with that name.",
        parameters={"type": "object", "properties": {
            "name": {"type": "string", "description": "what he calls it, e.g. "
                                                      "'Spider-Man Suit Mark 2'"},
            "about": {"type": "string", "description": "what it is, in a line"},
            "confirmed": {"type": "boolean", "description": "he has agreed to "
                                                            "the folder name"}},
            "required": ["name"]},
        # LOW: it makes folders in his Documents and nothing else.
        risk=Risk.LOW, handler=start_project, timeout=30))

    registry.register(Tool(
        name="project_note",
        description="Write a decision or a finding into the open project's log. "
                    "Use it when something is settled — a dimension chosen, a "
                    "reference rejected, what he wants to do next — so it is "
                    "still there next week.",
        parameters={"type": "object", "properties": {
            "text": {"type": "string"},
            "project": {"type": "string"},
            "heading": {"type": "string"}},
            "required": ["text"]},
        risk=Risk.LOW, handler=project_note, timeout=30))

    registry.register(Tool(
        name="recall_project",
        description="Bring back everything about a project — the models made "
                    "and the notes taken. Use when he names one: 'pull up the "
                    "Spider-Man suit', 'where were we on the arc reactor'.",
        parameters={"type": "object", "properties": {
            "name": {"type": "string"}}},
        risk=Risk.SAFE, handler=recall_project, timeout=30))

    registry.register(Tool(
        name="file_in_project",
        description="Put a model, its source and its named parts into the open "
                    "project folder.",
        parameters={"type": "object", "properties": {
            "stl_path": {"type": "string"},
            "project": {"type": "string"}}},
        risk=Risk.LOW, handler=file_in_project, timeout=60))

    registry.register(Tool(
        name="list_workspace",
        description="What projects exist in the workspace.",
        parameters={"type": "object", "properties": {}},
        risk=Risk.SAFE, handler=list_workspace, timeout=20))
