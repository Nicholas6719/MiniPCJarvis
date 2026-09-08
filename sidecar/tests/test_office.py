"""Working in the document he already has open.

Word and Excel are not running in a build gate, so what is asserted here is
everything that does not need them: the shapes COM hands back, the refusals
when nothing is open, and — most importantly — that this module can never
LAUNCH Office. `Dispatch` would start an invisible copy and write into a
document nobody can see; only `GetActiveObject` may be used.

The live half runs against his real Word and Excel from
`.agent/scripts/office_live.py`, with him watching.

Run: python tests/test_office.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "office.db"))

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


def main() -> int:
    from tools import office as O

    print("\n-- it can never start Office, only attach to it --")
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "tools", "office.py"), encoding="utf-8").read()
    check("GetActiveObject only — no Dispatch anywhere",
          "GetActiveObject" in src and "Dispatch(" not in src)
    check("...and it never saves his work for him",
          ".Save(" not in src and ".SaveAs" not in src)

    print("\n-- the shapes Excel hands back --")
    check("a block of cells", O._cells_to_rows((("a", "b"), (1, 2))) == [["a", "b"], [1, 2]])
    check("one row comes back flat, not nested", O._cells_to_rows(("a", "b")) == [["a", "b"]])
    check("a single cell is still a row", O._cells_to_rows(42) == [[42]])
    check("an empty range is nothing", O._cells_to_rows(None) == [])
    check("a whole number is not shown as 3.0", O._fmt(3.0) == "3", O._fmt(3.0))
    check("...but a real decimal is", O._fmt(3.5) == "3.5")
    check("an empty cell is empty, not 'None'", O._fmt(None) == "")

    rows = [[f"r{i}", i] for i in range(200)]
    text = O._rows_text(rows, limit=60)
    check("a long sheet is capped", len(text.split("\n")) == 61, len(text.split("\n")))
    check("...and says how much was left out", "140 more rows" in text, text[-40:])

    print("\n-- addresses --")
    for good in ("A1", "B4", "AA100", "z9"):
        check(f"{good!r} is a cell", bool(O._ADDR.match(good)))
    for bad in ("A", "4", "B4:C9", "Sheet1!A1", ""):
        check(f"{bad!r} is not", not O._ADDR.match(bad))

    print("\n-- with nothing open, it says so and does nothing --")
    O._excel = lambda: None
    O._word = lambda: None
    st = O.office_status()
    check("status is honest", st.get("open") is False and "Nothing's open" in st.get("spoken", ""), st)
    check("...and tells the model not to guess", "do not guess" in st.get("instruction", "").lower(), st)
    check("reading refuses", "nothing's open" in (O.read_open_document().get("error") or ""),
          O.read_open_document())
    check("writing to Word refuses", "Word isn't open" in (O.edit_open_document("hi").get("error") or ""))
    check("writing to Excel refuses", "Excel isn't open" in (O.set_cells(values=[[1]]).get("error") or ""))
    check("an empty edit is refused before anything else",
          "what should I write" in (O.edit_open_document("  ").get("error") or ""))

    print("\n-- a fake Excel, to prove the writing logic --")

    class FakeRange:
        def __init__(self, sheet, addr, value=None):
            self.sheet, self.addr, self._value = sheet, addr, value

        def Address(self, a=False, b=False):
            return self.addr

        def Offset(self, r, c):
            return FakeRange(self.sheet, f"OFF{r}x{c}")

        @property
        def Value(self):
            return self._value

        @Value.setter
        def Value(self, v):
            self.sheet.written = v

    class FakeSheet:
        Name = "Sheet1"

        def __init__(self):
            self.written = None
            self.existing = (("keep", "keep"), ("keep", "keep"))

        def Range(self, a, b=None):
            if b is None:
                return FakeRange(self, a if isinstance(a, str) else "A1")
            return FakeRange(self, "B4:C6", self.existing)

    class FakeBook:
        Name = "Budget.xlsx"
        Saved = True

        def __init__(self, sheet):
            self.Worksheets = [sheet]

    class Books:
        Count = 1

    class FakeExcel:
        def __init__(self):
            self.ActiveSheet = FakeSheet()
            self.ActiveWorkbook = FakeBook(self.ActiveSheet)
            self.Workbooks = Books()
            self.Selection = FakeRange(self.ActiveSheet, "B4")

    xl = FakeExcel()
    O._excel = lambda: xl
    r = O.set_cells(values=[["Books", 120], ["Bus", 45]], start="B4")
    check("cells are written as one block", not r.get("error") and xl.ActiveSheet.written is not None, r)
    check("...at the address he named", r.get("range") == "B4:C6", r)
    check("...and it reports what it wrote over", r.get("overwritten") == 4, r)
    check("...saying so out loud", "had something in them" in r.get("spoken", ""), r.get("spoken"))
    check("...and that it is not saved", "Control-Z" in r.get("spoken", ""), r.get("spoken"))
    r = O.set_cells(text="a,b\nc,d")
    check("CSV text works, at his selection", r.get("range") == "B4:C6" and not r.get("error"), r)
    r = O.set_cells(values=[["one"], ["two", "three"]], start="B4")
    check("a ragged block does not raise", not r.get("error"), r)
    check("...and nothing to put in is still refused",
          "what should I put in" in (O.set_cells().get("error") or ""), O.set_cells())

    print("\n-- the tools are registered at the right tiers --")
    O.register_all()
    from tools.registry import Risk, registry
    for name, risk in (("office_status", Risk.SAFE), ("read_open_document", Risk.SAFE),
                       ("edit_open_document", Risk.LOW), ("set_cells", Risk.LOW)):
        t = registry.get(name)
        check(f"{name} at {risk.name}", t is not None and t.risk is risk, t.risk if t else None)
    t = registry.get("read_open_document")
    check("reading the open document says it beats looking at the screen",
          "screen" in (t.description or "").lower(), t.description if t else "")

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
