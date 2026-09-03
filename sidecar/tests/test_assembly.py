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

    # WHAT THE MODEL ACTUALLY WROTE when it was given the research brief: six
    # modules, all of them wrapped inside one master module, and only the master
    # called. One top-level call means one part — and the components he wants to
    # zoom into were sitting one level down the whole time. Plus a block of
    # commented-out example calls, which is a hand-written copy of the very
    # dispatcher we generate.
    MASTER = """
module core() { cylinder(d=20, h=30); }
module outer_shell() { cylinder(d=40, h=30); }
module power_core(off) { translate([off,0,0]) cylinder(d=5, h=10); }
module mounting_bracket() { cube([5,5,5]); }

module arc_reactor() {
    // Place core at origin
    core();
    // Outer shell around core
    outer_shell();
    // Power cores on either side
    power_core(-15);
    power_core(15);
}

// Render parts individually
// Uncomment the desired part to view
// core();
// outer_shell();
// mounting_bracket();
arc_reactor();
"""
    mp = [p["name"] for p in assembly.parts_in(MASTER)]
    check("a master module that just assembles the others is read through",
          mp == ["core", "outer_shell", "power_core", "power_core_2"], mp)
    check("...and a module defined but never called is not a part",
          "mounting_bracket" not in mp,
          "the model defines helpers it does not use")
    check("...and the same module called twice is numbered, not dropped",
          "power_core_2" in mp)
    check("a commented-out call is not a call",
          assembly.parts_in(MASTER)[0]["name"] == "core"
          and "// core();" not in assembly._decomment(MASTER),
          "the model writes a hand-made dispatcher as documentation, and "
          "reading it as code made a statement claim to build a module it "
          "only mentioned")

    md = assembly.with_dispatcher(MASTER, assembly.parts_in(MASTER))
    # The DEFINITION stays — the next check asserts it — so the property is
    # that the name appears once and not as a top-level call.
    check("the master's own call is dropped from the dispatcher",
          assembly._decomment(md).count("arc_reactor()") == 1
          and not any(p["name"] == "arc_reactor"
                      for p in assembly.parts_in(md)),
          "left in, it calls every component a second time")
    check("...while the master module itself is left defined",
          "module arc_reactor()" in md)

    # A master call that PLACES the assembly must not be unwrapped: dropping
    # that transform would move every part.
    PLACED = MASTER.replace("arc_reactor();", "translate([0,0,5]) arc_reactor();")
    check("a master call carrying a transform stays one part",
          [p["name"] for p in assembly.parts_in(PLACED)] == ["arc_reactor"],
          "unwrapping it would silently drop the placement from every piece")

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

    # THE BUG THAT COST TWO PARTS OUT OF THREE, on the first real assembly the
    # model ever produced. It wrote:
    #
    #     // Assemble
    #     base();
    #     translate([0, 0, base_size[2]]) ring();
    #
    # and the dispatcher collapsed each statement onto one line, so the `//` ate
    # `base();`. An `if` with an empty body then took the NEXT statement as its
    # body, so `ring` rendered only when you asked for `base`. Everything
    # compiled. The whole-assembly render still looked right.
    COMMENTED = """
module base() { cube([10,10,2]); }
module ring() { cylinder(d=8, h=5); }
module core() { cylinder(d=3, h=6); }

// Assemble
base();
translate([0, 0, 2]) ring();
translate([0, 0, 7]) core();
"""
    cp = assembly.parts_in(COMMENTED)
    cd = assembly.with_dispatcher(COMMENTED, cp)
    def live_call(text: str, module: str) -> bool:
        """Is `module()` actually called here, on a line that is not a comment?"""
        for line in text.splitlines():
            bare = line.strip()
            if bare.startswith("//"):
                continue
            code = bare.split("//", 1)[0]
            if f"{module}(" in code:
                return True
        return False

    check("a comment above a call cannot swallow the call",
          all(live_call(cd.split(f'== "{p["name"]}") {{', 1)[1].split("}", 1)[0],
                        p["name"]) for p in cp),
          "the call ended up behind the `//` and the part rendered nothing")
    check("...because every part is braced",
          all(f'== "{p["name"]}") {{' in cd for p in cp),
          "braces also stop an empty body capturing the next part's line")
    check("...and the statement is left exactly as written",
          "translate([0, 0, 2]) ring();" in cd)

    print("\n-- an edit must not leave the old parts behind --")
    dispatched = assembly.with_dispatcher(ASSEMBLY, assembly.parts_in(ASSEMBLY))
    clean = assembly.strip_dispatcher(dispatched)
    check("the model is given its own source back, not our scaffolding",
          assembly.PART_VAR not in clean,
          "it was being asked to reproduce a machine-generated guard per part, "
          "verbatim, on every edit — and a mangled guard still renders, because "
          "the conditions fall back to \"all\"")
    check("...and stripping it finds the same parts again",
          [p["name"] for p in assembly.parts_in(clean)]
          == [p["name"] for p in assembly.parts_in(ASSEMBLY)])
    check("stripping a source that was never dispatched changes nothing",
          assembly.strip_dispatcher(ASSEMBLY) == ASSEMBLY)

    fab_src = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "tools", "fabrication.py"),
        encoding="utf-8").read()
    check("an edit rebuilds the parts",
          "_split_into_parts(exe, scad, stl, code, out_d)" in fab_src,
          "without this, asking for a bigger ring and then zooming in on the "
          "ring shows the ring from before the edit, with its old dimensions "
          "read out as fact")
    check("...and forgets them when the new source has none",
          "clear_manifest" in fab_src)

    print("\n-- an operation that fails while reporting success --")
    check("a part file is removed before it is rendered",
          "out_stl.unlink()" in fab_src,
          "OpenSCAD exits 0 and writes NOTHING when the object is empty, "
          "leaving the previous file intact — so last run's part is accepted "
          "as this one's")
    check("a component that renders nothing refuses the whole assembly",
          "parts_incomplete" in fab_src,
          "shipping the rest hands him a model with a piece missing and no "
          "indication; this is how base and ring both vanished")

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

        # AND THE COMMENTED SOURCE, RENDERED. This is the case that cost two
        # parts out of three on real model output, and no structural check is
        # worth as much as asking OpenSCAD whether a part came out.
        cs = os.path.join(d, "commented.scad")
        open(cs, "w", encoding="utf-8").write(cd)
        built = {}
        for target in ("base", "ring", "core"):
            o = os.path.join(d, f"commented.{target}.stl")
            if os.path.exists(o):
                os.remove(o)          # a stale file would pass for a fresh one
            subprocess.run([exe, "-D", f'{assembly.PART_VAR}="{target}"',
                            "-o", o, cs], capture_output=True, text=True,
                           timeout=120)
            built[target] = os.path.exists(o) and os.path.getsize(o) > 84
        check("every part of a commented assembly really renders", all(built.values()),
              f"{built} — base and ring both vanished here, and the whole "
              f"render still looked correct")

    print()
    if skips:
        print(f"  {len(skips)} case(s) SKIPPED:")
        for name, why in skips:
            print(f"     - {name}: {why}")
    print("\n-- the parts render in parallel, and STILL in source order --")
    fab = open(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "tools", "fabrication.py"), encoding="utf-8").read()
    body = fab[fab.index("async def _split_into_parts"):]
    body = body[:body.index("async def edit_part")]

    check("the per-part renders are gathered, not awaited one at a time",
          "asyncio.gather(" in body and "async def render_one" in body,
          "twenty-four half-second renders back to back is twelve seconds "
          "of him waiting for work the machine can overlap")
    check("...but bounded, because llama-server is on the same box",
          "asyncio.Semaphore(" in body and "part_render_lanes" in body,
          "unbounded would take the machine away from the thing that "
          "answers him")
    check("...and ORDER SURVIVES, because gather returns in input order",
          "zip(parts, rendered)" in body,
          "the parts are read back to him in the order the source "
          "declares them; whichever finishes first must not change that")
    check("...and a part that built nothing is still caught by name",
          "empty.append(p[" in body and "if got is None" in body,
          "this is how base and ring both vanished while the whole "
          "render still looked correct")
    check("...and the stale-file unlink is still inside the per-part path",
          "out_stl.unlink()" in body and "async with lanes" in body
          and body.index("out_stl.unlink()") < body.index("async with lanes"),
          "OpenSCAD exits 0 and writes nothing on an empty part")


    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}"
          f"{f' ({len(skips)} skipped)' if skips else ''}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
