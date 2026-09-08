"""Companion mode: the corner widget he works beside.

His words, 2026-09-08: *"He minimises the Arc Reactor into the bottom left of
my screen, so he's working with me... That should have the same rules as the
rendering stage: same conversation window, same everything."*

The failure that would matter most is not a wrong pixel. It is a window he
cannot get out of — so the ways back are asserted here: the spoken command,
the double-click, and the tray.

Run: python tests/test_companion.py
"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "companion.db"))

ROOT = Path(__file__).resolve().parents[2]
fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


async def main() -> int:
    from tools import office as O
    import events

    sent = []

    async def cap(kind, **kw):
        sent.append((kind, kw))
    real = events.bus.emit
    events.bus.emit = cap
    O._excel = lambda: None
    O._word = lambda: None
    try:
        print("\n-- going in and coming out --")
        check("it starts out", not O.companion_on())
        r = await O.companion_mode(on=True)
        check("the command turns it on", r.get("companion") is True and O.companion_on(), r)
        check("...and tells the HUD", sent[-1] == ("ui", {"action": "companion", "on": True}), sent[-1:])
        check("...saying where he is, out loud", "corner" in r.get("spoken", "").lower(), r.get("spoken"))
        check("...and telling the model to keep it short",
              "one or two short" in r.get("instruction", ""), r.get("instruction"))
        r = await O.companion_mode(on=False)
        check("and off again", r.get("companion") is False and not O.companion_on(), r)
        check("...telling the HUD", sent[-1] == ("ui", {"action": "companion", "on": False}), sent[-1:])
        check("coming back twice is not an error", "already back" in
              (await O.companion_mode(on=False)).get("spoken", ""), "")

        print("\n-- with a spreadsheet open it says which one --")
        class Sheet:
            Name = "Q1"

        class Used:
            Rows = type("R", (), {"Count": 12})()
            Columns = type("C", (), {"Count": 4})()

            def Address(self, a=False, b=False):
                return "A1:D12"

        class Book:
            Name = "Budget.xlsx"
            Saved = True
            Worksheets = [Sheet()]

        class XL:
            Workbooks = type("W", (), {"Count": 1})()
            ActiveWorkbook = Book()
            ActiveSheet = Sheet()
            Selection = Used()

            def __getattr__(self, k):
                raise AttributeError(k)
        xl = XL()
        xl.ActiveSheet.UsedRange = Used()
        O._excel = lambda: xl
        r = await O.companion_mode(on=True)
        check("the first thing said names the workbook and the sheet",
              "Budget.xlsx" in r.get("spoken", "") and "Q1" in r.get("spoken", ""), r.get("spoken"))
        await O.companion_mode(on=False)
    finally:
        events.bus.emit = real
        O._excel = lambda: None

    print("\n-- the same rules as the rendering stage --")
    orch = open(ROOT / "sidecar" / "orchestrator.py", encoding="utf-8").read()
    arm = orch[orch.index("def _arm_conversation"):]
    arm = arm[:arm.index("def ", 40)] if "def " in arm[40:] else arm[:4000]
    check("the conversation window widens in companion mode",
          "companion_on" in arm and "companion_window_s" in arm)
    check("...to the same forty seconds a hologram gets",
          'default=40' in arm and arm.count("default=40") >= 2, arm.count("default=40"))
    check("the brain is told he is in a document",
          '"companion": False' in orch and 'ctx["companion"]' in orch)
    router = open(ROOT / "sidecar" / "brain" / "router.py", encoding="utf-8").read()
    check("...and reading the open document is favoured there",
          "_OFFICE_SKILLS" in router and 'ctx.get("companion")' in router)
    # THE WHOLE BRANCH, not the first 400 characters of it. A fixed window
    # failed the moment the branch grew a comment (2026-09-08) - which is the
    # one edit that should never have broken a test about behaviour.
    _b = router[router.index("if skill in _OFFICE_SKILLS"):]
    _end = _b.find(chr(10) + "    if ", 10)
    branch = _b[:_end if _end > 0 else 1200]
    check("...but never penalised outside it, since Word can be open anyway",
          "else 0.0" in branch, branch[-120:])
    check("...and the window in front of him counts as much as companion mode",
          'ctx.get("office_app")' in branch, branch[-120:])

    print("\n-- there is always a way back --")
    lib = open(ROOT / "src-tauri" / "src" / "lib.rs", encoding="utf-8").read()
    check("one atomic Rust command does the whole window change",
          "fn set_companion(" in lib and "set_companion" in
          lib[lib.index("invoke_handler"):lib.index("invoke_handler") + 300])
    for piece in ("set_decorations(false)", "set_always_on_top(true)",
                  "set_skip_taskbar(true)", "set_min_size", "current_monitor"):
        check(f"...it does {piece}", piece in lib)
    check("the full-size geometry is remembered before it shrinks",
          "full_geometry" in lib and "if saved.is_none()" in lib)
    check("leaving restores decorations, the floor, and the old rectangle",
          "fn restore_full_window" in lib and "set_min_size(Some(LogicalSize::new(940.0, 620.0)))" in lib)
    check("the tray is a way out on its own", lib.count("restore_full_window(") >= 3,
          lib.count("restore_full_window("))

    app = open(ROOT / "src" / "App.tsx", encoding="utf-8").read()
    check("a double-click on the reactor comes back",
          "onDoubleClick" in app and "setCompanion(false)" in app)
    check("...and it says so on hover", "double-click to come back" in app)
    check("the widget draws the core, the state and one line",
          "companion__core" in app and "companion__word" in app and "companion__line" in app)
    check("the window follows the mode", 'invoke("set_companion"' in app)
    store = open(ROOT / "src" / "state" / "store.ts", encoding="utf-8").read()
    check("the HUD's own exit tells the sidecar too", '"/companion"' in store)
    check("entering drops the stage: a 300px window has no room for one",
          "dismissStage()" in store[store.index('action === "companion"'):][:700])
    css = open(ROOT / "src" / "styles.css", encoding="utf-8").read()
    check("the widget has real styles", ".companion {" in css and ".companion__line" in css)
    caps = json.loads((ROOT / "src-tauri" / "capabilities" / "default.json").read_text(encoding="utf-8"))
    check("it can be dragged where he wants it",
          "core:window:allow-start-dragging" in caps["permissions"], caps["permissions"])

    main_py = open(ROOT / "sidecar" / "main.py", encoding="utf-8").read()
    check("the HUD's exit goes through the same function the voice uses",
          '@app.post("/companion")' in main_py and "companion_mode(on=" in main_py)

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
