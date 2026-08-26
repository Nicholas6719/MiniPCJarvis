// Boot (§ JarvisBoot) and first run (§ JarvisFirstRun) — every line a real event.
import { useEffect, useRef, useState } from "react";
import { useStore } from "../state/store";
import { api } from "../lib/sidecar";
import { ArcReactor } from "./ArcReactor";

const BOOT_STEPS: { key: string; label: string; match: RegExp }[] = [
  { key: "sidecar", label: "SIDECAR", match: /initializing|subsystem/i },
  { key: "stt", label: "SPEECH IN", match: /stt|parakeet|whisper|speech recognition|microphone/i },
  { key: "tts", label: "SPEECH OUT", match: /tts|kokoro|piper|voice/i },
  { key: "llm", label: "LANGUAGE MODEL", match: /model|llama|llm|engine/i },
  { key: "rest", label: "MEMORY · SKILLS", match: /memory|brain|skills|ready/i },
];

export function BootOverlay() {
  const state = useStore((s) => s.state);
  const activity = useStore((s) => s.activity);
  const [dismissed, setDismissed] = useState(false);
  const sawBoot = useRef(false);

  if (state === "starting" || state === "offline") sawBoot.current = true;

  useEffect(() => {
    if (sawBoot.current && state !== "starting" && state !== "offline" && !dismissed) {
      const t = setTimeout(() => setDismissed(true), 1400);
      return () => clearTimeout(t);
    }
  }, [state, dismissed]);

  if (dismissed || !sawBoot.current) return null;

  const bootLines = activity.filter((a) => a.kind === "boot" || a.kind === "boot_error");
  const ready = state !== "starting" && state !== "offline";
  const doneKeys = new Set<string>();
  for (const line of bootLines) {
    for (const s of BOOT_STEPS) if (s.match.test(line.summary)) doneKeys.add(s.key);
  }
  if (ready) BOOT_STEPS.forEach((s) => doneKeys.add(s.key));
  const activeIdx = BOOT_STEPS.findIndex((s) => !doneKeys.has(s.key));
  const pct = Math.round((doneKeys.size / BOOT_STEPS.length) * 100);

  return (
    <div className={`boot ${ready ? "boot--done" : ""}`}>
      <div className="boot__ticks" />
      <div className="boot__core"><ArcReactor state={ready ? "idle" : "starting"} size={300} charge={pct} /></div>
      <div className="boot__logo">JARVIS</div>
      <div className="boot__pct mono-sub">{ready ? "READY" : `SPINNING UP · ${pct}%`}</div>
      <div className="boot__steps">
        {BOOT_STEPS.map((s, i) => {
          const done = doneKeys.has(s.key);
          const active = i === activeIdx && !ready;
          return (
            <div key={s.key} className="boot__step" style={{ opacity: done || active ? 1 : 0.45 }}>
              <span className={`dot ${done ? "dot--green" : active ? "dot--rim flick" : "dot--hollow"}`} />
              <span className="boot__steplabel">{s.label}</span>
              <span className="boot__stepbar">
                <span style={{ width: done ? "100%" : active ? "60%" : "0%", background: done ? "#59e0a5" : "#27c7ff" }} />
              </span>
              <span className="boot__stepstate mono-sub">{done ? "ready" : active ? "loading" : "queued"}</span>
            </div>
          );
        })}
      </div>
      <div className="boot__note mono-sub">EVERY LINE IS A REAL EVENT FROM THE SIDECAR · NOTHING IS SIMULATED</div>
    </div>
  );
}

// ------------------------------------------------------------------- first run

const LISTEN_MODES = [
  { id: "both", title: "Wake word, or the hotkey", sub: 'SAY "HEY JARVIS" · OR CTRL+SHIFT+J', badge: "RECOMMENDED" },
  { id: "wake_word", title: "Wake word only", sub: "HANDS NEVER TOUCH THE KEYBOARD" },
  { id: "push_to_talk", title: "Hotkey only", sub: "THE MIC STAYS SHUT UNTIL YOU ASK" },
];

