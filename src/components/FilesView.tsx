// FILES: browse, search, preview and manage Desktop/Documents/Downloads/Pictures
// without leaving JARVIS. Voice ("open my downloads", "find the file called budget")
// and clicks land in the same view.
import { useEffect, useState } from "react";
import { useStore, FileEntry } from "../state/store";
import { api } from "../lib/sidecar";

const ROOTS = ["desktop", "documents", "downloads", "pictures"];

function fmtSize(n: number): string {
  if (!n) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}
function fmtDate(ts?: number): string {
  return ts ? new Date(ts * 1000).toLocaleDateString([], { month: "short", day: "numeric" }) + " " + new Date(ts * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }).replace(" ", "") : "";
}
const ICON: Record<string, string> = { folder: "▰", image: "▣", text: "≡", pdf: "▤", video: "▶", audio: "♪", archive: "▥", document: "▤" };

export function FilesView() {
  const files = useStore((s) => s.files);
  const preview = useStore((s) => s.filePreview);
  const setPreview = useStore((s) => s.setFilePreview);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<FileEntry | null>(null);
  const [renaming, setRenaming] = useState("");
  const [moving, setMoving] = useState(false);

  useEffect(() => { if (!files) open("downloads"); }, []);

  const open = async (path: string) => {
    setBusy(true); setSelected(null);
    try { await api(`/files?path=${encodeURIComponent(path)}`); } catch {}
    setBusy(false);
  };
  const search = async () => {
    if (!q.trim()) return;
    setBusy(true);
    try { await api(`/files/search?q=${encodeURIComponent(q.trim())}`); } catch {}
    setBusy(false);
  };
  const show = async (e: FileEntry) => {
    setSelected(e);
    if (e.kind === "folder") { open(e.path); return; }
    try { setPreview(await api(`/files/preview?path=${encodeURIComponent(e.path)}`)); } catch {}
  };
  const op = async (body: any) => {
    setBusy(true);
    try {
      const r = await api("/files/op", { method: "POST", body: JSON.stringify(body) });
      if (r?.error) alert(r.error);
    } catch {}
    setBusy(false); setRenaming(""); setMoving(false); setSelected(null); setPreview(null);
  };

  const active = files?.path ?? "";
  return (
    <div className="files">
      <div className="files__bar">
        <span className="panel-title">FILES</span>
        {ROOTS.map((r) => (
          <button key={r} className={`hud__navbtn ${files?.roots?.[r] && active.toLowerCase().startsWith(files.roots[r].toLowerCase()) ? "is-active" : ""}`}
                  onClick={() => open(r)}>{r.toUpperCase()}</button>
        ))}
        <input className="browser__url" placeholder="find a file…" value={q}
               onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") search(); }} />
        <button className="ghost-btn" onClick={search} disabled={busy}>FIND</button>
      </div>
      <div className="files__crumb">
        {files?.parent && <button className="ghost-btn" onClick={() => open(files.parent!)}>↑ UP</button>}
        <span>{files?.label ?? ""}</span>
        <span className="files__count">{files ? `${files.count} items` : ""}</span>
      </div>
      <div className="files__body">
        <div className={`files__list ${busy ? "is-busy" : ""}`}>
          {files?.entries.map((e) => (
            <div key={e.path} className={`files__row ${selected?.path === e.path ? "is-selected" : ""}`}
                 onClick={() => show(e)} onDoubleClick={() => e.kind === "folder" && open(e.path)}>
              <span className={`files__icon files__icon--${e.kind === "folder" ? "folder" : e.type || "file"}`}>{ICON[e.kind === "folder" ? "folder" : (e.type || "")] ?? "·"}</span>
              <span className="files__name" title={e.path}>{e.name}</span>
              <span className="files__meta">{fmtSize(e.size)}</span>
              <span className="files__meta">{fmtDate(e.modified)}</span>
            </div>
          ))}
          {files && files.entries.length === 0 && <div className="memory__empty">empty</div>}
        </div>
        <div className="files__preview">
          {selected ? (
            <>
              <div className="files__pvhead">
                <span className="files__pvname">{selected.name}</span>
                <span className="files__meta">{fmtSize(selected.size)}</span>
              </div>
              <div className="files__actions">
                <button className="ghost-btn" onClick={() => setRenaming(selected.name)}>RENAME</button>
                <button className="ghost-btn" onClick={() => setMoving(true)}>MOVE</button>
                <button className="ghost-btn" onClick={() => { if (confirm(`Send "${selected.name}" to the Recycle Bin?`)) op({ op: "delete", path: selected.path }); }}>RECYCLE</button>
                <button className="ghost-btn" onClick={() => op({ op: "open", path: selected.path })} title="Open in its Windows app">OPEN IN WINDOWS</button>
              </div>
              {renaming !== "" && (
                <div className="files__inline">
                  <input className="browser__url" value={renaming} onChange={(e) => setRenaming(e.target.value)}
                         onKeyDown={(e) => { if (e.key === "Enter") op({ op: "rename", path: selected.path, new_name: renaming }); if (e.key === "Escape") setRenaming(""); }} autoFocus />
                  <button className="ghost-btn" onClick={() => op({ op: "rename", path: selected.path, new_name: renaming })}>OK</button>
                </div>
              )}
              {moving && (
                <div className="files__inline">
                  <span className="files__meta">move to:</span>
                  {ROOTS.map((r) => <button key={r} className="ghost-btn" onClick={() => op({ op: "move", path: selected.path, destination: r })}>{r.toUpperCase()}</button>)}
                  <button className="ghost-btn" onClick={() => setMoving(false)}>CANCEL</button>
                </div>
              )}
              <div className="files__pvbody">
                {preview?.path === selected.path && preview.type === "image" && <img className="files__img" src={preview.data} alt={selected.name} />}
                {preview?.path === selected.path && preview.type === "text" && <pre className="files__text">{preview.text}</pre>}
                {preview?.path === selected.path && preview.type === "binary" && <div className="memory__empty">no preview for this file type</div>}
                {selected.kind === "folder" && <div className="memory__empty">folder</div>}
              </div>
            </>
          ) : <div className="memory__empty">Select a file to preview it here.</div>}
        </div>
      </div>
    </div>
  );
}
