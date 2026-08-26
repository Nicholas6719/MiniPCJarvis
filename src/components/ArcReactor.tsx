// The arc reactor — the single most important component (§4 of the handoff).
// Thirteen states; hue = kind of state, ring speed = urgency, charge arc = progress,
// coil deformation = live voice, core brightness = speaking. Nothing is decorative.
import { useEffect, useRef } from "react";
import type { JarvisState } from "../state/store";

// [colour, halo opacity, base spin duration (s), label, sublabel]
export const CORE_SPEC: Record<JarvisState, [string, number, number, string, string]> = {
  offline:     ["#3a4656", 0.06, 9,    "OFFLINE",     "sidecar not responding"],
  starting:    ["#27c7ff", 0.30, 1.1,  "SPINNING UP", "loading model"],
  idle:        ["#27c7ff", 0.34, 6,    "STANDING BY", "ctrl+shift+j to talk"],
  listening:   ["#45ffc8", 0.62, 1.3,  "LISTENING",   "go ahead"],
  processing:  ["#45d7ff", 0.46, 0.8,  "TRANSCRIBING", ""],
  thinking:    ["#7a9bff", 0.58, 0.6,  "THINKING",    "local model"],
  searching:   ["#45d7ff", 0.52, 0.5,  "SEARCHING",   "reading sources"],
  executing:   ["#ffc94d", 0.55, 0.6,  "EXECUTING",   "running tools"],
  waiting:     ["#ffc94d", 0.44, 2.2,  "NEEDS YOU",   "awaiting confirmation"],
  speaking:    ["#27c7ff", 0.76, 0.9,  "SPEAKING",    "interrupt any time"],
  interrupted: ["#ff8a5c", 0.50, 0.4,  "STOPPED",     ""],
  error:       ["#ff5c6a", 0.68, 0.35, "FAULT",       "diagnostics has it"],
  sleeping:    ["#22506b", 0.10, 12,   "ASLEEP",      "say jarvis to wake"],
};

const COIL_ANGLES = [15, 45, 75, 105, 135, 165, 195, 225, 255, 285, 315, 345];
const SPOKE_ANGLES = [0, 120, 240];

const mix = (c: string, pct: number, base = "transparent") =>
  `color-mix(in srgb, ${c} ${pct}%, ${base})`;

interface Props {
  state: JarvisState;
  size: number;      // px, pre-scaled by the caller
  charge?: number;   // 0–100 — literal task progress, never decorative
}

