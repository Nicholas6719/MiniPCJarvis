// JARVIS's in-app browser: a live view of the hidden browser JARVIS drives.
// Nothing ever opens in an external browser — pages render here.
import { useEffect, useState } from "react";
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
          ? <img className="browser__shot" src={b.shot} alt={b.title ?? "page"} />
          : <div className="memory__empty">{b ? "no preview available" : "Ask JARVIS to open a page, or type an address above."}</div>}
      </div>
      {b?.text && <div className="browser__text">{b.text}</div>}
    </div>
  );
}
