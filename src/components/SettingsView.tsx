import { useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { enable as autostartEnable, disable as autostartDisable, isEnabled as autostartIsEnabled } from "@tauri-apps/plugin-autostart";
import { api } from "../lib/sidecar";

interface Devices {
  input: { id: number; name: string }[];
  output: { id: number; name: string }[];
}

export function SettingsView() {
  const [cfg, setCfg] = useState<any>(null);
  const [devices, setDevices] = useState<Devices | null>(null);
  const [voiceList, setVoiceList] = useState<string[]>([]);
  const [modelList, setModelList] = useState<string[]>([]);
  const [braveKey, setBraveKey] = useState("");
  const [braveSet, setBraveSet] = useState<boolean | null>(null);
  const [autostart, setAutostart] = useState<boolean | null>(null);
  const [status, setStatus] = useState("");

  useEffect(() => {
    (async () => {
      try {
        const [c, d, v, m] = await Promise.all([
          api("/config"), api("/audio/devices"), api("/voices"), api("/models"),
        ]);
        setCfg(c.config);
        setDevices(d);
        setVoiceList(v.voices);
        setModelList(m.models);
      } catch {
        setStatus("settings unavailable — sidecar not reachable");
      }
      try {
        setBraveSet(await invoke<boolean>("has_secret", { name: "brave_api_key" }));
      } catch { setBraveSet(null); }
      try {
        setAutostart(await autostartIsEnabled());
      } catch { setAutostart(null); }
    })();
  }, []);

  const patch = async (partial: any) => {
    try {
      const r = await api("/config", { method: "PATCH", body: JSON.stringify(partial) });
      setCfg((c: any) => deepMerge(c, partial));
      setStatus(r.applied?.length ? `applied: ${r.applied.join(", ")}` : "saved");
      setTimeout(() => setStatus(""), 3000);
    } catch {
      setStatus("failed to save");
    }
  };

  const preview = async (voice?: string) => {
    try {
      const r = await api("/voices/preview", { method: "POST", body: JSON.stringify({ voice }) });
      if (!r.ok) setStatus(r.error === "busy" ? "JARVIS is busy — try again in a moment" : `preview failed: ${r.error}`);
    } catch { setStatus("preview unavailable"); }
  };

  const saveBraveKey = async () => {
    if (!braveKey.trim()) return;
    try {
      await invoke("set_secret", { name: "brave_api_key", value: braveKey.trim() });
      setBraveKey("");
      setBraveSet(true);
      setStatus("Brave API key stored in Windows Credential Manager");
      setTimeout(() => setStatus(""), 4000);
    } catch {
      setStatus("credential storage unavailable (dev mode?)");
    }
  };

  const toggleAutostart = async () => {
    try {
      if (autostart) { await autostartDisable(); setAutostart(false); }
      else { await autostartEnable(); setAutostart(true); }
    } catch { setStatus("autostart unavailable (dev mode?)"); }
  };

  if (!cfg) return <div className="settings"><span className="panel-title">SETTINGS</span><div className="memory__empty">{status || "loading…"}</div></div>;

  return (
    <div className="settings">
      <span className="panel-title">SETTINGS</span>
      {status && <div className="settings__status">{status}</div>}

      <section className="settings__group">
        <h3>VOICE ACTIVATION</h3>
        <label>Mode
          <select value={cfg.wake?.mode ?? "push_to_talk"}
                  onChange={(e) => patch({ wake: { mode: e.target.value } })}>
            <option value="push_to_talk">Push to talk (Ctrl+Shift+J)</option>
            <option value="wake_word">Wake word — "Hey Jarvis"</option>
            <option value="both">Both</option>
          </select>
        </label>
        <label>Wake sensitivity
          <input type="range" min={0.3} max={0.8} step={0.05}
                 value={cfg.wake?.threshold ?? 0.45}
                 onChange={(e) => patch({ wake: { threshold: Number(e.target.value) } })} />
          <span className="settings__val">{(cfg.wake?.threshold ?? 0.45).toFixed(2)}</span>
        </label>
        <label>Follow-up window (seconds without repeating the wake word)
          <input type="range" min={0} max={20} step={1}
                 value={cfg.conversation?.window_s ?? 8}
                 onChange={(e) => patch({ conversation: { window_s: Number(e.target.value) } })} />
          <span className="settings__val">{cfg.conversation?.window_s ?? 8}s</span>
        </label>
        <label>Interrupting JARVIS while he speaks
          <select value={cfg.interrupt?.mode ?? "wake_word"}
                  onChange={(e) => patch({ interrupt: { mode: e.target.value } })}>
            <option value="wake_word">Only his name stops him (safe with speakers)</option>
            <option value="any_speech">Any speech stops him (headset / isolated mic)</option>
          </select>
        </label>
        <label className="settings__row settings__row--toggle">
          <span>Sound cues (wake chime, boot, attention tone)</span>
          <button className={`toggle ${cfg.audio?.sound_cues !== false ? "toggle--on" : ""}`}
                  onClick={() => patch({ audio: { sound_cues: !(cfg.audio?.sound_cues !== false) } })}>
            <span className="toggle__knob" />
          </button>
        </label>
        <label className="settings__row settings__row--toggle">
          <span>Speak while thinking ("Let me see.", "Searching.") so replies feel instant</span>
          <button className={`toggle ${cfg.speech?.fillers !== false ? "toggle--on" : ""}`}
                  onClick={() => patch({ speech: { fillers: !(cfg.speech?.fillers !== false) } })}>
            <span className="toggle__knob" />
          </button>
        </label>
        <label className="settings__row settings__row--toggle">
          <span>Brain reflexes (answer known requests instantly without the AI model)</span>
          <button className={`toggle ${cfg.brain?.enabled !== false ? "toggle--on" : ""}`}
                  onClick={() => patch({ brain: { enabled: !(cfg.brain?.enabled !== false) } })}>
            <span className="toggle__knob" />
          </button>
        </label>
      </section>

      <section className="settings__group">
        <h3>AUDIO</h3>
        <label>Microphone
          <select value={cfg.audio?.input_device ?? ""}
                  onChange={(e) => patch({ audio: { input_device: e.target.value === "" ? null : Number(e.target.value) } })}>
            <option value="">System default</option>
            {devices?.input.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
          </select>
        </label>
        <label>Speaker
          <select value={cfg.audio?.output_device ?? ""}
                  onChange={(e) => patch({ audio: { output_device: e.target.value === "" ? null : Number(e.target.value) } })}>
            <option value="">System default</option>
            {devices?.output.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
          </select>
        </label>
        <label>Voice
          <div className="settings__row">
            <select value={cfg.tts?.voice ?? ""}
                    onChange={(e) => { patch({ tts: { voice: e.target.value } }); preview(e.target.value); }}>
              {voiceList.map((v) => <option key={v} value={v}>{v}</option>)}
            </select>
            <button className="ghost-btn" onClick={() => preview(cfg.tts?.voice)}>▶ PREVIEW</button>
          </div>
        </label>
      </section>

      <section className="settings__group">
        <h3>INTELLIGENCE</h3>
        <label>Model
          <select value={cfg.llm?.active_model ?? ""}
                  onChange={(e) => patch({ llm: { active_model: e.target.value } })}>
            {modelList.map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
        </label>
      </section>

      <section className="settings__group">
        <h3>WEB SEARCH</h3>
        <label>Brave Search API key {braveSet === true && <span className="settings__ok">● configured</span>}
          <div className="settings__row">
            <input type="password" placeholder={braveSet ? "Replace key…" : "Paste API key…"}
                   value={braveKey} onChange={(e) => setBraveKey(e.target.value)} />
            <button className="ghost-btn" onClick={saveBraveKey}>SAVE</button>
          </div>
        </label>
      </section>

      <section className="settings__group">
        <h3>PROACTIVE ASSISTANCE</h3>
        <label className="settings__row settings__row--toggle">
          <span>Let JARVIS speak up on its own (disk, memory, break reminders)</span>
          <button className={`toggle ${cfg.proactive?.enabled ? "toggle--on" : ""}`}
                  onClick={() => patch({ proactive: { enabled: !cfg.proactive?.enabled } })}>
            <span className="toggle__knob" />
          </button>
        </label>
        <div className="settings__row">
          <label style={{ flex: 1 }}>Quiet hours start
            <input type="text" value={cfg.proactive?.quiet_start ?? "22:00"}
                   onChange={(e) => patch({ proactive: { quiet_start: e.target.value } })} />
          </label>
          <label style={{ flex: 1 }}>Quiet hours end
            <input type="text" value={cfg.proactive?.quiet_end ?? "08:00"}
                   onChange={(e) => patch({ proactive: { quiet_end: e.target.value } })} />
          </label>
        </div>
      </section>

      <section className="settings__group">
        <h3>SYSTEM</h3>
        <label className="settings__row settings__row--toggle">
          <span>Start JARVIS with Windows</span>
          <button className={`toggle ${autostart ? "toggle--on" : ""}`}
                  onClick={toggleAutostart} disabled={autostart === null}>
            <span className="toggle__knob" />
          </button>
        </label>
      </section>
    </div>
  );
}

function deepMerge(a: any, b: any): any {
  const out = { ...a };
  for (const k of Object.keys(b ?? {})) {
    out[k] = b[k] && typeof b[k] === "object" && !Array.isArray(b[k])
      ? deepMerge(a?.[k] ?? {}, b[k])
      : b[k];
  }
  return out;
}
