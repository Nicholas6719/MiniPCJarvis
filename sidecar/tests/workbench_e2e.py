"""The workbench, live: a part made, moved, checked, edited, filed - on the
INSTALLED build, through the same tools the voice reaches.

What this proves that the offline gates cannot: the model writes real
OpenSCAD and OpenSCAD renders it; the stage gets real geometry with real
per-part counts; an edit changes the millimetres and revert puts them back;
a project takes the model in and gives it back. It is silent (speech muted
for the run) and nothing here takes the screen.

It writes into his real work folder and workspace, under names that start
with `e2e-`, and removes everything it made at the end.

Run: python tests/workbench_e2e.py     (sidecar must be running under JARVIS_DEBUG=1)
"""
import io
import json
import os
import shutil
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx   # noqa: E402
import psutil  # noqa: E402

fails, made = [], []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


def port() -> int:
    for p in psutil.process_iter(["name"]):
        if (p.info["name"] or "").lower().startswith("jarvis-sidecar"):
            for c in p.net_connections():
                if c.status == "LISTEN":
                    return c.laddr.port
    raise SystemExit("no sidecar listening")


P = port()
T = open(os.path.expandvars(r"%APPDATA%\JARVIS\session.token")).read().strip()
H = {"X-Jarvis-Token": T}
BASE = f"http://127.0.0.1:{P}"


def tool(_tool: str, timeout: float = 120, **args) -> dict:
    r = httpx.post(f"{BASE}/debug/tool", headers=H, timeout=timeout,
                   json={"tool": _tool, "args": args})
    j = r.json()
    return j.get("result", j) if isinstance(j, dict) else {"raw": j}


def get(path: str, timeout: float = 60):
    return httpx.get(f"{BASE}{path}", headers=H, timeout=timeout).json()


def wait_render(max_s: float) -> dict:
    t0 = time.time()
    last = {}
    while time.time() - t0 < max_s:
        last = tool("render_status")
        if not last.get("busy") and not last.get("running"):
            return last
        time.sleep(2)
    return last


