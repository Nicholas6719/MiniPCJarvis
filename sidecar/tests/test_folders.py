"""Where his folders ARE, asked of Windows rather than guessed.

`Path.home() / "Desktop"` exists on this PC and is the wrong folder: OneDrive
redirects the Desktop, so a file "saved to the Desktop" under the naive path
succeeds and is never seen, and "read desktop/notes.txt" looks where he never
puts anything. folders.py asks SHGetKnownFolderPath; config.py's defaults and
builtin.read_file's roots now come from it; a config.json entry still wins.

Hermetic: reads nothing but the shell's registration, writes only a temp
folder, no network. Run: python tests/test_folders.py
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "folders.db"))

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


def main() -> int:
    import folders
    from config import DEFAULTS, config

    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(here, "folders.py"), encoding="utf-8").read()

    print("\n-- it asks Windows, and it asks nobody else --")
    check("SHGetKnownFolderPath is the lookup", "SHGetKnownFolderPath" in src)
    for banned in ("httpx", "requests", "socket", "urllib", "subprocess"):
        check(f"  ...and never imports {banned}", f"import {banned}" not in src
              and f"from {banned}" not in src)
    check("...nor config (config imports IT; a cycle would be a crash at import)",
          "import config" not in src and "from config" not in src)

    print("\n-- the Desktop is the redirected one --")
    desktop = folders.known_folder("desktop")
    onedrive = Path.home() / "OneDrive" / "Desktop"
    check("the resolved Desktop exists", desktop.exists(), str(desktop))
    if onedrive.exists():
        # This machine: OneDrive owns the Desktop. The naive guess is a
        # different, stale folder that also exists - which is the whole bug.
        check("...and it is the OneDrive Desktop, not the stale local one",
              desktop.resolve() == onedrive.resolve(), f"{desktop} != {onedrive}")
    else:
        print("  skip  no OneDrive Desktop on this machine; the redirect cannot be shown here")
    check("save_folder('desktop') is that same redirected Desktop",
          folders.save_folder("desktop") == desktop)
    # Documents is the opposite case: Windows may register it under OneDrive
    # too (it does here), but he keeps his files - and his Coding_Projects -
    # in the local one, so the folder JARVIS uses is Path.home()/Documents.
    local_docs = Path.home() / "Documents"
    check("Documents is the plain local one",
          folders.save_folder("documents").resolve() == local_docs.resolve(),
          f"{folders.save_folder('documents')} (Windows says {folders.known_folder('documents')})")
    check("...so are Downloads and Pictures while they exist locally",
          all(folders.save_folder(k) == Path.home() / k.capitalize()
              for k in ("downloads", "pictures") if (Path.home() / k.capitalize()).exists()))
    check("an unknown name falls back to the naive guess, never raises",
          folders.known_folder("widgets") == Path.home() / "Widgets")
    check("the lookup is cached (same object twice)",
          folders.known_folder("desktop") is folders.known_folder("desktop"))
    check("describe() names every known folder",
          set(folders.describe()) == set(folders.KNOWN_FOLDER_IDS))

    print("\n-- config defaults come from it, and a config entry still wins --")
    check("DEFAULTS.folders.desktop is the resolved Desktop",
          DEFAULTS["folders"]["desktop"] == str(desktop), DEFAULTS["folders"]["desktop"])
    check("...and documents / downloads / pictures are the save folders",
          all(DEFAULTS["folders"][k] == str(folders.save_folder(k))
              for k in ("documents", "downloads", "pictures")))
    check("...documents being the local one", DEFAULTS["folders"]["documents"] == str(local_docs),
          DEFAULTS["folders"]["documents"])

    from tools import builtin, file_tools
    saved = dict(config.data.get("folders") or {})
    tmp = Path(tempfile.mkdtemp(prefix="jarvis-desk-"))
    try:
        config.data["folders"] = {**saved, "desktop": str(tmp)}
        check("file_tools.roots() honours the override",
              file_tools.roots().get("desktop") == tmp, str(file_tools.roots().get("desktop")))
        check("builtin's read roots honour it too (not Path.home()/Desktop)",
              builtin._read_roots().get("desktop") == tmp,
              str(builtin._read_roots().get("desktop")))
        config.data["folders"] = {"desktop": str(tmp)}        # nothing else named
        check("...and the folders the config does not name are the save folders",
              builtin._read_roots().get("documents") == folders.save_folder("documents")
              and builtin._read_roots().get("pictures") == folders.save_folder("pictures"),
              str(builtin._read_roots()))
    finally:
        config.data["folders"] = saved

    bsrc = open(os.path.join(here, "tools", "builtin.py"), encoding="utf-8").read()
    check("builtin no longer builds its roots from the home folder",
          '_HOME / "Desktop"' not in bsrc and "_ALLOWED_READ_ROOTS" not in bsrc
          and "_read_roots()" in bsrc)
    ws = open(os.path.join(here, "workspace.py"), encoding="utf-8").read()
    check("workspace's default root is not a typed-in path",
          r'DEFAULT_ROOT = r"C:\Users' not in ws and 'save_folder("documents")' in ws)

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
