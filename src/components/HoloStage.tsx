// The hologram (§ hologram plan, phases A and B). A model, projected — and
// then interrogated.
//
// Three rules this file exists inside, all learned the hard way elsewhere:
//
//  1. A stage returns a FRAGMENT. Wrapping it in another `.stage` div put the
//     camera panel 646 px off the right edge and cost an afternoon to find.
//  2. Nothing animates through React state. ArcReactor carries the same comment:
//     a 60 fps setState re-renders the tree sixty times a second. The frame loop
//     writes to three.js objects directly and touches no store.
//  3. It settles. The HUD's idle CPU was taken from 41.7% of a core to 0.8%, and
//     a canvas that repaints an unmoving scene would hand that straight back. So
//     when nothing is turning, moving or fading, the loop stops rendering.
//
// The look, decided from mockups: translucent faces, bright edges over a wide
// dim pass for a fake bloom that costs one extra draw rather than a
// post-processing chain, a bed footprint underneath in true proportion, and
// millimetre callouts. Effects on the object and its instrumentation; nothing on
// the empty room.
//
// WHICH WAY IS UP. STL — and every slicer, and the overhang maths in
// printcheck.py — treats +Z as up. three.js treats +Y as up. Phase A fed STL
// coordinates straight in and hung the bed grid off size_mm[1], so the part was
// lying on its side on a bed drawn through the wrong plane. Harmless while the
// hologram was only pretty; wrong the moment overhangs are painted on it, since
// a face flagged as facing "down" would have pointed sideways on screen. So an
// inner `orient` group rotates the model -90° about X once, and everything
// downstream — bed, overhangs, toolpath — lives in that group and agrees.
import { useEffect, useRef } from "react";
// Named imports, not `import * as THREE`. The namespace form pulls the whole
// library past the bundler's tree-shaker; this build only needs a scene, a
// camera, a renderer and three material types.
import {
  BufferGeometry, DoubleSide, Float32BufferAttribute, Group, GridHelper,
  LineBasicMaterial, LineSegments, Material, Mesh, MeshBasicMaterial,
  PerspectiveCamera, Scene, WebGLRenderer,
} from "three";
import { useStore } from "../state/store";
import { api } from "../lib/sidecar";

const CYAN = 0x27c7ff;
const AMBER = 0xffb454;       // overhangs: the one thing allowed to alarm
const GREEN = 0x59e0a5;       // the real toolpath

type Geometry = {
  positions: number[];
  edge_positions: number[];
  size_mm: number[];
  triangles: number;
  edges: number;
  error?: string;
};

type Layer = { z: number; paths: number[][] };
type Check = {
  overhang_positions?: number[];
  report?: {
    bed?: { fits: boolean; footprint_mm: number[]; too_tall?: boolean };
    overhangs?: { faces: number; worst_deg: number };
    wall?: { estimate_mm: number | null; below_minimum?: boolean };
    integrity?: { sliceable: boolean | null };
  };
  gcode?: { layers: Layer[]; count: number; shown?: number;
            truncated?: boolean; layer_height: number | null };
  error?: string;
};

