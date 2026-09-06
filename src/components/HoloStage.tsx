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
import { useEffect, useRef, useState } from "react";
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
import { api, sidecarInfo } from "../lib/sidecar";

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
  has_colour?: boolean;
  parts?: { name: string; colour?: string; size_mm?: number[] }[];
  bodies?: number[];
  body_centres?: number[][];
  // Triangles and edge segments per part, in part order, so one part can be
  // drawn on its own by draw range ("focus on the helmet").
  part_tri_counts?: number[];
  edge_counts?: number[];
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

/** The camera, small, in the corner of the hologram.
 *
 * His ask: "I want to be able to see the camera view when I'm using the hand
 * mode... I should be able to see my camera on the bottom right or something.
 * I can see how it sees my hands, so I can work with it like that."
 *
 * He is right, and it is partly my doing that he could not: the camera panel
 * used to take the whole stage, which hid the model he was reaching for, so I
 * stopped it doing that — and left him with no way to see the camera at all
 * while a hologram was up. This is the other half of that change.
 *
 * MIRRORED, because he is looking at himself: moving his hand right must move
 * the picture right. The tracker already flips the same way (`grip_point`
 * mirrored=true), so the two agree.
 */
function HandCam() {
  const [src, setSrc] = useState("");
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let dead = false;
    (async () => {
      const { port, token } = await sidecarInfo();
      if (dead) return;
      // cache-buster, same reason as the full camera stage: without it a re-open
      // reuses the finished stream and shows one frozen frame.
      setSrc(`http://127.0.0.1:${port}/camera/stream`
        + `?token=${encodeURIComponent(token)}&t=${Date.now()}`);
    })();
    return () => { dead = true; setSrc(""); };
  }, []);
  if (failed) return null;
  return (
    <div className="holo__cam">
      {src && <img className="holo__cam-img" src={src} alt=""
                   onError={() => setFailed(true)} />}
      <div className="holo__cam-tag mono-sub">HANDS</div>
    </div>
  );
}


