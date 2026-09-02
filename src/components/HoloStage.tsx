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
  PerspectiveCamera, Plane, Scene, Vector3, WebGLRenderer,
} from "three";
import { useStore } from "../state/store";
import type { HoloState } from "../state/store";
import { api } from "../lib/sidecar";

const CYAN = 0x27c7ff;
const AMBER = 0xffb454;       // overhangs: the one thing allowed to alarm
const GREEN = 0x59e0a5;       // the real toolpath

// Base64 float32, not a JSON array of numbers. A 38k-triangle mesh from tier 3
// is 344,556 coordinates: 7.5 MB of JSON the browser parses number by number,
// against 2.0 MB that arrives as one typed array. Exact, too — three.js wants
// float32 either way.
function f32(b64: string): Float32Array {
  if (!b64) return new Float32Array(0);
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new Float32Array(bytes.buffer);
}

type Geometry = {
  positions_b64: string;
  edge_positions_b64: string;
  size_mm: number[];
  triangles: number;
  edges: number;
  // Only present when the part is actually made of more than one body; the
  // sidecar leaves them out otherwise rather than sending kilobytes to say
  // "there is nothing to explode".
  body_count?: number;
  bodies?: number[];
  body_centres?: number[][];
  error?: string;
};

type Layer = { z: number; paths: number[][] };
type Check = {
  overhang_positions?: number[];
  report?: {
    bed?: { fits: boolean; footprint_mm: number[]; too_tall?: boolean };
    overhangs?: { faces: number; worst_deg: number; fraction?: number };
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
  // Same reasoning as applyCheck: a control must reach the live scene without
  // rebuilding it, or every "turn it" would restart the spin and flash the panel.
  const applyCmd = useRef<((c: NonNullable<HoloState["cmd"]>) => void) | null>(null);
  const holo = useStore((s) => s.holo);
  const checkTs = holo?.check?.ts ?? 0;
  const showLayers = holo?.showLayers ?? false;
  const cmd = holo?.cmd;

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
    // Rotation is held per axis rather than as the two the idle spin uses, so a
    // spoken "tip it forward thirty degrees" and the ambient turn do not fight
    // over the same number.
    const HOME = { rx: -0.2, ry: 0.6, rz: 0, scale: 1 };
    const target = { ...HOME };
    const current = { ...HOME };

    // Section cut. One plane, disabled by default; three.js clips against it
    // only while it is in the material's `clippingPlanes`.
    const clip = new Plane(new Vector3(0, 0, -1), 0);
    let clipping = false;
    const clipped: (Material & { clippingPlanes?: Plane[] | null })[] = [];

    // Exploded view: the untouched positions, so it can always come home.
    let basePos: Float32Array | null = null;
    let vertBody: Int32Array | null = null;
    let bodyDir: number[][] = [];
    let explode = 0;
    let explodeTarget = 0;
    let faceGeom: BufferGeometry | null = null;
    let lastSize: number[] | null = null;   // millimetres, for the cut plane

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
      const positions = f32(geo.positions_b64);
      if (disposed || geo.error || !positions.length) return;

      clear();
      const faces = new BufferGeometry();
      faces.setAttribute("position", new Float32BufferAttribute(positions, 3));
      faces.computeVertexNormals();
      faceGeom = faces;
      basePos = positions.slice();
      // Per-VERTEX body labels: the sidecar sends one per triangle, because
      // three of every four bytes would otherwise be a repeat.
      vertBody = geo.bodies
        ? Int32Array.from(geo.bodies.flatMap((b) => [b, b, b]))
        : null;
      bodyDir = geo.body_centres ?? [];
      explode = 0;
      explodeTarget = 0;
      const faceMat = new MeshBasicMaterial({
        color: CYAN, transparent: true, opacity: 0.075,
        side: DoubleSide, depthWrite: false,
      });
      shell.add(new Mesh(faces, faceMat));
      clipped.length = 0;
      clipped.push(faceMat);

      const edgePositions = f32(geo.edge_positions_b64);
      if (edgePositions.length) {
        const lines = new BufferGeometry();
        lines.setAttribute("position", new Float32BufferAttribute(edgePositions, 3));
        // The wide dim pass is the bloom. One extra draw call, no render target,
        // no post-processing — and it costs the 780M nothing worth measuring,
        // which matters because llama-server owns that GPU.
        const haloMat = new LineBasicMaterial({
          color: CYAN, transparent: true, opacity: 0.16,
        });
        const edgeMat = new LineBasicMaterial({
          color: CYAN, transparent: true, opacity: 0.95,
        });
        const halo = new LineSegments(lines, haloMat);
        halo.scale.setScalar(1.015);
        shell.add(halo);
        shell.add(new LineSegments(lines, edgeMat));
        clipped.push(haloMat, edgeMat);
      }

      // Frame it: the model is already centred by the sidecar, so this only has
      // to choose a distance that fits the largest dimension.
      const [w, h, d] = geo.size_mm;
      lastSize = [w, h, d];
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
          // A COUNT means something on a bracket and nothing on a mesh from a
          // photograph, where "15,424 overhangs" is both true and useless. Past
          // a few hundred faces the AREA is the number he can act on.
          const deg = Math.round(r.overhangs.worst_deg);
          const frac = r.overhangs.fraction;
          faults.push(r.overhangs.faces > 200 && frac != null
            ? `${Math.round(frac * 100)}% OVERHANGING · ${deg}°`
            : `${r.overhangs.faces} OVERHANGS · ${deg}°`);
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

    // ---- the spoken controls ------------------------------------------------
    function setClip(axis: string, at: number) {
      // The model is centred, so the plane sits at (at - 0.5) of the extent from
      // the middle. Axis names are HIS — z is vertical, the axis it stands on the
      // bed on — and the -90 degree `orient` rotation is what maps that onto the
      // renderer's y. Doing the swap here rather than in the language keeps every
      // number in the app Z-up, the way the slicer and printcheck have it.
      const size = lastSize ?? [1, 1, 1];
      const n = axis === "x" ? new Vector3(-1, 0, 0)
        : axis === "y" ? new Vector3(0, 0, 1)
        : new Vector3(0, -1, 0);
      const extent = axis === "x" ? size[0] : axis === "y" ? size[1] : size[2];
      clip.normal.copy(n);
      clip.constant = (0.5 - at) * extent * (axis === "y" ? -1 : 1);
      clipping = true;
      for (const m of clipped) m.clippingPlanes = [clip];
      settled = false;
    }

    function clearClip() {
      clipping = false;
      for (const m of clipped) m.clippingPlanes = null;
      settled = false;
    }

    function applyExplode(t: number) {
      if (!basePos || !vertBody || !faceGeom || !bodyDir.length) return;
      const attr = faceGeom.getAttribute("position") as { array: Float32Array; needsUpdate: boolean };
      const arr = attr.array;
      for (let i = 0, v = 0; i < arr.length; i += 3, v++) {
        const d = bodyDir[vertBody[v]] ?? [0, 0, 0];
        arr[i] = basePos[i] + d[0] * t;
        arr[i + 1] = basePos[i + 1] + d[1] * t;
        arr[i + 2] = basePos[i + 2] + d[2] * t;
      }
      attr.needsUpdate = true;
      settled = false;
    }

    applyCmd.current = (c) => {
      switch (c.action) {
        case "rotate": {
          // Degrees are in HIS frame (z vertical). `orient` already turns the
          // model, so a rotation about his z is a rotation about the renderer's
          // y — the same mapping as the clip plane above, and wrong in the same
          // way if it is only done in one of them.
          const r = ((c.degrees ?? 90) * Math.PI) / 180;
          if (c.axis === "x") target.rx += r;
          else if (c.axis === "y") target.rz += r;
          else target.ry += r;
          spin = false;              // he is steering it now; stop the idle drift
          break;
        }
        case "scale":
          target.scale = Math.max(0.2, Math.min(6, target.scale * (c.factor ?? 1.5)));
          break;
        case "section":
          setClip(c.axis ?? "z", c.at ?? 0.5);
          break;
        case "explode":
          explodeTarget = explodeTarget > 0 ? 0 : 1;
          break;
        case "fit":
          target.scale = 1;
          break;
        case "reset":
          Object.assign(target, HOME);
          clearClip();
          explodeTarget = 0;
          spin = true;
          break;
        case "layers":
          void applyCheck.current?.(!!c.on);
          break;
      }
      settled = false;
    };

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
      const drz = target.rz - current.rz;
      const ds = target.scale - current.scale;
      const dx = explodeTarget - explode;
      const moving = spin
        || Math.abs(dry) > 1e-4 || Math.abs(drx) > 1e-4 || Math.abs(drz) > 1e-4
        || Math.abs(ds) > 1e-4 || Math.abs(dx) > 1e-3;
      // Settle, like the orb. An unmoving scene is not re-rendered.
      if (!moving && settled) return;
      current.ry += dry * 0.1;
      current.rx += drx * 0.1;
      current.rz += drz * 0.1;
      current.scale += ds * 0.1;
      if (Math.abs(dx) > 1e-3) {
        explode += dx * 0.1;
        // Pushed by a fraction of the model's own size, so a 6 mm bracket and a
        // 200 mm frame separate by an amount that reads the same on screen.
        applyExplode(explode * (Math.max(...(lastSize ?? [10])) * 0.45));
      }
      bob += 0.012;
      group.rotation.set(current.rx, current.ry, current.rz);
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
      applyCmd.current = null;
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

  // Controls, keyed on the sequence number rather than the payload: "turn it
  // ninety degrees" twice is two identical payloads and must turn it twice.
  useEffect(() => {
    if (cmd) applyCmd.current?.(cmd);
  }, [cmd?.seq]);

  return (
    <>
      <div className="holo" style={{position:"relative"}}>
        <div className="holo__head mono-sub">
          <span className="holo__name">{holo?.name ?? "hologram"}</span>
          <span ref={label} className="holo__dims" />
        </div>
        <div ref={host} className="holo__canvas" />
        {holo?.check && <div ref={note} className="holo__check mono-sub" />}
        {holo?.hands && holo.hands !== "off" && (
          <div className="holo__hands mono-sub" data-holding={holo.hands === "holding"}>
            {holo.hands === "holding" ? "HOLDING" : "WATCHING YOUR HANDS"}
          </div>
        )}
      </div>
    </>
  );
}
