// First-run experience: boot sequence overlay (driven by real states/events)
// and a one-time setup card. Spec §59 — must feel premium, never fake progress.
import { useEffect, useRef, useState } from "react";
import { useStore } from "../state/store";
import { api } from "../lib/sidecar";

export function BootOverlay() {
  const state = useStore((s) => s.state);
  const activity = useStore((s) => s.activity);
  const [dismissed, setDismissed] = useState(false);
  const sawBoot = useRef(false);

  if (state === "starting" || state === "offline") sawBoot.current = true;

  // Fade out shortly after we reach a live state, but only if we watched boot.
  useEffect(() => {
    if (sawBoot.current && state !== "starting" && state !== "offline" && !dismissed) {
      const t = setTimeout(() => setDismissed(true), 1400);
      return () => clearTimeout(t);
    }
  }, [state, dismissed]);

  if (dismissed || !sawBoot.current) return null;

  const bootLines = activity
    .filter((a) => a.kind === "boot" || a.kind === "boot_error")
    .map((a) => a.summary);
  const ready = state !== "starting" && state !== "offline";

  return (
    <div className={`boot ${ready ? "boot--done" : ""}`}>
      <div className="boot__logo">J.A.R.V.I.S.</div>
      <div className="boot__lines">
        <div className="boot__line">initializing…</div>
        {bootLines.map((l, i) => (
          <div key={i} className="boot__line">{l}</div>
        ))}
        {ready && <div className="boot__line boot__line--ready">system ready</div>}
      </div>
    </div>
  );
}

export function FirstRunSetup() {
  const [cfg, setCfg] = useState<any>(null);
  const [voices, setVoices] = useState<string[]>([]);
  const [done, setDone] = useState(true); // assume complete until proven otherwise
  const [wakeMode, setWakeMode] = useState("both");
  const [voice, setVoice] = useState("en_GB-alan-medium");

  useEffect(() => {
    (async () => {
      try {
        const [c, v] = await Promise.all([api("/config"), api("/voices")]);
        setCfg(c.config);
        setVoices(v.voices);
        setWakeMode(c.config.wake?.mode ?? "both");
        setVoice(c.config.tts?.voice ?? "en_GB-alan-medium");
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

  return (
    <div className="modal-backdrop">
      <div className="firstrun">
        <div className="firstrun__title">WELCOME</div>
        <p className="firstrun__lead">
          I'm JARVIS. Everything — speech recognition, reasoning, memory, and my
          voice — runs locally on this machine. A few choices before we begin:
        </p>
        <label>How should I listen?
          <select value={wakeMode} onChange={(e) => setWakeMode(e.target.value)}>
            <option value="both">Say "Hey Jarvis" or press Ctrl+Shift+J</option>
            <option value="wake_word">"Hey Jarvis" only</option>
            <option value="push_to_talk">Push to talk only (Ctrl+Shift+J)</option>
          </select>
        </label>
        <label>Voice
          <select value={voice} onChange={(e) => setVoice(e.target.value)}>
            {voices.map((v) => <option key={v} value={v}>{v}</option>)}
          </select>
        </label>
        <p className="firstrun__note">
          Web search stays off until you add a Brave API key in Settings.
          Risky actions always ask before running. You can change everything later.
        </p>
        <div className="firstrun__actions">
          <button className="firstrun__go" onClick={finish}>BEGIN</button>
        </div>
      </div>
    </div>
  );
}
