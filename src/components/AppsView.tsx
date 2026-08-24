// APPS: live tiles of every open window. Click to switch, or minimize / maximize /
// close — the desktop, managed from inside JARVIS.
import { useEffect, useState } from "react";
import { api } from "../lib/sidecar";

interface Win { hwnd: number; title: string; process: string; minimized: boolean; active: boolean; thumb: string | null }

export function AppsView() {
  const [wins, setWins] = useState<Win[]>([]);
  const [busy, setBusy] = useState(false);

  const load = async () => {
    try { const r = await api("/windows"); setWins(r.windows ?? []); } catch {}
  };
  useEffect(() => {
    load();
    // only poll while this tab is actually on screen (and the window is focused)
    const t = setInterval(() => { if (!document.hidden) load(); }, 4000);
    return () => clearInterval(t);
  }, []);

  const act = async (hwnd: number, action: string) => {
    setBusy(true);
    try { await api("/windows/act", { method: "POST", body: JSON.stringify({ hwnd, action }) }); } catch {}
    setTimeout(load, 400);
    setBusy(false);
  };

  return (
    <div className="apps">
      <div className="apps__bar">
        <span className="panel-title">APPS</span>
        <span className="files__count">{wins.length} open window{wins.length === 1 ? "" : "s"}</span>
        <button className="ghost-btn" onClick={load} disabled={busy}>REFRESH</button>
      </div>
      <div className="apps__grid">
        {wins.map((w) => (
          <div key={w.hwnd} className={`apps__tile ${w.active ? "is-active" : ""} ${w.minimized ? "is-min" : ""}`}>
            <div className="apps__thumb" onClick={() => act(w.hwnd, "focus")} title="Switch to this window">
              {w.thumb ? <img src={w.thumb} alt={w.title} /> : <div className="apps__placeholder">{w.minimized ? "minimized" : w.process || "no preview"}</div>}
            </div>
            <div className="apps__title" title={w.title}>{w.title}</div>
            <div className="apps__proc">{w.process}</div>
            <div className="apps__actions">
              <button className="ghost-btn" onClick={() => act(w.hwnd, "focus")}>SWITCH</button>
              <button className="ghost-btn" onClick={() => act(w.hwnd, w.minimized ? "focus" : "minimize")}>{w.minimized ? "RESTORE" : "MIN"}</button>
              <button className="ghost-btn" onClick={() => act(w.hwnd, "maximize")}>MAX</button>
              <button className="ghost-btn apps__close" onClick={() => { if (confirm(`Close "${w.title}"?`)) act(w.hwnd, "close"); }}>CLOSE</button>
            </div>
          </div>
        ))}
        {wins.length === 0 && <div className="memory__empty">No other windows open.</div>}
      </div>
    </div>
  );
}