export function ArcReactor({ state, size, charge = 0 }: Props) {
  const root = useRef<HTMLDivElement>(null);

  // Voice reactivity: four smoothed bands written straight onto the element as CSS
  // vars — never through React state, which would re-render the tree 60×/sec.
  // Synthetic envelope for now (tuned in the design pass); swap the `gate` for real
  // mic/TTS RMS when the sidecar streams it. Keep 0.34 attack / 0.06 release / 0.035
  // floor — chosen so "it's hearing me" reads instantly across a room.
  useEffect(() => {
    const t0 = performance.now();
    const smooth = [0, 0, 0, 0];
    let settled = false;
    let raf = 0;
    const wob = (t: number, f: number, p: number) =>
      0.5 + 0.5 * Math.sin(t * f + p) * Math.sin(t * f * 0.41 + p * 1.7);

    const tick = (now: number) => {
      raf = requestAnimationFrame(tick);
      const el = root.current;
      if (!el) return;
      const t = (now - t0) / 1000;
      const hot = state === "listening" || state === "speaking";
      // Speech comes in syllables; listening is breathier and lower.
      const gate = state === "speaking"
        ? 0.30 + 0.70 * Math.max(0, Math.sin(t * 7.4) * 0.7 + Math.sin(t * 11.3) * 0.3)
        : state === "listening" ? 0.18 + 0.52 * wob(t, 3.1, 0.4) : 0;
      const bands = [
        wob(t, 9.3, 0.0) * 1.00,
        wob(t, 6.1, 1.3) * 0.86,
        wob(t, 13.7, 2.1) * 0.68,
        wob(t, 4.3, 3.4) * 0.92,
      ];
      // Once a quiet core has settled at the floor there is nothing to write.
      if (!hot && settled) return;
      let peak = 0, moved = false;
      for (let i = 0; i < 4; i++) {
        const target = hot ? 0.10 + gate * bands[i] : 0.035;
        const next = smooth[i] + (target - smooth[i]) * (hot ? 0.34 : 0.06);
        if (Math.abs(next - smooth[i]) > 0.0004) moved = true;
        smooth[i] = next;
        el.style.setProperty("--a" + (i + 1), smooth[i].toFixed(3));
        if (smooth[i] > peak) peak = smooth[i];
      }
      el.style.setProperty("--amp", peak.toFixed(3));
      settled = !hot && !moved;
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [state]);

  const [c, glow, spin] = CORE_SPEC[state] ?? CORE_SPEC.idle;

  return (
    <div
      ref={root}
      className="arc"
      style={{
        width: size, height: size,
        // @ts-ignore css custom properties
        "--c": c, "--spin": `${spin}s`, "--glow": glow, "--chg": charge,
        "--a1": 0.035, "--a2": 0.035, "--a3": 0.035, "--a4": 0.035, "--amp": 0.035,
      } as React.CSSProperties}
    >
      {/* 1 · outer halo */}
      <div className="arc__halo" />
      {/* 2 · housing well */}
      <div style={{
        position: "absolute", inset: "3%", borderRadius: "50%",
        background: "radial-gradient(circle at 50% 42%, #0a1c28 0%, #030c14 62%, #020709 100%)",
        boxShadow: "inset 0 0 30px rgba(0,0,0,.8)",
      }} />
      {/* 3 · fine tick ring */}
      <div style={{
        position: "absolute", inset: 0, borderRadius: "50%",
        background: `repeating-conic-gradient(from 0deg, ${mix(c, 80)} 0 .6deg, transparent .6deg 5deg)`,
        WebkitMaskImage: "radial-gradient(closest-side, transparent 94%, #000 95.5%)",
        maskImage: "radial-gradient(closest-side, transparent 94%, #000 95.5%)",
        opacity: 0.7, animation: `arcSpin calc(var(--spin) * 9) linear infinite`,
      }} />
      {/* 4 · bezel ring */}
      <div style={{ position: "absolute", inset: "3%", borderRadius: "50%", border: `1px solid ${mix(c, 34)}` }} />
      {/* 5 · twelve outer plates */}
      <div style={{
        position: "absolute", inset: "4.5%", borderRadius: "50%",
        background: `repeating-conic-gradient(from 7.5deg, ${mix(c, 34)} 0 20deg, transparent 20deg 30deg)`,
        WebkitMaskImage: "radial-gradient(closest-side, transparent 77%, #000 79%)",
        maskImage: "radial-gradient(closest-side, transparent 77%, #000 79%)",
        animation: `arcSpin calc(var(--spin) * 3) linear infinite`,
      }} />
      {/* 6 · inner ring */}
      <div style={{ position: "absolute", inset: "22%", borderRadius: "50%", border: `1px solid ${mix(c, 26)}` }} />
      {/* 7 · charge arc — literal progress */}
      <div style={{
        position: "absolute", inset: "14%", borderRadius: "50%",
        background: `conic-gradient(from -90deg, var(--c) 0 calc(var(--chg) * 1%), transparent calc(var(--chg) * 1%))`,
        WebkitMaskImage: "radial-gradient(closest-side, transparent 91%, #000 93%)",
        maskImage: "radial-gradient(closest-side, transparent 91%, #000 93%)",
        filter: `drop-shadow(0 0 7px ${c})`,
      }} />
      {/* 8 · twelve voice coils */}
      {COIL_ANGLES.map((deg, i) => {
        const band = (i % 4) + 1;
        return (
          <div key={deg} style={{
            position: "absolute", width: "2.5%", height: "7%", background: c, borderRadius: 1,
            transform: `rotate(${deg}deg) translateY(-460%) scaleY(var(--a${band}))`,
            opacity: `calc(.18 + var(--a${band}) * .82)`,
            boxShadow: `0 0 7px ${c}`,
          }} />
        );
      })}
      {/* 9 · eight coil plates */}
      <div style={{
        position: "absolute", inset: "23%", borderRadius: "50%",
        background: `repeating-conic-gradient(from 22.5deg, ${mix(c, 95)} 0 32deg, transparent 32deg 45deg)`,
        WebkitMaskImage: "radial-gradient(closest-side, transparent 70%, #000 72%)",
        maskImage: "radial-gradient(closest-side, transparent 70%, #000 72%)",
        filter: `drop-shadow(0 0 5px ${mix(c, 50)})`,
        animation: `arcSpin calc(var(--spin) * 5) linear infinite reverse`,
      }} />
      {/* 10 · three spokes */}
      {SPOKE_ANGLES.map((deg) => (
        <div key={deg} style={{
          position: "absolute", width: 2, height: "50%", top: 0, left: "calc(50% - 1px)",
          transformOrigin: "50% 100%", transform: `rotate(${deg}deg)`,
          background: `linear-gradient(180deg, transparent 0 4%, ${mix(c, 75)} 4% 40%, transparent 40%)`,
        }} />
      ))}
      {/* 11 · inner bezel */}
      <div style={{
        position: "absolute", inset: "36%", borderRadius: "50%", border: `2px solid ${c}`,
        boxShadow: `inset 0 0 20px ${mix(c, 40)}, 0 0 16px ${mix(c, 28)}`,
        background: `radial-gradient(circle at 50% 50%, ${mix(c, 16, "#02080d")} 0%, #01060a 100%)`,
      }} />
      {/* 12 · centre bloom */}
      <div style={{
        position: "absolute", inset: "40%", borderRadius: "50%",
        background: `radial-gradient(circle at 50% 58%, ${mix(c, 55)} 0%, transparent 72%)`,
        opacity: "calc(.55 + var(--amp) * .75)", pointerEvents: "none",
      }} />
      {/* 13 · the triangle — over soft bloom. No disc (explicitly rejected). */}
      <div style={{
        position: "absolute", inset: "40.5%",
        clipPath: "polygon(50% 4%, 97% 88%, 3% 88%)",
        background: `linear-gradient(172deg, #ffffff 0%, ${mix(c, 55, "white")} 26%, ${c} 64%, ${mix(c, 72, "#02121a")} 100%)`,
        filter: `drop-shadow(0 0 16px ${mix(c, 85)}) drop-shadow(0 0 40px ${mix(c, 45)})`,
        animation: `arcBreathe calc(var(--spin) * 4) ease-in-out infinite`,
      }} />
    </div>
  );
}
