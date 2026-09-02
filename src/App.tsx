// The frame (§5): two geometries, chosen by state. Radial — the core is the
// subject, centred. Anchor — the core slides left and a stage opens beside it.
// The core carries the layout with it; nothing cross-fades in place.
import { useEffect, useMemo, useState } from "react";
import { ArcReactor, CORE_SPEC } from "./components/ArcReactor";
import { Stage } from "./components/Stage";
import { ConfirmationGate, FaultWedges } from "./components/Wedges";
import { BootOverlay, FirstRunSetup } from "./components/FirstRun";
import { useStore, JarvisState, setHoldBase } from "./state/store";
import { connectEvents, api } from "./lib/sidecar";

// Radial states (§5): the machine turning to face you. Everything else anchors.
const RADIAL: Set<JarvisState> = new Set([
  "offline", "starting", "listening", "waiting", "error", "sleeping",
]);

// Per-state room intensity (§ Atmosphere) and chrome opacity (§ Chrome).
function airFor(state: JarvisState): number {
  if (state === "idle" || state === "sleeping" || state === "offline") return 0.5;
  if (state === "listening") return 0.8;
  if (state === "speaking") return 1.15;
  if (state === "error" || state === "waiting") return 1.1;
  return 1;
}
function chromeFor(state: JarvisState): number {
  if (state === "idle" || state === "sleeping" || state === "offline") return 0.32;
  if (state === "listening") return 0.6;
  return 1;
}

function useClock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 15000);
    return () => clearInterval(t);
  }, []);
  return now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}

// One scaling rule, two clauses, no breakpoints (§10).
function useScale() {
  const [s, setS] = useState(1);
  const [compact, setCompact] = useState(false);
  useEffect(() => {
    const apply = () => {
      const raw = Math.min(window.innerWidth / 1920, window.innerHeight / 1080);
      const scale = Math.min(2.0, Math.max(0.85, raw));
      setS(scale);
      setCompact(window.innerWidth < 1600 || window.innerHeight < 900);
      document.documentElement.style.setProperty("--s", String(scale));
      document.body.classList.toggle("compact", window.innerWidth < 1600 || window.innerHeight < 900);
    };
    apply();
    window.addEventListener("resize", apply);
    return () => window.removeEventListener("resize", apply);
  }, []);
  return { s, compact };
}