export function HoloStage() {
  const host = useRef<HTMLDivElement>(null);
  const label = useRef<HTMLDivElement>(null);
  const note = useRef<HTMLDivElement>(null);
  // The renderer is built once per model. A check arrives later and must not
  // tear the scene down and rebuild it — that would restart the spin and flash
  // the panel — so it reaches the live scene through this.
  const applyCheck = useRef<((wantLayers: boolean) => void) | null>(null);
  const holo = useStore((s) => s.holo);
  const checkTs = holo?.check?.ts ?? 0;
  const showLayers = holo?.showLayers ?? false;

  useEffect(() => {
    const el = host.current;
    if (!el) return;

    const scene = new Scene();
    const camera = new PerspectiveCamera(38, 1, 0.1, 4000);
    const renderer = new WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.localClippingEnabled = true;
    el.appendChild(renderer.domElement);
    renderer.domElement.style.display = "block";

    const group = new Group();          // spun by the frame loop
    const orient = new Group();         // STL Z-up -> three.js Y-up, once
    orient.rotation.x = -Math.PI / 2;
    group.add(orient);
    const shell = new Group();          // the model itself
    const marks = new Group();          // overhangs
    const path = new Group();           // the sliced toolpath
    orient.add(shell, marks, path);
    const bed = new Group();
    scene.add(group);
    scene.add(bed);

    let disposed = false;
    let raf = 0;
    let spin = true;
    let settled = false;
    let bob = 0;
    const target = { ry: 0.6, rx: -0.2, scale: 1 };
    const current = { ry: 0.6, rx: -0.2, scale: 1 };

    const size = () => {
      const w = el.clientWidth || 800;
      const h = el.clientHeight || 500;
      renderer.setSize(w, h, false);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      settled = false;
    };

    function empty(g: Group) {
      while (g.children.length) {
        const c = g.children.pop()!;
        const any = c as unknown as { geometry?: BufferGeometry; material?: Material };
        any.geometry?.dispose();
        any.material?.dispose();
      }
    }
    const clear = () => [shell, marks, path, bed].forEach(empty);

    async function load() {
      let geo: Geometry;
      try {
        geo = await api("/holo/geometry");
      } catch {
        return;                       // the panel stays, empty, rather than throwing
      }
      if (disposed || geo.error || !geo.positions?.length) return;

      clear();
      const faces = new BufferGeometry();
      faces.setAttribute("position", new Float32BufferAttribute(geo.positions, 3));
      faces.computeVertexNormals();
      shell.add(new Mesh(faces, new MeshBasicMaterial({
        color: CYAN, transparent: true, opacity: 0.075,
        side: DoubleSide, depthWrite: false,
      })));

      if (geo.edge_positions?.length) {
        const lines = new BufferGeometry();
        lines.setAttribute("position", new Float32BufferAttribute(geo.edge_positions, 3));
        // The wide dim pass is the bloom. One extra draw call, no render target,
        // no post-processing — and it costs the 780M nothing worth measuring,
        // which matters because llama-server owns that GPU.
        const halo = new LineSegments(lines, new LineBasicMaterial({
          color: CYAN, transparent: true, opacity: 0.16,
        }));
        halo.scale.setScalar(1.015);
        shell.add(halo);
        shell.add(new LineSegments(lines, new LineBasicMaterial({
          color: CYAN, transparent: true, opacity: 0.95,
        })));
      }

      // Frame it: the model is already centred by the sidecar, so this only has
      // to choose a distance that fits the largest dimension.
      const [w, h, d] = geo.size_mm;
      const span = Math.max(w, h, d) || 1;
      camera.position.set(0, span * 0.34, span * 2.5);
      camera.lookAt(0, 0, 0);

      // The bed, in true proportion to the part — 220 mm from the slicer
      // profile. It sits under the part's Z extent, which after `orient` is the
      // vertical one; hanging it off size_mm[1] put it through the part.
      const grid = new GridHelper(220, 11, CYAN, CYAN);
      const gm = grid.material as Material & { opacity: number; transparent: boolean };
      gm.opacity = 0.14;
      gm.transparent = true;
      grid.position.y = -d / 2 - span * 0.04;
      bed.add(grid);

      if (label.current) {
        label.current.textContent =
          `${Math.round(w)} × ${Math.round(h)} × ${Math.round(d)} mm · ` +
          `${geo.triangles.toLocaleString()} triangles`;
      }
      settled = false;
    }

    // ---- the print check, painted onto the model already on the stage --------
    let lastCheck: Check | null = null;
    let loadedLayers = false;

    function draw(c: Check, wantLayers: boolean) {
      empty(marks);
      if (c.overhang_positions?.length) {
        const g = new BufferGeometry();
        g.setAttribute("position", new Float32BufferAttribute(c.overhang_positions, 3));
        marks.add(new Mesh(g, new MeshBasicMaterial({
          color: AMBER, transparent: true, opacity: 0.42, side: DoubleSide,
          depthWrite: false,
        })));
      }
      if (c.gcode?.layers?.length && !path.children.length) {
        // One LineSegments for the whole print rather than one per layer: a
        // hundred draw calls to show a cube is how a preview becomes the reason
        // the HUD stutters.
        const pts: number[] = [];
        for (const L of c.gcode.layers) {
          for (const poly of L.paths) {
            for (let i = 0; i + 3 < poly.length; i += 2) {
              pts.push(poly[i], poly[i + 1], L.z, poly[i + 2], poly[i + 3], L.z);
            }
          }
        }
        const g = new BufferGeometry();
        g.setAttribute("position", new Float32BufferAttribute(pts, 3));
        // Dim on purpose. A hundred layers of solid infill at full opacity is a
        // solid green block — technically the whole toolpath, and completely
        // unreadable. At this weight the layer banding and the skirt loop on the
        // bed are both visible, which is the thing worth looking at.
        path.add(new LineSegments(g, new LineBasicMaterial({
          color: GREEN, transparent: true, opacity: 0.34,
        })));
      }
      path.visible = wantLayers && path.children.length > 0;
      // Showing the real toolpath and the shell at once is visual mush; the
      // toolpath IS the object when it is up.
      shell.visible = !path.visible;

      const r = c.report;
      if (note.current && r) {
        // Faults first, then the measurements. Keeping them in one list made a
        // solid 20 mm cube report "WALL = 20 MM" and nothing else — the reading
        // was true and the verdict, which is what he actually wants, never
        // appeared at all.
        const faults: string[] = [];
        if (r.bed && !r.bed.fits) faults.push("TOO LARGE FOR BED");
        if (r.bed?.too_tall) faults.push("TOO TALL");
        if (r.integrity?.sliceable === false) faults.push("NOT WATERTIGHT");
        if (r.overhangs?.faces) {
          faults.push(`${r.overhangs.faces} OVERHANGS · ${Math.round(r.overhangs.worst_deg)}°`);
        }
        if (r.wall?.below_minimum) faults.push("WALL UNDER MINIMUM");

        const facts: string[] = [];
        if (r.wall?.estimate_mm != null) facts.push(`WALL ≈ ${r.wall.estimate_mm} MM`);
        // Read the CHECK from the store, not from the `holo` this effect closed
        // over: the effect is keyed on the model name, so its `holo` is the one
        // from before any check existed, and `holo.check.layers` was always
        // undefined. The layer count simply never appeared.
        const live = useStore.getState().holo?.check;
        // The hint stays up whichever way round it is: once the toolpath was
        // showing, the only key that would put the model back stopped being
        // mentioned anywhere.
        const nLayers = c.gcode?.count ?? live?.layers;
        if (nLayers) facts.push(`${nLayers} LAYERS · L`);
        // A capped toolpath must say so. Naming the true layer count while
        // drawing fewer of them would have him reading a preview as complete
        // when the top of the print is simply not on screen.
        if (c.gcode?.truncated && c.gcode.shown) {
          facts.push(`${c.gcode.shown} DRAWN`);
        }

        note.current.textContent =
          [...(faults.length ? faults : ["PRINTS AS IT IS"]), ...facts].join("  ·  ");
        note.current.dataset.bad = String(faults.length > 0);
      }
      settled = false;
    }

    applyCheck.current = async (wantLayers: boolean) => {
      // Fetch once, and once more only if layers are wanted and were not asked
      // for the first time. Re-parsing a 6,000-line G-code file on every toggle
      // would be work done for nothing.
      const needFetch = !lastCheck || (wantLayers && !loadedLayers);
      if (needFetch) {
        try {
          const q = `/holo/printcheck?name=${encodeURIComponent(holo?.name ?? "")}` +
                    (wantLayers ? "&layers=true" : "");
          const got: Check = await api(q);
          if (disposed || got.error) return;
          lastCheck = got;
          if (wantLayers) loadedLayers = true;
        } catch {
          return;
        }
      }
      if (lastCheck) draw(lastCheck, wantLayers);
    };

    const tick = () => {
      raf = requestAnimationFrame(tick);
      if (spin) target.ry += 0.006;
      const dry = target.ry - current.ry;
      const drx = target.rx - current.rx;
      const ds = target.scale - current.scale;
      const moving = spin || Math.abs(dry) > 1e-4 || Math.abs(drx) > 1e-4 || Math.abs(ds) > 1e-4;
      // Settle, like the orb. An unmoving scene is not re-rendered.
      if (!moving && settled) return;
      current.ry += dry * 0.1;
      current.rx += drx * 0.1;
      current.scale += ds * 0.1;
      bob += 0.012;
      group.rotation.set(current.rx, current.ry, 0);
      group.scale.setScalar(current.scale);
      group.position.y = Math.sin(bob) * 1.4;
      bed.rotation.y = current.ry;
      renderer.render(scene, camera);
      settled = !moving;
    };

    size();
    void load();
    raf = requestAnimationFrame(tick);
    const ro = new ResizeObserver(size);
    ro.observe(el);

    return () => {
      disposed = true;
      applyCheck.current = null;
      cancelAnimationFrame(raf);
      ro.disconnect();
      clear();
      renderer.dispose();
      if (renderer.domElement.parentNode === el) el.removeChild(renderer.domElement);
    };
  }, [holo?.name]);

  // A check arrives after the scene is built, and toggling the layer view must
  // not rebuild it either.
  useEffect(() => {
    if (checkTs) void applyCheck.current?.(showLayers);
  }, [checkTs, showLayers]);

  return (
    <>
      <div className="holo">
        <div className="holo__head mono-sub">
          <span className="holo__name">{holo?.name ?? "hologram"}</span>
          <span ref={label} className="holo__dims" />
        </div>
        <div ref={host} className="holo__canvas" />
        {holo?.check && <div ref={note} className="holo__check mono-sub" />}
      </div>
    </>
  );
}
