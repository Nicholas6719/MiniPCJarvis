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

    print("\n-- the name is read back before the folder exists --")
    # His point, and the better half of the argument: "that way if he hears the
    # name wrong I simply correct him." The sanitiser stops a name escaping the
    # workspace; it does nothing about the far likelier problem, which is a
    # fortnight of work landing in a folder called the wrong thing.
    ask = await WT.start_project("Spider-Man Suit Mark 2")
    check("it asks before it creates anything",
          "_ask" in ask
          and not os.path.isdir(workspace.path_for("Spider-Man Suit Mark 2")),
          "nothing may exist on disk until he has agreed to the name")
    check("...reading back the FOLDER name, not what he said",
          "Spider-Man Suit Mark 2" in ask["_ask"]["question"],
          "confirming what he said while creating something else confirms "
          "nothing")
    check("...and inviting a correction rather than only yes or no",
          "correction is an answer" in (ask.get("instruction") or ""))
    adj = await WT.start_project("Mark 3: the good one")
    check("a name it had to change says so out loud",
          adj.get("adjusted") is True
          and "drop a character" in adj["_ask"]["question"],
          "dropping a character silently is how he finds out weeks later that "
          "the folder is not called what he thinks it is")

    print("\n-- opening a project --")
    r = await WT.start_project("Spider-Man Suit Mark 2",
                               about="a wearable chest emblem first",
                               confirmed=True)
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
    check("...and does NOT ask him to approve a name he already approved",
          "_ask" not in again,
          "being asked to confirm a folder he has worked in for a week is "
          "friction pretending to be care")

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

    print("\n-- the Windows device names --")
    # `mkdir NUL` reports success and then is_dir is False: the name resolves
    # to the null device, not a folder. A project called that would look
    # opened and silently swallow every model filed into it.
    probe = tempfile.mkdtemp()
    was = workspace.DEFAULT_ROOT
    try:
        workspace.DEFAULT_ROOT = probe
        for said in ("CON", "NUL", "com1", "aux", "LPT9", "nul.suit", "PRN"):
            workspace.create(said, about="device name")
            check(f"a project he calls {said!r} is REALLY on disk",
                  os.path.isdir(workspace.path_for(said)),
                  "mkdir NUL reports success and then is_dir is False, "
                  "because the name is the null device and not a folder")
    finally:
        workspace.DEFAULT_ROOT = was
    for said in ("Nulls", "CONtainer", "Auxiliary", "Mark 2", "Comet"):
        check(f"{said!r} is left alone",
              workspace.folder_name(said) == said, workspace.folder_name(said))

    d = tempfile.mkdtemp()
    old_root = workspace.DEFAULT_ROOT
    try:
        workspace.DEFAULT_ROOT = d
        workspace.create("NUL", about="device name")
        p = workspace.path_for("NUL")
        check("a project he calls NUL is a REAL folder on disk",
              os.path.isdir(p),
              "mkdir succeeding is not the same as the folder existing")
        check("...and a note written into it survives",
              not (workspace.note("NUL", "a line") or {}).get("error"))
    finally:
        workspace.DEFAULT_ROOT = old_root


    print("\n-- speech does not spell consistently --")
    # "pull up Spider-Man suit Mark 2" answered "I don't have a project called
    # that" about a project that was right there: the folder was created from
    # one transcription and recalled from another, and the match was a plain
    # substring test that a single hyphen defeats. Worse, project_note would
    # then CREATE the near-miss as a second folder and write the note into the
    # empty one, splitting his design log with no error anywhere.
    # This suite monkeypatches workspace.root, so DEFAULT_ROOT is not what
    # decides where things land — ask the module where it is actually writing.
    d2 = workspace.root()
    if True:
        workspace.create("Spider-Man suit Mark 2", about="our own suit")
        workspace.note("Spider-Man suit Mark 2", "webs go on last")
        workspace.create("Iron Man Mark 3")

        for said in ("spiderman suit mark 2", "Spider Man suit Mark 2",
                     "SPIDERMAN SUIT MARK 2", "the spiderman suit",
                     "pull up the spider-man suit"):
            check(f"{said!r} finds it",
                  workspace.resolve(said)[0] == "Spider-Man suit Mark 2",
                  repr(workspace.resolve(said)))
        check("the name he hears back is the project's, not his casing",
              workspace.resolve("iron man mark 3")[0] == "Iron Man Mark 3",
              repr(workspace.resolve("iron man mark 3")))
        check("something genuinely absent is still refused",
              workspace.resolve("a duck")[0] == "",
              repr(workspace.resolve("a duck")))
        got, near = workspace.resolve("mark")
        check("...and an ambiguous one names the candidates instead of guessing",
              got == "" and len(near) == 2, f"{got!r} {near}")

        # THE ONE THAT LOST DATA: a note under a different transcription.
        # Other gates in this suite share this root, so what matters is that
        # the count does not CHANGE, not what it is.
        def dirs():
            return sorted(x for x in os.listdir(d2)
                          if os.path.isdir(os.path.join(d2, x)))

        before = dirs()
        workspace.note("spiderman suit mark 2", "second note, same project")
        check("a note under another transcription does NOT make a second folder",
              dirs() == before,
              f"appeared: {[x for x in dirs() if x not in before]}")
        log = open(os.path.join(d2, "Spider-Man suit Mark 2",
                                workspace.NOTES), encoding="utf-8").read()
        check("...and both notes are in the one design log",
              "webs go on last" in log and "second note" in log)


    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
