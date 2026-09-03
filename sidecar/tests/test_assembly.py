"""A model made of named parts, and the two ways of getting one wrong.

His picture of this: *"Render me Iron Man's Mark 3 suit... I zoom in on the
helmet to see the helmet specs. I zoom in on the gauntlet to see the gauntlet
specs."* That needs the model to KNOW WHAT ITS PIECES ARE CALLED, and there are
exactly two ways to fail: miss a part that is really there, or invent one that
is not. Both are represented here with the sources that actually caused them.

Offline. OpenSCAD is only exercised where it is installed, and skipped LOUDLY
where it is not, because a green tick for something that never ran teaches the
suite means more than it does.

Run: python tests/test_assembly.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "asm.db"))

fails = []
skips = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


def skip(name, why):
    print(f"  SKIP  {name}  ({why})")
    skips.append((name, why))


# What the local model actually returned for "iron man's arc reactor": one
# module holding everything, with a comment block above it.
ONE_MODULE = """$fn = 48;

// Iron Man's Arc Reactor
// Simple printable version
module arc_reactor() {
    w = 40;
    difference() {
        cylinder(d=w, h=20);
        cylinder(d=w-8, h=21);
    }
}

arc_reactor();
"""

# A real assembly, with a helper module used inside another one.
ASSEMBLY = """
outer_d = 76;
module coil() { cylinder(d=6, h=10); }
// the outer rim
module outer_ring() { cylinder(d=outer_d, h=8); }
/* the middle */
module core() { cylinder(d=28, h=6); }
module coil_housing() { for (i=[0:7]) rotate([0,0,i*45]) coil(); }
outer_ring();
coil_housing();
translate([0,0,2]) core();
"""


def main() -> int:
    import assembly

    print("\n-- what counts as a part --")
    names = [p["name"] for p in assembly.parts_in(ASSEMBLY)]
    check("every component called at the top level is a part",
          names == ["outer_ring", "coil_housing", "core"], names)
    check("...and a module used INSIDE another one is not",
          "coil" not in names,
          "coil is called six times from coil_housing; promoting it would give "
          "six identical coils and no housing")
    check("...and a part keeps the transform that places it",
          any("translate" in p["text"] for p in assembly.parts_in(ASSEMBLY)
              if p["name"] == "core"),
          "without it the core renders at the origin instead of where it sits")

    # THE BUG THIS SUITE EXISTS FOR. The definition is preceded by comments, so
    # testing the raw text for "module" missed it, and the file came back with
    # TWO parts — both the same module, one of them its own definition.
    one = [p["name"] for p in assembly.parts_in(ONE_MODULE)]
    check("a definition behind a comment block is not a part", one == ["arc_reactor"],
          one)
    check("...so a single-module file is not an assembly", len(one) < 2,
          "one part is the whole thing with a label, and announcing '1 of 1' "
          "is worse than saying nothing")
    check("a file with no modules has no parts", assembly.parts_in("cube([1,1,1]);") == [])

    print("\n-- rendering one part at a time --")
    parts = assembly.parts_in(ASSEMBLY)
    out = assembly.with_dispatcher(ASSEMBLY, parts)
    check("every part is guarded so it can be rendered alone",
          all(f'== "{p["name"]}"' in out for p in parts))
    check("...and the default still builds the whole thing",
          f'{assembly.PART_VAR} = "all";' in out)
    check("...and the placement survives the rewrite",
          "translate([0,0,2]) core();" in " ".join(out.split()))
    check("the helper module is left where it belongs",
          "module coil()" in out and '== "coil"' not in out)

    print("\n-- the manifest --")
    d = tempfile.mkdtemp()
    stl = os.path.join(d, "arc.stl")
    open(stl, "wb").write(b"\0" * 84)
    made = []
    for n in ("outer_ring", "core"):
        p = os.path.join(d, f"arc.{n}.stl")
        open(p, "wb").write(b"\0" * 84)
        made.append({"name": n, "stl": p})
    made.append({"name": "gone", "stl": os.path.join(d, "arc.gone.stl")})
    assembly.write_manifest(stl, made)
    got = assembly.read_manifest(stl)
    check("the parts come back by name and file", [n for n, _ in got] == ["outer_ring", "core"])
    check("...and a part whose file has gone is dropped here, not at render time",
          all(os.path.exists(p) for _, p in got))
    check("a model with no manifest simply has no parts",
          assembly.read_manifest(os.path.join(d, "nothing.stl")) == [])

    print("\n-- the payload the stage receives --")
    import numpy as np

    import meshio

    def box(path, w, h, dp, z=0.0):
        """A closed-enough box, written as a binary STL."""
        v = np.array([[0, 0, z], [w, 0, z], [w, h, z], [0, h, z],
                      [0, 0, z + dp], [w, 0, z + dp], [w, h, z + dp], [0, h, z + dp]],
                     dtype=np.float32)
        f = [(0, 1, 2), (0, 2, 3), (4, 6, 5), (4, 7, 6), (0, 4, 5), (0, 5, 1),
             (1, 5, 6), (1, 6, 2), (2, 6, 7), (2, 7, 3), (3, 7, 4), (3, 4, 0)]
        tris = np.array([[v[a], v[b], v[c]] for a, b, c in f], dtype="<f4")
        with open(path, "wb") as fh:
            fh.write(b"\0" * 80)
            fh.write(len(tris).to_bytes(4, "little"))
            for t in tris:
                fh.write(b"\0" * 12)
                fh.write(t.astype("<f4").tobytes())
                fh.write(b"\0\0")
        return path

    a = box(os.path.join(d, "a.stl"), 40, 40, 10)
    b = box(os.path.join(d, "b.stl"), 20, 20, 6, z=10)
    pay = meshio.assembly_payload([("base", a), ("cap", b)])
    check("it arrives as an assembly", pay.get("assembly") is True)
    check("...with both parts named", [p["name"] for p in pay["parts"]] == ["base", "cap"])
    check("...each carrying its OWN dimensions",
          pay["parts"][0]["size_mm"] == [40.0, 40.0, 10.0]
          and pay["parts"][1]["size_mm"] == [20.0, 20.0, 6.0],
          "this is the answer to 'zoom in on the helmet to see the helmet specs'")
    check("...and the whole thing measured together",
          pay["size_mm"] == [40.0, 40.0, 16.0], pay["size_mm"])
    check("every triangle knows which part it belongs to",
          len(pay["bodies"]) == pay["triangles"])
    check("...and each part knows which way to move when it explodes",
          len(pay["body_centres"]) == 2
          and pay["body_centres"][1][2] > pay["body_centres"][0][2],
          "the cap sits above the base, so it explodes upward")

    # A part that will not load is REPORTED. Half an assembly drawn as though it
    # were whole is the same lie as a quarter of a helmet called a helmet.
    pay2 = meshio.assembly_payload([("base", a), ("missing", os.path.join(d, "no.stl"))])
    check("a part that will not load is reported, not quietly dropped",
          pay2.get("parts_missing") and pay2["parts_missing"][0]["name"] == "missing")
    check("...and what did load is still shown", pay2["part_count"] == 1)

    print("\n-- the prompt asks for components, and asks NOT to invent them --")
    fab = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "tools", "fabrication.py"), encoding="utf-8").read()
    check("the model is told to give each real component its own module",
          "give each one its own module" in fab,
          "it returned one module holding the rim, the coils and the core — "
          "correct, printable, and impossible to point at")
    check("...and told not to split a cube",
          "do NOT invent divisions" in fab,
          "a cube wrapped in a module and announced as an assembly is a worse "
          "answer than a cube")

    print("\n-- OpenSCAD really does render one part at a time --")
    from tools.fabrication import openscad_path
    exe = openscad_path()
    if not exe:
        skip("per-part rendering", "OpenSCAD is not installed here")
    else:
        import subprocess
        src = ("module ring() { cylinder(d=40, h=8, $fn=24); }\n"
               "module pin() { cylinder(d=6, h=20, $fn=12); }\n"
               "ring();\ntranslate([0,0,4]) pin();\n")
        ps = assembly.parts_in(src)
        sp = os.path.join(d, "two.scad")
        open(sp, "w", encoding="utf-8").write(assembly.with_dispatcher(src, ps))
        sizes = {}
        for target in ("all", "ring", "pin"):
            o = os.path.join(d, f"two.{target}.stl")
            r = subprocess.run([exe, "-D", f'{assembly.PART_VAR}="{target}"',
                                "-o", o, sp], capture_output=True, text=True,
                               timeout=120)
            sizes[target] = (meshio.describe(o)["size_mm"]
                             if r.returncode == 0 and os.path.exists(o) else None)
        check("the whole assembly builds", sizes["all"] and sizes["all"][0] == 40.0,
              sizes["all"])
        check("...and one named part builds ALONE",
              sizes["ring"] and sizes["ring"][2] == 8.0, sizes["ring"])
        check("...and so does the other, with its placement",
              sizes["pin"] and sizes["pin"][0] == 6.0, sizes["pin"])
        check("-D really does override a top-level variable",
              sizes["ring"] != sizes["all"],
              "if this fails, every part is the whole model wearing a name")

    print()
    if skips:
        print(f"  {len(skips)} case(s) SKIPPED:")
        for name, why in skips:
            print(f"     - {name}: {why}")
    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}"
          f"{f' ({len(skips)} skipped)' if skips else ''}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
