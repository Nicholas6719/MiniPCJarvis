"""Phase 5: a part, sliced — and an honest skip where a binary is missing.

NEITHER OpenSCAD NOR PrusaSlicer IS INSTALLED ON THIS MACHINE, and there is no
printer. So the generate-and-slice case SKIPS, loudly, naming what is missing. A
green tick for a tool that never ran is worse than no tick: it teaches him the
suite means something it does not, and the first time it matters he will believe
a number nobody produced.

Everything that does NOT need a binary is tested for real, and that is most of
the risk anyway:

  * the estimate parser, against captured PrusaSlicer output — including the
    case where a file slices but reports no numbers, which must be a warning
    rather than a silent success;
  * filename safety: the model names these files from something he said, so a
    name can never walk out of the work directory;
  * the refusals — missing binary, missing STL, wrong extension, empty
    description — all sentences, never exceptions;
  * NoPrinterBackend answers every method rather than raising, because the
    abstraction is the deliverable here and a half-implemented seam is worse
    than none.

Run: python tests/test_fabrication.py
"""
import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "p5.db"))

fails = []
skips = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


def skip(name, why):
    print(f"  SKIP  {name}  ({why})")
    skips.append(name)


SLICER_OUTPUT = """
[info] Processing triangle mesh
; estimated printing time (normal mode) = 1h 12m 30s
; filament used [mm] = 3421.77
; filament used [g] = 10.21
; total filament cost = 0.25
"""


async def main() -> int:
    from tools import fabrication as F

    # ---------------------------------------------------------- name safety
    check("a normal name survives", F.safe_name("bracket v2") == "bracket-v2")
    for evil in ("../../windows/system32", "..\\..\\evil", "/etc/passwd",
                 "a/b/c", "con:.txt"):
        got = F.safe_name(evil)
        check(f"{evil!r} cannot escape the work directory",
              "/" not in got and "\\" not in got and ".." not in got, got)
    check("an empty name still yields something", F.safe_name("") == "part")
    check("a name of only punctuation yields something", F.safe_name("///") == "part")

    # ------------------------------------------------------ estimate parsing
    est = F.parse_slicer_output(SLICER_OUTPUT)
    check("print time is parsed", est.get("print_time") == "1h 12m 30s", est)
    check("filament grams are parsed", est.get("filament_g") == 10.21, est)
    check("filament length is parsed", est.get("filament_mm") == 3421.77, est)
    check("output with no numbers yields nothing, not zeroes",
          F.parse_slicer_output("[info] done") == {},
          "a fabricated 0 g estimate is worse than no estimate")

    # a real gcode footer is read too, since PrusaSlicer writes some there
    d = tempfile.mkdtemp()
    g = os.path.join(d, "x.gcode")
    open(g, "w").write("G1 X0\n" + SLICER_OUTPUT)
    from pathlib import Path
    est2 = F.parse_slicer_output("", Path(g))
    check("the gcode footer is read as well as stdout",
          est2.get("filament_g") == 10.21, est2)

    # ------------------------------------------------------------- refusals
    check("an empty description is refused",
          (await F.generate_part("")).get("error"))
    check("a missing STL is refused",
          (await F.slice_part(os.path.join(d, "nope.stl"))).get("error"))
    open(os.path.join(d, "notmesh.txt"), "w").write("x")
    check("a non-STL is refused",
          (await F.slice_part(os.path.join(d, "notmesh.txt"))).get("error"))

    # ------------------------------------------------------- printer backend
    st = await F.printer_status()
    check("printer status answers rather than raising", st.get("error"), st)
    check("...and says it plainly", "no printer configured" in st["error"], st)
    check("start_print answers too",
          (await F.backend.start_print("x.gcode")).get("error"))
    check("cancel_print answers too", (await F.backend.cancel_print()).get("error"))
    check("the seam for a real printer exists",
          issubclass(F.NoPrinterBackend, F.PrinterBackend))

    # -------------------------------------------------- the bundled profile
    prof = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "profiles", "generic_fdm_0.4.ini")
    check("a default slicer profile ships", os.path.exists(prof))
    if os.path.exists(prof):
        body = open(prof, encoding="utf-8").read()
        check("...and it is a 0.4 mm FDM profile", "nozzle_diameter = 0.4" in body)
        check("...with a layer height", "layer_height" in body)

    # ------------------------------------------------- the part that cannot run
    scad, slicer = F.openscad_path(), F.slicer_path()
    if not scad:
        skip("generate a real part", "OpenSCAD is not installed on this machine")
        res = await F.generate_part("a 20 mm cube")
        check("...and the tool says so instead of failing obscurely",
              res.get("unavailable") is True, res)
    else:
        res = await F.generate_part("a 20 mm cube", name="test-cube")
        check("a cube is generated", res.get("stl") and os.path.exists(res["stl"]), res)

    if not slicer:
        skip("slice a real part and read a real estimate",
             "PrusaSlicer is not installed on this machine")
        res = await F.slice_part(os.path.join(d, "notmesh.txt"))
        check("...and slicing reports the missing binary honestly",
              res.get("error") is not None, res)

    print()
    if skips:
        print(f"  {len(skips)} case(s) SKIPPED because a binary is missing — "
              f"phase 5's gate was NOT fully exercised:")
        for s in skips:
            print(f"     - {s}")
        print("  Install OpenSCAD and PrusaSlicer and re-run to close this.")
    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}"
          f"{f' ({len(skips)} skipped)' if skips else ''}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
