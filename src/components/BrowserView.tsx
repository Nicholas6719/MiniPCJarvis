// JARVIS's in-app browser: a live view of the hidden browser JARVIS drives.
// Nothing ever opens in an external browser — pages render here.
import React, { useEffect, useState } from "react";
import { useStore } from "../state/store";
import { api } from "../lib/sidecar";

export function BrowserView() {
  const b = useStore((s) => s.browser);
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => { if (b?.url) setUrl(b.url); }, [b?.url]);

  const go = async (target?: string) => {
    const u = (target ?? url).trim();
    if (!u) return;
    setBusy(true);
    try { await api("/browser/open", { method: "POST", body: JSON.stringify({ url: u }) }); } catch {}
    setBusy(false);
  };
  const clickShot = async (e: React.MouseEvent<HTMLImageElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const x = (e.clientX - r.left) / r.width, y = (e.clientY - r.top) / r.height;
    setBusy(true);
    try { await api("/browser/click", { method: "POST", body: JSON.stringify({ x, y }) }); } catch {}
    setBusy(false);
  };
  const wheel = async (e: React.WheelEvent) => {
    if (busy) return;
    setBusy(true);
    try { await api("/browser/scroll", { method: "POST", body: JSON.stringify({ dy: Math.sign(e.deltaY) * 500 }) }); } catch {}
    setBusy(false);
  };
  const typeInto = async (text: string) => {
    setBusy(true);
    try { await api("/browser/type", { method: "POST", body: JSON.stringify({ text, enter: true }) }); } catch {}
    setBusy(false);
  };
  const [typed, setTyped] = useState("");
  const back = async () => {
    setBusy(true);
    try { await api("/browser/back", { method: "POST" }); } catch {}
    setBusy(false);
  };

  return (
    <div className="browser">
      <div className="browser__bar">
        <span className="panel-title">BROWSER</span>
        <button className="ghost-btn" onClick={back} disabled={busy || !b}>BACK</button>
        <input className="browser__url" value={url} placeholder="enter a web address"
               onChange={(e) => setUrl(e.target.value)}
               onKeyDown={(e) => { if (e.key === "Enter") go(); }} />
        <button className="ghost-btn" onClick={() => go()} disabled={busy}>{busy ? "…" : "GO"}</button>
      </div>
      {b?.title && <div className="browser__title">{b.title}{b.action ? <span className="browser__action"> · {b.action}</span> : null}</div>}
      {b?.error && <div className="webpanel__error">{b.error}</div>}
      <div className={`browser__frame ${busy ? "is-busy" : ""}`}>
        {b?.shot
          ? <img className="browser__shot browser__shot--live" src={b.shot} alt={b.title ?? "page"}
                 onClick={clickShot} onWheel={wheel} title="Click to interact · scroll to scroll" />
          : <div className="memory__empty">{b ? "no preview available" : "Ask JARVIS to open a page, or type an address above."}</div>}
      </div>
      <div className="browser__typebar">
        <input className="browser__url" value={typed} placeholder="type into the page and press Enter (after clicking a field)"
               onChange={(e) => setTyped(e.target.value)}
               onKeyDown={(e) => { if (e.key === "Enter") { typeInto(typed); setTyped(""); } }} />
      </div>
      {b?.text && <div className="browser__text">{b.text}</div>}
    </div>
  );
}
