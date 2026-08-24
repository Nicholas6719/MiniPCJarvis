// SYSTEM: volume, battery, network, load, power — Windows Settings without leaving JARVIS.
import { useEffect, useState } from "react";
import { api } from "../lib/sidecar";

interface Snap {
  stats: { cpu_percent: number; ram_used_gb: number; ram_total_gb: number; ram_percent: number; disk_c_free_gb: number; disk_c_percent: number; process_count: number };
  volume: { volume_percent: number | null; muted: boolean | null };
  mic: string | null;
  battery: { percent: number; plugged: boolean } | null;
  network: { ssid: string | null; signal: number | null; state: string; ip: string | null; sent_mb: number; recv_mb: number };
  uptime_s: number;
  processes: { name: string; cpu: number; mem_mb: number }[];
}

function uptime(s: number): string {
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return `${d ? d + "d " : ""}${h}h ${m}m`;
}

function Gauge({ label, value, detail }: { label: string; value: number; detail: string }) {
  return (
    <div className="sys__gauge">
      <div className="sys__gaugehead"><span>{label}</span><span>{Math.round(value)}%</span></div>
      <div className="sys__bar"><div className="sys__fill" style={{ width: `${Math.min(100, value)}%` }} /></div>
      <div className="sys__detail">{detail}</div>
    </div>
  );
}

export function SystemView() {
  const [s, setS] = useState<Snap | null>(null);
  const [vol, setVol] = useState<number>(50);
  const [busy, setBusy] = useState(false);

  const load = async () => {
    try { const r = await api("/system"); setS(r); if (r.volume?.volume_percent != null) setVol(r.volume.volume_percent); } catch {}
  };
  useEffect(() => { load(); const t = setInterval(() => { if (!document.hidden) load(); }, 5000); return () => clearInterval(t); }, []);

  const post = async (path: string, body: any) => {
    setBusy(true);
    try { await api(path, { method: "POST", body: JSON.stringify(body) }); } catch {}
    setBusy(false); load();
  };
  const power = (action: string) => {
    const label = { lock: "Lock the computer", sleep: "Put the computer to sleep", restart: "Restart the computer", shutdown: "Shut down the computer" }[action as "lock"];
    if (action === "lock" || confirm(`${label}?`)) post("/system/power", { action });
  };

  if (!s) return <div className="sys"><span className="panel-title">SYSTEM</span><div className="memory__empty">reading…</div></div>;
  return (
    <div className="sys">
      <div className="sys__head"><span className="panel-title">SYSTEM</span><span className="files__count">up {uptime(s.uptime_s)} · {s.stats.process_count} processes</span></div>
      <div className="sys__grid">
        <div className="sys__card">
          <div className="sys__cardtitle">SOUND</div>
          <div className="sys__row">
            <input type="range" min={0} max={100} value={vol} onChange={(e) => setVol(Number(e.target.value))}
                   onMouseUp={() => post("/system/volume", { percent: vol })} onTouchEnd={() => post("/system/volume", { percent: vol })} className="sys__slider" />
            <span className="sys__val">{vol}%</span>
            <button className={`ghost-btn ${s.volume.muted ? "is-on" : ""}`} onClick={() => post("/system/mute", { muted: !s.volume.muted })}>{s.volume.muted ? "UNMUTE" : "MUTE"}</button>
          </div>
          <div className="sys__detail">mic: {s.mic ?? "—"}</div>
        </div>
        <div className="sys__card">
          <div className="sys__cardtitle">NETWORK</div>
          <div className="sys__big">{s.network.ssid ?? (s.network.ip ? "Wired" : "Offline")}</div>
          <div className="sys__detail">{s.network.ip ?? "no address"}{s.network.signal != null ? ` · signal ${s.network.signal}%` : ""}</div>
          <div className="sys__detail">↑ {s.network.sent_mb} MB · ↓ {s.network.recv_mb} MB this session</div>
        </div>
        <div className="sys__card">
          <div className="sys__cardtitle">POWER</div>
          <div className="sys__big">{s.battery ? `${s.battery.percent}%${s.battery.plugged ? " ⚡" : ""}` : "Plugged in"}</div>
          <div className="sys__row sys__power">
            <button className="ghost-btn" disabled={busy} onClick={() => power("lock")}>LOCK</button>
            <button className="ghost-btn" disabled={busy} onClick={() => power("sleep")}>SLEEP</button>
            <button className="ghost-btn" disabled={busy} onClick={() => power("restart")}>RESTART</button>
            <button className="ghost-btn apps__close" disabled={busy} onClick={() => power("shutdown")}>SHUT DOWN</button>
          </div>
        </div>
        <Gauge label="CPU" value={s.stats.cpu_percent} detail="" />
        <Gauge label="MEMORY" value={s.stats.ram_percent} detail={`${s.stats.ram_used_gb} / ${s.stats.ram_total_gb} GB`} />
        <Gauge label="DISK C:" value={s.stats.disk_c_percent} detail={`${(s.stats.disk_c_free_gb / 1000).toFixed(2)} TB free`} />
      </div>
      <div className="sys__card">
        <div className="sys__cardtitle">TOP PROCESSES (memory)</div>
        <div className="sys__procs">
          {s.processes.map((p, i) => (
            <div key={p.name + i} className="sys__proc"><span>{p.name}</span><span className="files__meta">{p.mem_mb} MB</span></div>
          ))}
        </div>
      </div>
    </div>
  );
}
