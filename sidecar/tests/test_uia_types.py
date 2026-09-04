"""The UIA control-type table, against the one Windows actually publishes.

This drifted by one from 50029 onward and nothing noticed, because a wrong label
is not a crash: `list_controls` cheerfully told the model a Document was a
"pane", a Pane was a "datagrid" and a Table was a "splitbutton", in every result
it ever returned. Names it could not act on wrongly, so nothing failed loudly.

What it did break was `read_window_text`, which looked up 50029 on the strength
of this table calling it "document". 50029 is DataItem. Notepad's editing
surface is a 50030 Document named "Text editor" publishing a ValuePattern, so
the read came back empty from a document with text in it — and the dictation
receipt in hands_e2e failed on a build where dictation worked perfectly.

The fix is a table; the gate is the table. These IDs are fixed by Windows and
will not change, so a literal copy here is the right kind of duplication: it
cannot drift silently, because drifting is exactly what it now fails on.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# From the Windows UI Automation control-type identifiers.
WINDOWS = {
    50000: "button", 50001: "calendar", 50002: "checkbox", 50003: "combobox",
    50004: "edit", 50005: "hyperlink", 50006: "image", 50007: "listitem",
    50008: "list", 50009: "menu", 50010: "menubar", 50011: "menuitem",
    50012: "progressbar", 50013: "radiobutton", 50014: "scrollbar",
    50015: "slider", 50016: "spinner", 50017: "statusbar", 50018: "tab",
    50019: "tabitem", 50020: "text", 50021: "toolbar", 50022: "tooltip",
    50023: "tree", 50024: "treeitem", 50025: "custom", 50026: "group",
    50027: "thumb", 50028: "datagrid", 50029: "dataitem", 50030: "document",
    50031: "splitbutton", 50032: "window", 50033: "pane", 50034: "header",
    50035: "headeritem", 50036: "table", 50037: "titlebar", 50038: "separator",
}

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}{'' if ok else '  — ' + detail}")
    if not ok:
        FAILURES.append(name)


def main() -> int:
    from tools import uia

    print("\nevery label is the one Windows uses")
    wrong = {k: (v, WINDOWS.get(k)) for k, v in uia._INTERESTING.items()
             if WINDOWS.get(k) != v}
    for k, (was, should) in sorted(wrong.items()):
        print(f"        {k}: we say {was!r}, Windows says {should!r}")
    check(f"all {len(uia._INTERESTING)} control types", not wrong,
          f"{len(wrong)} mislabelled")

    print("\nand every id is one Windows defines")
    unknown = sorted(set(uia._INTERESTING) - set(WINDOWS))
    check("no invented ids", not unknown, str(unknown))

    print("\nthe clickable set is made of real ids")
    bad = sorted(set(uia._CLICKABLE) - set(WINDOWS))
    check("no invented ids there either", not bad, str(bad))
    # The ones a "click X" almost always means. Named rather than numbered, so
    # this reads as the intent it is protecting.
    want = {"button", "checkbox", "combobox", "hyperlink", "listitem",
            "menuitem", "radiobutton", "tab", "tabitem", "treeitem",
            "dataitem", "splitbutton", "edit"}
    got = {WINDOWS[i] for i in uia._CLICKABLE if i in WINDOWS}
    check("and it is the set that was meant", got == want,
          f"extra {sorted(got - want)} missing {sorted(want - got)}")

    print("\nWHERE TEXT LIVES — the lookup that came back empty")
    readable = {WINDOWS.get(i) for i in uia._READABLE}
    check("a Document is readable", "document" in readable, str(readable))
    check("...and so is an Edit", "edit" in readable, str(readable))
    check("and read_window_text is registered",
          "read_window_text" in Path(uia.__file__).read_text(encoding="utf-8"))

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: " + ", ".join(FAILURES))
        return 1
    print("UIA control types: all good")
    return 0


sys.exit(main())