export function FirstRunSetup() {
  const [cfg, setCfg] = useState<any>(null);
  const [voices, setVoices] = useState<string[]>([]);
  const [done, setDone] = useState(true);
  const [wakeMode, setWakeMode] = useState("both");
  const [voice, setVoice] = useState("");
  const [showAll, setShowAll] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const [c, v] = await Promise.all([api("/config"), api("/voices")]);
        setCfg(c.config);
        setVoices(v.voices ?? []);
        setWakeMode(c.config.wake?.mode ?? "both");
        setVoice(c.config.tts?.voice ?? (v.voices?.[0] ?? ""));
        setDone(Boolean(c.config.general?.first_run_complete));
      } catch {}
    })();
  }, []);

  if (done || !cfg) return null;

  const finish = async () => {
    try {
      await api("/config", {
        method: "PATCH",
        body: JSON.stringify({
          general: { first_run_complete: true },
          wake: { mode: wakeMode },
          tts: { voice },
        }),
      });
    } catch {}
    setDone(true);
  };

  const preview = async (v: string) => {
    setVoice(v);
    try { await api("/voices/preview", { method: "POST", body: JSON.stringify({ voice: v }) }); } catch {}
  };

  const shownVoices = showAll ? voices : voices.slice(0, 3);
  const hour = new Date().getHours();
  const greeting = hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";

  return (
    <div className="firstrun">
      <div className="firstrun__bloom" />
      <div className="firstrun__left">
        <ArcReactor state="idle" size={340} />
        <div className="firstrun__brand">
          <span className="firstrun__wordmark">JARVIS</span>
          <span className="mono-sub">SPEECH · REASONING · MEMORY · VOICE<br />ALL LOCAL TO THIS MACHINE</span>
        </div>
      </div>
      <div className="firstrun__right">
        <p className="firstrun__lead">{greeting}. Two things and I'm yours.</p>

        <div className="firstrun__q mono-sub">HOW SHOULD I LISTEN?</div>
        <div className="firstrun__modes">
          {LISTEN_MODES.map((m) => (
            <div key={m.id} className={`fropt ${wakeMode === m.id ? "is-active" : ""}`} onClick={() => setWakeMode(m.id)}>
              <span className={`fropt__radio ${wakeMode === m.id ? "is-on" : ""}`} />
              <span className="fropt__body">
                {m.title}
                <span className="fropt__sub mono-sub">{m.sub}</span>
              </span>
              {m.badge && <span className="fropt__badge mono-sub">{m.badge}</span>}
            </div>
          ))}
        </div>

        <div className="firstrun__q mono-sub">AND MY VOICE</div>
        <div className="firstrun__voices">
          {shownVoices.map((v) => (
            <div key={v} className={`frvoice ${voice === v ? "is-active" : ""}`} onClick={() => preview(v)}>
              <div className="frvoice__name">{prettyVoice(v)}</div>
              <div className="frvoice__sub mono-sub">{voiceMeta(v)}</div>
            </div>
          ))}
          {!showAll && voices.length > 3 && (
            <div className="frvoice frvoice--more" onClick={() => setShowAll(true)}>
              <span className="mono-sub">+ {voices.length - 3} MORE</span>
            </div>
          )}
        </div>

        <div className="firstrun__footer">
          <span className="firstrun__note mono-sub">
            Anything risky asks first, every time. All of it changes later in settings.
          </span>
          <button className="firstrun__begin" onClick={finish}>BEGIN</button>
        </div>
      </div>
    </div>
  );
}

function prettyVoice(v: string) {
  const name = v.replace(/^en_[A-Z]{2}-/, "").replace(/-\w+$/, "").replace(/^[abm][fm]_/, "");
  return name.charAt(0).toUpperCase() + name.slice(1);
}
function voiceMeta(v: string) {
  const gb = /en_GB|bf_|bm_/.test(v);
  return `${gb ? "EN-GB" : "EN-US"} · ${/f_|female/i.test(v) ? "WARM" : "DRY, CLIPPED"}`;
}
