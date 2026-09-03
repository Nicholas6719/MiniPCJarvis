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
    set_active(said)

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
    try:
        from events import bus
        await bus.emit("workspace", action="open", project=r["name"],
                       path=r["path"], projects=workspace.projects())
    except Exception:
        log.debug("could not announce the project to the HUD", exc_info=True)

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
    if not workspace.exists(said):
        near = [p["name"] for p in workspace.projects()
                if said.lower() in p["name"].lower()]
        if len(near) == 1:
            said = near[0]
        elif near:
            return {"error": f"I have {len(near)} that could be that, sir: "
                             + ", ".join(near[:4]),
                    "candidates": near}
        else:
            return {"error": f"I don't have a project called {said}, sir"}

    m = workspace.meta(said)
    set_active(said)
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


def register_all() -> None:
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
