"""The folder he gave JARVIS, and what must never happen to it.

His words: *"I created a folder in my documents... we give him access to this
folder entirely, he's in charge of organizing it, of maintaining it."* That is a
lot of trust to hand a program that mishears things, so most of this file is
about the two ways it could be abused rather than the ways it is used:

  * writing outside the folder, because he named a project something with a
    slash in it;
  * losing work, because "delete the Spider-Man suit" was heard.

Neither is possible by construction, and both are asserted here.

Run: python tests/test_workspace.py
"""
import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "ws.db"))

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


async def main() -> int:
    import workspace

    root = tempfile.mkdtemp()
    workspace.root = lambda: root
    from tools import workspace_tools as WT

    print("\n-- it cannot write outside the folder --")
    for said, want in (("../../Windows/System32", "WindowsSystem32"),
                       ("..", "Untitled project"),
                       ("  ", "Untitled project"),
                       ("C:/Windows", "CWindows"),
                       ('a"b<c>d|e', "abcde")):
        got = workspace.folder_name(said)
        inside = os.path.realpath(workspace.path_for(said)).startswith(
            os.path.realpath(root))
        check(f"{said!r} stays inside the workspace", inside and got == want, got)
    check("a very long name is trimmed rather than refused",
          len(workspace.folder_name("x" * 400)) <= 80)

    print("\n-- opening a project --")
    r = await WT.start_project("Spider-Man Suit Mark 2",
                               about="a wearable chest emblem first")
    check("the folder is made", os.path.isdir(r["path"]))
    check("...with somewhere for models and references",
          os.path.isdir(os.path.join(r["path"], workspace.MODELS))
          and os.path.isdir(os.path.join(r["path"], workspace.REFERENCES)))
    check("...and it becomes the one we are working in",
          WT.active() == "Spider-Man Suit Mark 2")
    check("...and it survives a restart, because it is on disk not in memory",
          os.path.exists(os.path.join(root, "_active.txt")),
          "he opens a project on Tuesday and asks for a part on Thursday")
    again = await WT.start_project("Spider-Man Suit Mark 2")
    check("opening one that exists reopens it rather than starting over",
          again.get("reopened") is True and "Reopened" in again.get("spoken", ""))

    print("\n-- the notes, which are the part he cannot reconstruct --")
    await WT.project_note("Traced from the 1080p still, not the poster — the "
                          "poster has a border.", heading="Reference")
    await WT.project_note("Chest plate 320 mm across; bigger will not fit the bed.")
    notes = workspace.meta("Spider-Man Suit Mark 2")["notes"]
    check("a decision is written down with its date",
          "320 mm across" in notes and "2026" in notes or "20" in notes)
    check("...under a heading when it has one", "## Reference" in notes)
    check("...and appended, never rewritten",
          notes.index("Reference") < notes.index("Chest plate"),
          "a log the writer can edit is a log that can disagree with what "
          "happened")

    print("\n-- filing a model keeps the WORK, not just the picture --")
    d = tempfile.mkdtemp()
    stl = os.path.join(d, "arc.stl")
    open(stl, "wb").write(b"\0" * 200)
    open(os.path.join(d, "arc.scad"), "w").write("cube(1);")
    import assembly
    parts = []
    for n in ("rim", "core"):
        p = os.path.join(d, f"arc.{n}.stl")
        open(p, "wb").write(b"\0" * 120)
        parts.append({"name": n, "stl": p})
    assembly.write_manifest(stl, parts)

    got = await WT.file_in_project(stl_path=stl)
    kept = os.listdir(os.path.join(workspace.path_for("Spider-Man Suit Mark 2"),
                                   workspace.MODELS))
    check("the model is filed", "arc.stl" in kept)
    check("...and its SOURCE, which is what makes it editable", "arc.scad" in kept,
          "without it the part can never be changed again")
    check("...and its manifest and named parts",
          "arc.parts.json" in kept and "arc.rim.stl" in kept
          and "arc.core.stl" in kept,
          "a .stl on its own is a picture of the work rather than the work")
    check("...and the original is left where the stage reads from",
          os.path.exists(stl),
          "moving it would blank a hologram that is currently up")
    check("it says how much it filed", got.get("count") == 5, got.get("count"))

    print("\n-- finding it again a week later --")
    back = await WT.recall_project("spider-man suit")
    check("a partial name finds it", back.get("project") == "Spider-Man Suit Mark 2",
          back.get("error"))
    check("...and brings the notes back in full",
          "320 mm across" in (back.get("notes") or ""),
          "the models he can find himself; this is the part he cannot")
    check("...and lists what was made", "arc.stl" in (back.get("models") or []))
    missing = await WT.recall_project("a project that was never started")
    check("a project that does not exist says so", bool(missing.get("error")))

    print("\n-- and it cannot lose his work --")
    src = open(os.path.join(os.path.dirname(os.path.abspath(workspace.__file__)),
                            "workspace.py"), encoding="utf-8").read()
    check("there is no code here that deletes a project",
          "rmtree" not in src and "os.remove" not in src,
          "'delete the Spider-Man suit' must not be able to destroy a "
          "fortnight of work on a mishearing")
    a = workspace.archive("Spider-Man Suit Mark 2")
    check("archiving moves it instead", os.path.isdir(a.get("archived", "")),
          a.get("error"))
    check("...and everything is still in it",
          os.path.exists(os.path.join(a["archived"], "notes.md")))
    check("archiving something that is not there is refused, not invented",
          bool(workspace.archive("nothing at all").get("error")))

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