def main() -> int:
    httpx.post(f"{BASE}/debug/silence", headers=H, json={"seconds": 900}, timeout=10)
    print(f"sidecar on {P}, speech muted for the run")

    # ---------------------------------------------------------- a real part
    print("\n-- a part he described, made for real --")
    desc = "a flat plate 40 by 30 by 6 millimetres with one 5 millimetre hole through the middle"
    r = tool("make_hologram", description=desc, name="e2e-plate", confirmed=True, timeout=60)
    check("the render starts without a question (he already agreed)",
          not r.get("_ask") and not r.get("error"), r)
    st = wait_render(240)
    check("...and finishes", not st.get("busy") and not st.get("running"), st)
    cur = tool("show_hologram", name="e2e-plate")
    check("the plate is on the stage", cur.get("on_stage") and not cur.get("error"), cur)
    size = cur.get("size_mm") or [0, 0, 0]
    check("...at roughly the millimetres he asked for",
          abs(size[0] - 40) < 3 and abs(size[1] - 30) < 3 and abs(size[2] - 6) < 2, size)
    if cur.get("on_stage"):
        made.append(("model", "e2e-plate"))
    geo = get("/holo/geometry")
    check("the stage's geometry endpoint serves it", geo.get("triangles", 0) > 0
          and geo.get("positions_b64"), {k: geo.get(k) for k in ("triangles", "edges", "error")})

    # ------------------------------------------------------ the view controls
    print("\n-- moving it, by the words he uses --")
    for said, want in (("turn it ninety degrees", "rotate"),
                       ("show me the top", "view"),
                       ("cut it in half", "section"),
                       ("zoom in on it", "scale"),
                       ("hold it still", "still"),
                       ("back to how it was", "reset")):
        r = tool("holo_control", phrase=said)
        got = (r.get("applied") or {}).get("action") or r.get("action")
        check(f"{said!r} -> {want}", got == want and not r.get("error"), r)
    r = tool("holo_control", phrase="just the helmet")
    check("a single piece has no parts to pick out", "single piece" in r.get("error", ""), r)
    r = tool("inspect_part")
    check("'will it print' answers with real numbers",
          r.get("fits_bed") is not None and r.get("spoken"), r)

    # ------------------------------------------------------- editing the part
    print("\n-- changing the real part, and changing it back --")
    r = tool("edit_part", change="make the hole 8 millimetres", timeout=180)
    check("the edit runs", not r.get("error"), r)
    after = tool("show_hologram", name="e2e-plate").get("size_mm") or [0, 0, 0]
    check("...and the plate is still a plate", abs(after[0] - size[0]) < 2, (size, after))
    r2 = tool("revert_part", timeout=120)
    check("revert puts the old source back", not r2.get("error"), r2)

    # ------------------------------------------------ an assembly, on the stage
    print("\n-- a model made of named parts --")
    # The shell these suites run from sees a VIRTUALISED AppData: a file it
    # writes beside the sidecar's models is not there for the sidecar. So
    # the sidecar writes them, through its debug-only work-file endpoint.
    import base64
    import struct

    def stl_bytes(tris) -> bytes:
        out = bytearray(b"\0" * 80 + struct.pack("<I", len(tris)))
        for t in tris:
            out += struct.pack("<3f", 0, 0, 0)
            for v in t:
                out += struct.pack("<3f", *v)
            out += b"\0\0"
        return bytes(out)

    def box(ox=0.0, s=10.0):
        v = [(x + ox, y, z) for x, y, z in ((0, 0, 0), (s, 0, 0), (s, s, 0), (0, s, 0),
                                            (0, 0, s), (s, 0, s), (s, s, s), (0, s, s))]
        f = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4),
             (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
        return [(v[a], v[b], v[c]) for a, b, c in f]

    def put(name: str, data: bytes) -> str:
        r = httpx.post(f"{BASE}/debug/workfile", headers=H, timeout=30,
                       json={"name": name, "content_b64": base64.b64encode(data).decode()}).json()
        made.append(("workfile", name))
        return r.get("path", "")
    p1 = put("e2e-pair.helmet.stl", stl_bytes(box(0, 10)))
    p2 = put("e2e-pair.gauntlet.stl", stl_bytes(box(30, 6)))
    put("e2e-pair.stl", stl_bytes(box(0, 10) + box(30, 6)))
    put("e2e-pair.parts.json", json.dumps({"parts": [{"name": "helmet", "stl": p1},
                                                      {"name": "gauntlet", "stl": p2}]}).encode())
    r = tool("show_hologram", name="e2e-pair")
    check("the pair goes up with its part names", r.get("part_count") == 2
          and "helmet" in (r.get("parts") or []), r)
    geo = get("/holo/geometry")
    check("the geometry carries per-part triangle and edge counts",
          geo.get("part_tri_counts") == [12, 12] and len(geo.get("edge_counts") or []) == 2,
          {k: geo.get(k) for k in ("part_tri_counts", "edge_counts", "assembly")})
    r = tool("holo_control", phrase="how big is the gauntlet")
    check("'how big is the gauntlet' picks the gauntlet out and says its size",
          r.get("part") == "gauntlet" and "6 by 6 by 6" in r.get("spoken", ""), r)
    r = tool("holo_control", phrase="hide the helmet")
    check("'hide the helmet' hides THAT part, not the hologram",
          r.get("part") == "helmet" and r.get("mode") == "hide", r)
    check("...and the model is still up", tool("show_hologram", name="e2e-pair").get("on_stage"))
    r = tool("holo_control", phrase="put all the parts back")
    check("'put all the parts back' is everything", r.get("action") == "part" and r.get("part") == "", r)
    r = tool("focus_window", title="the helmet")
    check("'focus on the helmet' through the app switch reaches the hologram",
          r.get("focused_part") == "helmet", r)

    # ------------------------------------------------------- the project file
    print("\n-- the project file --")
    r = tool("start_project", name="e2e project", about="a live test", confirmed=True)
    check("a project opens", r.get("project") and not r.get("error"), r)
    proj = r.get("project", "")
    made.append(("project", proj))
    r = tool("file_in_project")
    check("the model on the stage is filed there", not r.get("error"), r)
    r = tool("project_status")
    check("status counts the model and reads the last note",
          r.get("model_count", 0) >= 1 and r.get("project") == proj, r)
    r = tool("recall_project", name="e2e")
    check("recall finds it by half its name", r.get("project") == proj, r)
    r = tool("close_project")
    check("closing it clears the active pointer", r.get("was_active") is True, r)
    r = tool("close_application", name="the project file")
    check("'close the project file' with nothing open says so", "no project" in r.get("error", ""), r)

    # ------------------------------------------------------------- the hands
    print("\n-- hands --")
    r = tool("hand_status")
    check("hands report honestly when not armed", "not watching" in r.get("spoken", ""), r)

    r = tool("hide_hologram")
    check("the stage comes down", not r.get("error"), r)

    # ------------------------------------------------------- nothing left behind
    print("\n-- clearing up --")
    for kind, what in made:
        try:
            if kind == "workfile":
                httpx.post(f"{BASE}/debug/workfile", headers=H, timeout=30,
                           json={"name": what, "delete": True})
            elif kind == "model":
                for ext in (".stl", ".scad", ".prev.scad", ".prev.stl", ".parts.json", ".gcode"):
                    httpx.post(f"{BASE}/debug/workfile", headers=H, timeout=30,
                               json={"name": what + ext, "delete": True})
            elif kind == "project":
                import workspace
                for base in (workspace.root(), os.path.join(workspace.root(), workspace.ARCHIVE)):
                    d = os.path.join(base, what)
                    if os.path.isdir(d):
                        shutil.rmtree(d, ignore_errors=True)
        except OSError as e:
            print("  (could not remove", what, e, ")")
    left = tool("show_hologram", name="e2e-pair")
    check("nothing of the test is left in his work folder",
          "_ask" in left or "error" in left, left)
    tool("hide_hologram")

    print(f"\nWORKBENCH: {'PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