export function HoloStage() {
  const host = useRef<HTMLDivElement>(null);
  const label = useRef<HTMLDivElement>(null);
  const note = useRef<HTMLDivElement>(null);
  // The renderer is built once per model. A check arrives later and must not
  // tear the scene down and rebuild it — that would restart the spin and flash
  // the panel — so it reaches the live scene through this.
  const applyCheck = useRef<((wantLayers: boolean) => void) | null>(null);
  // The layer scale. Written to directly from the rAF/scrub path, never through
  // React state — the same rule as every other moving thing in this file.
  const scale = useRef<HTMLDivElement | null>(null);
  const applyHands = useRef<((state: string) => void) | null>(null);
  // Same reasoning as applyCheck: a control must reach the live scene without
  // rebuilding it, or every "turn it" would restart the spin and flash the panel.
  const applyCmd = useRef<((c: NonNullable<HoloState["cmd"]>) => void) | null>(null);
  const applyReload = useRef<(() => Promise<void>) | null>(null);
  // Which mesh the scene is currently showing. Without it, opening a hologram
  // fetches the geometry twice — once when the scene is built and again from
  // the reload effect firing on the same event — and that is a few hundred
  // kilobytes and half a second of numpy for nothing.
  const loadedTs = useRef<number | null>(null);
  const holo = useStore((s) => s.holo);
  const project = useStore((s) => s.project);
  const checkTs = holo?.check?.ts ?? 0;
  const showLayers = holo?.showLayers ?? false;
  const cmd = holo?.cmd;
  // The camera panel no longer takes the stage from a hologram, so this
  // corner view is the only way he can see what the tracker sees.
  const cameraOn = useStore((s) => s.cameraOn);

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
    // The grab affordance: eight corner brackets around the model's bounds.
    // Mixed-reality toolkits all landed on the same answer — show what is
    // grabbable BEFORE the grab, or the user waves at an object with no idea
    // whether it is listening. Ours had exactly that gap: the camera was armed
    // and the model looked identical either way, so the only way to find out was
    // to try. It lives outside `orient` because it wraps the model as displayed.
    const grabber = new Group();
    grabber.visible = false;
    group.add(grabber);
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
    let hasColour = false;
    let trueColour = false;
    let shellMat: MeshBasicMaterial | null = null;

    /** "#rrggbb" -> linear 0..1, or null. */
    const hexToRgb = (hex?: string): [number, number, number] | null => {
      if (!hex || hex.length !== 7 || hex[0] !== "#") return null;
      const n = Number.parseInt(hex.slice(1), 16);
      if (Number.isNaN(n)) return null;
      return [(n >> 16 & 255) / 255, (n >> 8 & 255) / 255, (n & 255) / 255];
    };

    /** Swap between the hologram and the thing itself. */
    const paintTrue = (on: boolean) => {
      trueColour = on;
      if (!shellMat) return;
      shellMat.vertexColors = on;
      // The hologram is a ghost on purpose; the object is not. Opacity moves
      // with the mode or the colours are there and invisible.
      shellMat.opacity = on ? 0.92 : 0.075;
      shellMat.depthWrite = on;
      shellMat.needsUpdate = true;
    };
    let faceGeom: BufferGeometry | null = null;
    let lastSize: number[] | null = null;   // millimetres, for the cut plane
    // ONE PART OF IT. Parts arrive concatenated in manifest order, triangles
    // and edges alike, so a part is a draw range on each buffer; the rest of
    // the model stays as a ghost so he keeps his bearings. Hidden parts have
    // their vertices collapsed to the origin (a degenerate triangle draws
    // nothing), which survives the exploded view because applyExplode
    // re-applies it after it rewrites positions.
    let edgeGeom: BufferGeometry | null = null;
    let edgeBase: Float32Array | null = null;
    let partTris: number[] = [];
    let edgeCounts: number[] = [];
    let partNames: string[] = [];
    let partSizes: number[][] = [];
    let focused = -1;
    const hidden = new Set<number>();
    let ghost: Group | null = null;
    // BEFORE AND AFTER: the mesh from before the last edit, drawn in amber
    // over the new one. Fetched only when asked for, dropped with the model.
    const before = new Group();
    before.visible = false;
    orient.add(before);
    let beforeLoaded = false;
    // The sliced toolpath, and how far up it he is looking. Layers are laid into
    // ONE buffer in order, so scrubbing is a `setDrawRange` — one draw call and
    // instant — rather than a hundred meshes toggled on and off.
    let pathGeom: BufferGeometry | null = null;
    let layerEnds: number[] = [];           // cumulative vertex count per layer
    let shownLayer = -1;                    // -1 = the whole print

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
    // ...and the grabber brackets, which were rebuilt per model and never
    // disposed — sixteen models in a session is sixteen sets of leaked lines.
    const clear = () => [shell, marks, path, bed, grabber, before].forEach(empty);

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
      // A NEW MESH INVALIDATES THE OLD CHECK. The same name re-rendered
      // (an edit, or a progressive rung) reloads geometry here, but the
      // overhang positions and the layer count cached from the previous mesh
      // survived — amber triangles from the old part floating on the new one
      // the next time he asked "will it print".
      lastCheck = null;
      loadedLayers = false;
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
      // A new mesh forgets which part was singled out and puts the model
      // back in the middle; the counts come with the geometry.
      partTris = geo.part_tri_counts ?? [];
      edgeCounts = geo.edge_counts ?? [];
      partNames = (geo.parts ?? []).map((p) => p.name);
      partSizes = (geo.parts ?? []).map((p) => p.size_mm ?? []);
      focused = -1;
      hidden.clear();
      ghost = null;
      edgeGeom = null;
      edgeBase = null;
      orient.position.set(0, 0, 0);
      beforeLoaded = false;
      before.visible = false;
      // THE REAL COLOURS, when the model has any. One attribute over the buffer
      // that is already there — the per-vertex part label exists for the
      // exploded view — so this costs no extra draw call and nothing at all on
      // a model with no colours in it.
      hasColour = false;
      if (geo.has_colour && vertBody && geo.parts?.length) {
        const rgb = geo.parts.map((p) =>
          hexToRgb(p.colour) ?? [1, 1, 1] as [number, number, number]);
        const col = new Float32Array(positions.length);
        for (let i = 0, v = 0; v < vertBody.length; v++, i += 3) {
          const c = rgb[vertBody[v]] ?? [1, 1, 1];
          col[i] = c[0]; col[i + 1] = c[1]; col[i + 2] = c[2];
        }
        faces.setAttribute("color", new Float32BufferAttribute(col, 3));
        hasColour = true;
      }
      const faceMat = new MeshBasicMaterial({
        color: CYAN, transparent: true, opacity: 0.075,
        side: DoubleSide, depthWrite: false,
      });
      shell.add(new Mesh(faces, faceMat));
      clipped.length = 0;
      clipped.push(faceMat);
      shellMat = faceMat;
      if (trueColour && hasColour) paintTrue(true);

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
        edgeGeom = lines;
        edgeBase = edgePositions.slice();
      }

      // Frame it: the model is already centred by the sidecar, so this only has
      // to choose a distance that fits the largest dimension.
      const [w, h, d] = geo.size_mm;
      lastSize = [w, h, d];
      buildGrabber(w, d, h);   // after `orient`, the part's Z is the screen's Y
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
        layerEnds = [];
        for (const L of c.gcode.layers) {
          for (const poly of L.paths) {
            for (let i = 0; i + 3 < poly.length; i += 2) {
              pts.push(poly[i], poly[i + 1], L.z, poly[i + 2], poly[i + 3], L.z);
            }
          }
          // Where this layer ends in the buffer. Because layers go in bottom to
          // top and in order, "show me up to layer N" is a draw range.
          layerEnds.push(pts.length / 3);
        }
        const g = new BufferGeometry();
        g.setAttribute("position", new Float32BufferAttribute(pts, 3));
        pathGeom = g;
        // Dim on purpose. A hundred layers of solid infill at full opacity is a
        // solid green block — technically the whole toolpath, and completely
        // unreadable. At this weight the layer banding and the skirt loop on the
        // bed are both visible, which is the thing worth looking at.
        path.add(new LineSegments(g, new LineBasicMaterial({
          color: GREEN, transparent: true, opacity: 0.34,
        })));
      }
      path.visible = wantLayers && path.children.length > 0;
      // The scale belongs to the toolpath: no toolpath, no ruler. It also gets
      // reset to the whole print, so switching the layers back on does not
      // resume halfway up a part he stopped looking at ten minutes ago.
      if (!path.visible && scale.current) {
        scale.current.dataset.on = "";
        shownLayer = -1;
        pathGeom?.setDrawRange(0, Infinity);
      } else if (path.visible && layerEnds.length) {
        // Up the MOMENT the toolpath is, reading "ALL 30" — not only once he has
        // already scrubbed. A ruler that appears after you use it does not tell
        // you the control exists, which is the same gap the grab affordance was
        // added to close.
        showLayer(shownLayer);
      }
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
      applyHidden();
      settled = false;
    }

    /** Where part `i` sits in the face and edge buffers (floats, not points). */
    function partRange(i: number) {
      let t0 = 0;
      let e0 = 0;
      for (let k = 0; k < i; k++) {
        t0 += partTris[k] ?? 0;
        e0 += edgeCounts[k] ?? 0;
      }
      return {
        v0: t0 * 9, vn: (partTris[i] ?? 0) * 9,        // 3 vertices × 3 floats
        e0: e0 * 6, en: (edgeCounts[i] ?? 0) * 6,      // 2 points × 3 floats
      };
    }

    /** Collapse every hidden part to the origin, faces and edges alike. */
    function applyHidden() {
      if (edgeGeom && edgeBase) {
        const ea = edgeGeom.getAttribute("position") as { array: Float32Array; needsUpdate: boolean };
        ea.array.set(edgeBase);
        for (const i of hidden) {
          const r = partRange(i);
          ea.array.fill(0, r.e0, r.e0 + r.en);
        }
        ea.needsUpdate = true;
      }
      if (!faceGeom || !hidden.size) return;
      const attr = faceGeom.getAttribute("position") as { array: Float32Array; needsUpdate: boolean };
      for (const i of hidden) {
        const r = partRange(i);
        attr.array.fill(0, r.v0, r.v0 + r.vn);
      }
      attr.needsUpdate = true;
    }

    /** One part on its own: draw ranges on the real buffers, a ghost of the
     *  whole for bearings, the part brought to the middle and framed. */
    function focusOn(i: number) {
      if (!faceGeom || i < 0 || i >= partTris.length) return;
      const r = partRange(i);
      faceGeom.setDrawRange(r.v0 / 3, r.vn / 3);
      edgeGeom?.setDrawRange(r.e0 / 3, r.en / 3);
      if (!ghost) {
        // The rest of the model, faint. Same attributes, new geometries, so
        // the draw range above does not apply to them.
        ghost = new Group();
        const g = new BufferGeometry();
        g.setAttribute("position", faceGeom.getAttribute("position"));
        ghost.add(new Mesh(g, new MeshBasicMaterial({
          color: CYAN, transparent: true, opacity: 0.025, side: DoubleSide, depthWrite: false,
        })));
        if (edgeGeom) {
          const eg = new BufferGeometry();
          eg.setAttribute("position", edgeGeom.getAttribute("position"));
          ghost.add(new LineSegments(eg, new LineBasicMaterial({
            color: CYAN, transparent: true, opacity: 0.07,
          })));
        }
        shell.add(ghost);
      }
      // The part's centre, in STL coordinates relative to the model's centre.
      // `orient` turns Z-up into Y-up, so its position is set in the parent's
      // frame: (x, y, z) -> (x, z, -y).
      const c = bodyDir[i] ?? [0, 0, 0];
      orient.position.set(-c[0], -c[2], c[1]);
      const whole = Math.max(...(lastSize ?? [1])) || 1;
      const part = Math.max(...(partSizes[i]?.length ? partSizes[i] : [whole])) || whole;
      target.scale = Math.max(1, Math.min(6, (whole / part) * 0.8));
      spin = false;
      focused = i;
      if (label.current && partSizes[i]?.length === 3) {
        const [w, h, d] = partSizes[i];
        label.current.textContent =
          `${partNames[i]?.replace(/_/g, " ") ?? "part"} · ${Math.round(w)} × ${Math.round(h)} × ${Math.round(d)} mm`;
      }
      settled = false;
    }

    /** Everything back: all parts drawn, nothing hidden, model in the middle. */
    function focusAll() {
      faceGeom?.setDrawRange(0, Infinity);
      edgeGeom?.setDrawRange(0, Infinity);
      if (ghost) {
        // the ghost geometries share their attributes with the real ones, so
        // only the geometry objects and materials are disposed, not the data
        for (const ch of ghost.children) {
          const any = ch as unknown as { geometry?: BufferGeometry; material?: Material };
          any.geometry?.deleteAttribute?.("position");
          any.geometry?.dispose();
          any.material?.dispose();
        }
        shell.remove(ghost);
        ghost = null;
      }
      hidden.clear();
      applyExplode(explode * (Math.max(...(lastSize ?? [10])) * 0.45));
      orient.position.set(0, 0, 0);
      if (focused >= 0) target.scale = 1;
      focused = -1;
      if (label.current && lastSize) {
        const [w, h, d] = lastSize;
        label.current.textContent = `${Math.round(w)} × ${Math.round(h)} × ${Math.round(d)} mm`;
      }
      settled = false;
    }

    function showLayer(n: number) {
      if (!pathGeom || !layerEnds.length) return;
      const last = layerEnds.length - 1;
      shownLayer = n < 0 ? -1 : Math.max(0, Math.min(last, n));
      // Built UP, not one layer in isolation: that is what a print looks like as
      // it happens, and a single floating layer tells him nothing about where it
      // sits in the part.
      pathGeom.setDrawRange(0, shownLayer < 0 ? Infinity : layerEnds[shownLayer]);
      const el = scale.current;
      if (el) {
        const n = layerEnds.length;
        const at = shownLayer < 0 ? n : shownLayer + 1;
        el.dataset.on = "true";
        el.style.setProperty("--at", String(at / n));
        const txt = el.firstElementChild as HTMLElement | null;
        if (txt) txt.textContent = shownLayer < 0 ? `ALL ${n}` : `${at} / ${n}`;
      }
      settled = false;
    }

    /** The mesh from before the last edit, in amber, over the new one. */
    async function showBefore(on: boolean) {
      if (!on) {
        before.visible = false;
        settled = false;
        return;
      }
      if (!beforeLoaded) {
        let geo: Geometry;
        try {
          geo = await api("/holo/geometry?version=prev");
        } catch {
          return;
        }
        if (geo.error || !geo.positions_b64) return;
        empty(before);
        const positions = f32(geo.positions_b64);
        const faces = new BufferGeometry();
        faces.setAttribute("position", new Float32BufferAttribute(positions, 3));
        before.add(new Mesh(faces, new MeshBasicMaterial({
          color: AMBER, transparent: true, opacity: 0.05, side: DoubleSide, depthWrite: false,
        })));
        const edgePositions = f32(geo.edge_positions_b64);
        if (edgePositions.length) {
          const lines = new BufferGeometry();
          lines.setAttribute("position", new Float32BufferAttribute(edgePositions, 3));
          before.add(new LineSegments(lines, new LineBasicMaterial({
            color: AMBER, transparent: true, opacity: 0.55,
          })));
        }
        beforeLoaded = true;
      }
      before.visible = true;
      settled = false;
    }

    function buildGrabber(bx: number, by: number, bz: number) {
      empty(grabber);
      const [x, y, z] = [bx / 2, by / 2, bz / 2];
      // A twelfth of the shortest side, so the brackets read as corners of THIS
      // object rather than a fixed-size widget that swamps a 6 mm spacer and
      // vanishes on a 200 mm plate.
      const a = Math.max(1, Math.min(bx, by, bz) / 12);
      const pts: number[] = [];
      for (const sx of [-1, 1]) for (const sy of [-1, 1]) for (const sz of [-1, 1]) {
        const [px, py, pz] = [sx * x, sy * y, sz * z];
        // Three short strokes meeting at the corner — the corner itself, not a
        // full wireframe box. A complete box hides the part inside it.
        pts.push(px, py, pz, px - sx * a, py, pz);
        pts.push(px, py, pz, px, py - sy * a, pz);
        pts.push(px, py, pz, px, py, pz - sz * a);
      }
      const g = new BufferGeometry();
      g.setAttribute("position", new Float32BufferAttribute(pts, 3));
      grabber.add(new LineSegments(g, new LineBasicMaterial({
        color: GREEN, transparent: true, opacity: 0.5,
      })));
      // Re-apply the state: the geometry can finish loading AFTER he has already
      // put his hands up, and a fresh set of brackets built at the default
      // opacity would say "watching" while he was actually holding the thing.
      applyHands.current?.(useStore.getState().holo?.hands ?? "off");
    }

    // Arrow keys scrub. Voice is the precise path and hands are the fast one,
    // but a layer preview that cannot be nudged one layer at a time is missing
    // the gesture every slicer trained him to expect.
    const onKey = (e: KeyboardEvent) => {
      if (!path.visible || !layerEnds.length) return;
      const step = e.key === "ArrowUp" ? 1 : e.key === "ArrowDown" ? -1 : 0;
      if (!step) return;
      e.preventDefault();
      showLayer((shownLayer < 0 ? layerEnds.length - 1 : shownLayer) + step);
    };
    window.addEventListener("keydown", onKey);

    // Armed and holding are different states and must LOOK different: armed says
    // it will respond, holding says it is responding. One appearance for both is
    // how he ends up unsure whether a gesture registered.
    applyHands.current = (state: string) => {
      grabber.visible = state !== "off";
      const m = (grabber.children[0] as LineSegments | undefined)?.material as
        (Material & { opacity: number }) | undefined;
      if (m) m.opacity = state === "holding" ? 1 : 0.5;
      settled = false;
    };

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
        case "spin":
          // The idle drift, asked for by name. It was only ever switched off as
          // a side effect of him steering, and back on by a reset.
          spin = c.on ?? !spin;
          break;
        case "scale":
          target.scale = Math.max(0.2, Math.min(6, target.scale * (c.factor ?? 1.5)));
          break;
        case "section":
          setClip(c.axis ?? "z", c.at ?? 0.5);
          break;
        case "explode":
          explodeTarget = explodeTarget > 0 ? 0 : 1;
          break;
        case "colour":
          // Nothing to switch to on a model with no colours: the cyan IS the
          // answer there, and flickering to a white blob would be worse.
          if (hasColour) paintTrue(c.on ?? !trueColour);
          break;
        case "fit":
          target.scale = 1;
          break;
        case "view": {
          // A named view, from HOME. His z is the renderer's y (see rotate):
          // yaw is ry, pitch is rx. "Top" tips it so the top faces him.
          const q = Math.PI / 2;
          const v = String(c.view ?? "front");
          Object.assign(target, { rx: HOME.rx, ry: HOME.ry, rz: HOME.rz });
          if (v === "top") target.rx = HOME.rx + q;
          else if (v === "bottom") target.rx = HOME.rx - q;
          else if (v === "back") target.ry = HOME.ry + Math.PI;
          else if (v === "left") target.ry = HOME.ry + q;
          else if (v === "right" || v === "side") target.ry = HOME.ry - q;
          else target.ry = 0;        // front: square on, no idle yaw
          spin = false;
          break;
        }
        case "part": {
          const i = c.part ? partNames.indexOf(c.part) : -1;
          if (c.mode === "all" || i < 0) focusAll();
          else if (c.mode === "hide") {
            hidden.add(i);
            if (focused === i) focusAll();
            else applyExplode(explode * (Math.max(...(lastSize ?? [10])) * 0.45));
          } else {
            hidden.delete(i);
            applyExplode(explode * (Math.max(...(lastSize ?? [10])) * 0.45));
            focusOn(i);
          }
          break;
        }
        case "compare":
          void showBefore(c.on ?? !before.visible);
          break;
        case "reset":
          Object.assign(target, HOME);
          clearClip();
          explodeTarget = 0;
          spin = true;
          focusAll();
          before.visible = false;
          break;
        case "layers":
          void applyCheck.current?.(!!c.on);
          break;
        case "layer": {
          // Turn the toolpath on if it is not already, then scrub.
          const go = () => showLayer(
            c.delta != null
              ? (shownLayer < 0 ? layerEnds.length - 1 : shownLayer) + c.delta
              : (c.layer ?? -1));
          // Always through applyCheck: it is a redraw once the G-code is parsed,
          // and going straight to `go()` when the store still thinks the layers
          // are off let the visibility effect switch them back off underneath.
          void Promise.resolve(applyCheck.current?.(true)).then(go);
          break;
        }
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
    applyReload.current = load;
    loadedTs.current = useStore.getState().holo?.ts ?? null;
    void load();
    raf = requestAnimationFrame(tick);
    const ro = new ResizeObserver(size);
    ro.observe(el);

    return () => {
      disposed = true;
      window.removeEventListener("keydown", onKey);
      applyHands.current = null;
      applyCheck.current = null;
      applyCmd.current = null;
      applyReload.current = null;
      cancelAnimationFrame(raf);
      ro.disconnect();
      clear();
      renderer.dispose();
      // dispose() frees the resources but not the CONTEXT. One WebGL context
      // is built per model, and Chrome drops the oldest live context once
      // sixteen exist — which after sixteen distinct models in a session can
      // be the one currently on the stage. Losing it on purpose here is what
      // actually returns it.
      try { renderer.forceContextLoss(); } catch { /* already lost */ }
      if (renderer.domElement.parentNode === el) el.removeChild(renderer.domElement);
    };
  }, [holo?.name]);

  // EACH ROUGH RUNG RE-READS THE MESH AND NOTHING ELSE. The scene effect is
  // keyed on the name, and a progressive carve keeps its name from the first
  // rung to the finished part because it is the same object throughout — so
  // without this a preview would arrive and change nothing. Rebuilding the
  // scene instead would throw away the renderer for a mesh swap and would do it
  // while he might have his hands on the thing; `load` already re-applies the
  // state it needs to, which is why it is safe to call again.
  useEffect(() => {
    if (!holo?.ts || holo.ts === loadedTs.current) return;
    loadedTs.current = holo.ts;
    void applyReload.current?.();
  }, [holo?.ts]);

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

  useEffect(() => {
    applyHands.current?.(holo?.hands ?? "off");
  }, [holo?.hands]);

  return (
    <>
      <div className="holo" style={{position:"relative"}}>
        <div className="holo__head mono-sub">
          <span className="holo__name">{holo?.name ?? "hologram"}</span>
          {holo?.rough ? (
            // Say it plainly. This is a real mesh of the real reconstruction,
            // just carved on a coarser grid — but it is not the part yet, and a
            // preview that does not admit to being one is a lie he would only
            // catch by waiting.
            <span className="holo__rough">resolving · {holo.rough}</span>
          ) : null}
          {project && <span className="holo__project">{project}</span>}
          <span ref={label} className="holo__dims" />
        </div>
        <div ref={host} className="holo__canvas" />
        {holo?.check && <div ref={note} className="holo__check mono-sub" />}
        <div ref={scale} className="holo__layers mono-sub">
          <span className="holo__layers-n" />
        </div>
        {/* Hands ARMED implies the camera is reading — hand_control opens the
            device itself rather than through the set_camera tool, so `cameraOn`
            never heard about it and this never appeared. He asked for the
            camera view precisely so he could see how it reads his hands, so
            hands-on is the case that matters most. */}
        {(cameraOn || (holo?.hands && holo.hands !== "off")) && <HandCam />}
        {holo?.hands && holo.hands !== "off" && (
          <div className="holo__hands mono-sub" data-holding={holo.hands === "holding"}>
            {holo.hands === "holding" ? "HOLDING" : "WATCHING YOUR HANDS"}
          </div>
        )}
      </div>
    </>
  );
}