export default function App() {
  const state = useStore((s) => s.state);
  const stage = useStore((s) => s.stage);
  const confirmation = useStore((s) => s.confirmation);
  const web = useStore((s) => s.web);
  const wakeMode = useStore((s) => s.wakeMode);
  const armedUntil = useStore((s) => s.armedUntil);
  const configVersion = useStore((s) => s.configVersion);
  const onEvent = useStore((s) => s.onEvent);
  const clock = useClock();
  const { s } = useScale();

  useEffect(() => connectEvents(onEvent), [onEvent]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") useStore.getState().dismissStage();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Don't animate a window nobody is looking at.
  //
  // HIDDEN, not merely unfocused. `is-hidden` pauses every animation on the page
  // (styles.css: `body.is-hidden * { animation-play-state: paused }`), and this
  // used to set it on `blur` - so alt-tabbing to another app while the HUD sat
  // fully visible on a second monitor froze the orb mid-spin, in the middle of
  // JARVIS speaking. Minimising still pauses it, because minimising sets
  // document.hidden. The old listeners also leaked: only one of the three was
  // ever removed.
  useEffect(() => {
    const sync = () => document.body.classList.toggle("is-hidden", document.hidden);
    sync();
    document.addEventListener("visibilitychange", sync);
    return () => document.removeEventListener("visibilitychange", sync);
  }, []);

  // wake mode + history hydrate
  useEffect(() => {
    (async () => {
      try {
        const r = await api("/config");
        useStore.getState().setWakeMode(r.config?.wake?.mode ?? "push_to_talk");
        setHoldBase(r.config?.ui?.panel_hold_s ?? 5);
      } catch {}
    })();
  }, [configVersion, state === "idle"]);
  useEffect(() => {
    (async () => {
      try {
        const r = await api("/transcript");
        useStore.getState().hydrateTranscript(r.transcript);
      } catch {}
    })();
  }, []);

  // ---- derived frame values -------------------------------------------------
  const gateOpen = confirmation != null;
  const faultOpen = state === "error";
  // The state picks the geometry; a held stage keeps the anchor while idle.
  //
  // The camera stage outranks the state. His ask, verbatim: "when the camera
  // is active, I don't want it to leave... when he goes back to listening, I
  // want him to be listening in the side panel HUD-ready state." Listening,
  // thinking and speaking used to flip the frame radial, which faded the live
  // feed out mid-conversation. A confirmation gate or a fault still takes the
  // whole room - those are the moments nothing may compete with.
  // A hologram holds the frame for exactly the reason the camera does: it is a
  // thing he is WORKING ON, not an answer that has finished being useful. Left
  // out of this list the stage opened correctly and was then never drawn, because
  // every RADIAL state — sleeping, listening, waiting — outranks a stage. The
  // event arrived, the store updated, and nothing appeared.
  const cameraHeld = stage?.kind === "camera";
  const holoHeld = stage?.kind === "holo";
  const geometry: "radial" | "anchor" =
    gateOpen || faultOpen ? "radial"
    : cameraHeld || holoHeld ? "anchor"
    : RADIAL.has(state) ? "radial"
    : stage ? "anchor"
    : state === "idle" ? "radial"
    : "anchor";

  const rim = gateOpen ? "#ffc94d" : (CORE_SPEC[state] ?? CORE_SPEC.idle)[0];
  const [, , , word, sub] = CORE_SPEC[state] ?? CORE_SPEC.idle;
  const air = airFor(state);
  const chr = chromeFor(state);
  const anchored = geometry === "anchor";

  // Core scale (§5.2/5.3): rest 1.45 · listening 1.3 · faults + gate 1.0 · anchor 0.85–0.95
  const coreScale = anchored
    ? state === "speaking" ? 0.95 : 0.85
    : gateOpen || faultOpen ? 1.0
    : state === "listening" ? 1.3
    : 1.45;

  // Charge arc = literal progress (real source reads), never decorative.
  const charge = useMemo(() => {
    const total = web?.results?.length ?? 0;
    if (!total || !stage) return 0;
    const read = (web?.results ?? []).filter((r) => web?.read[r.url]).length;
    if (web?.stage === "done") return 100;
    return Math.round((read / total) * 100);
  }, [web, stage]);

  const armed = state === "idle" && armedUntil > Date.now() / 1000;
  const radialWord = gateOpen ? "NEEDS YOU" : armed ? "CONVERSATION" : word;
  const radialSub = gateOpen ? "nothing has happened yet"
    : armed ? "listening · no wake word needed"
    : state === "idle" && wakeMode === "wake_word" ? 'say "hey jarvis"'
    : state === "idle" && wakeMode === "both" ? '"hey jarvis" · or ctrl+shift+j'
    : sub;

  const micClick = async () => {
    try { await api("/listen/toggle", { method: "POST" }); } catch {}
  };

  const showWedges = gateOpen || faultOpen;

  return (
    <div
      className={`frame frame--${geometry}`}
      style={{
        // @ts-ignore custom properties drive the whole room (§11)
        "--rim": rim, "--air": air, "--chr": chr,
        "--rad": geometry === "radial" ? 1 : 0,
        "--col": anchored ? 1 : 0,
      } as React.CSSProperties}
    >
      {/* atmosphere — behind everything (z 0) */}
      <div className="atmo atmo--vignette" />
      <div className="atmo atmo--grain" />
      <div className="atmo atmo--scanline" />

      {/* ambient bloom follows the core */}
      <div className="bloom" style={{ left: anchored ? `calc(380px * var(--s))` : "50%" }} />

      {/* orbital rings — radial only */}
      <div className="rail rail--outer" />
      <div className="rail rail--inner" />

      {/* chrome: corner ticks, wordmark, clock — re-anchored to the real viewport */}
      <div className="tick tick--tl-h" /><div className="tick tick--tl-v" />
      <div className="tick tick--tr-h" /><div className="tick tick--tr-v" />
      <div className="tick tick--bl-h" /><div className="tick tick--bl-v" />
      <div className="tick tick--br-h" /><div className="tick tick--br-v" />
      <button className="wordmark" title="Settings"
              onClick={() => useStore.getState().setSettingsSection("voice")}>JARVIS</button>
      <div className="clock">{clock}</div>

      {/* the core — it carries the layout with it (§5.1) */}
      <button
        className="coreslot"
        onClick={micClick}
        title="Toggle listening (Ctrl+Shift+J)"
        style={{
          left: anchored ? `calc(380px * var(--s))` : "50%",
          transform: `translate(-50%, -50%) scale(${coreScale})`,
        }}
      >
        <ArcReactor state={gateOpen && state !== "waiting" ? "waiting" : state} size={380 * s} charge={charge} />
      </button>

      {/* radial state block + rest hint */}
      <div className="radial__state">
        <div className="radial__word">{radialWord}</div>
        <div className="radial__sub mono-sub">{radialSub}</div>
      </div>
      {state === "idle" && !stage && !gateOpen && (
        <div className="radial__hint mono-sub">SAY "HEY JARVIS" OR PRESS CTRL+SHIFT+J</div>
      )}

      {/* wedges: faults and the gate — radial only */}
      {gateOpen && <ConfirmationGate />}
      {faultOpen && !gateOpen && <FaultWedges />}

      {/* column divider — the core casting light on the stage */}
      <div className="column" />

      {/* the stage */}
      <div className="stage">
        {!showWedges && <Stage />}
      </div>

      <FirstRunSetup />
      <BootOverlay />
    </div>
  );
}
