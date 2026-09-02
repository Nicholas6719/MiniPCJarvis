// The hologram (§ hologram plan, phase A). A model, projected.
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
const GREEN = 0x59e0a5;

type Geometry = {
  positions: number[];
  edge_positions: number[];
  size_mm: number[];
  triangles: number;
  edges: number;
  error?: string;
};

export function HoloStage() {
  const host = useRef<HTMLDivElement>(null);
  const label = useRef<HTMLDivElement>(null);
  const holo = useStore((s) => s.holo);

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

    const group = new Group();
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

    function clear() {
      for (const g of [group, bed]) {
        while (g.children.length) {
          const c = g.children.pop()!;
          const any = c as unknown as { geometry?: BufferGeometry; material?: Material };
          any.geometry?.dispose();
          any.material?.dispose();
        }
      }
    }

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
      group.add(new Mesh(faces, new MeshBasicMaterial({
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
        group.add(halo);
        group.add(new LineSegments(lines, new LineBasicMaterial({
          color: CYAN, transparent: true, opacity: 0.95,
        })));
      }

      // Frame it: the model is already centred by the sidecar, so this only has
      // to choose a distance that fits the largest dimension.
      const [w, h, d] = geo.size_mm;
      const span = Math.max(w, h, d) || 1;
      camera.position.set(0, span * 0.34, span * 2.5);
      camera.lookAt(0, 0, 0);

      // The bed, in true proportion to the part — 220 mm from the slicer profile.
      const grid = new GridHelper(220, 11, CYAN, CYAN);
      const gm = grid.material as Material & { opacity: number; transparent: boolean };
      gm.opacity = 0.14;
      gm.transparent = true;
      grid.position.y = -h / 2 - span * 0.12;
      bed.add(grid);

      if (label.current) {
        label.current.textContent =
          `${Math.round(w)} × ${Math.round(h)} × ${Math.round(d)} mm · ` +
          `${geo.triangles.toLocaleString()} triangles`;
      }
      settled = false;
    }

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
      cancelAnimationFrame(raf);
      ro.disconnect();
      clear();
      renderer.dispose();
      if (renderer.domElement.parentNode === el) el.removeChild(renderer.domElement);
    };
  }, [holo?.name]);

  return (
    <>
      <div className="holo">
        <div className="holo__head mono-sub">
          <span className="holo__name">{holo?.name ?? "hologram"}</span>
          <span ref={label} className="holo__dims" />
        </div>
        <div ref={host} className="holo__canvas" />
      </div>
    </>
  );
}
