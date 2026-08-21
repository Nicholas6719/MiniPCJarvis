import { useEffect, useState } from "react";
import { api } from "../lib/sidecar";

interface Memory {
  id: number;
  ts: number;
  category: string;
  content: string;
  source: string;
  confidence: string;
  pinned?: boolean;
}

export function MemoryView() {
  const [memories, setMemories] = useState<Memory[]>([]);
  const [filter, setFilter] = useState("");
  const [error, setError] = useState("");

  const load = async () => {
    try {
      const r = await api("/memory");
      setMemories(r.memories);
      setError("");
    } catch {
      setError("memory unavailable");
    }
  };

  useEffect(() => {
    load();
  }, []);

  const forget = async (id: number) => {
    try {
      await api(`/memory/${id}`, { method: "DELETE" });
      setMemories((m) => m.filter((x) => x.id !== id));
    } catch {}
  };

  const togglePin = async (m: Memory) => {
    try {
      await api(`/memory/${m.id}`, {
        method: "PATCH", body: JSON.stringify({ pinned: !m.pinned }),
      });
      setMemories((ms) => ms.map((x) => x.id === m.id ? { ...x, pinned: !m.pinned } : x));
    } catch {}
  };

  const [editing, setEditing] = useState<number | null>(null);
  const [editText, setEditText] = useState("");

  const saveEdit = async (id: number) => {
    const content = editText.trim();
    setEditing(null);
    if (!content) return;
    try {
      await api(`/memory/${id}`, {
        method: "PATCH", body: JSON.stringify({ content }),
      });
      setMemories((ms) => ms.map((x) => x.id === id ? { ...x, content } : x));
    } catch {}
  };

  const shown = memories.filter(
    (m) =>
      !filter ||
      m.content.toLowerCase().includes(filter.toLowerCase()) ||
      m.category.toLowerCase().includes(filter.toLowerCase())
  );

  return (
    <div className="memory">
      <div className="memory__head">
        <span className="panel-title">MEMORY</span>
        <input
          className="memory__search"
          placeholder="Search memories…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          spellCheck={false}
        />
        <button className="ghost-btn" onClick={load}>REFRESH</button>
      </div>
      <div className="memory__list">
        {error && <div className="memory__empty">{error}</div>}
        {!error && shown.length === 0 && (
          <div className="memory__empty">
            Nothing remembered yet. Tell JARVIS something worth keeping.
          </div>
        )}
        {shown.map((m) => (
          <div key={m.id} className={`memory__card ${m.pinned ? "memory__card--pinned" : ""}`}>
            <div className="memory__meta">
              <span className={`memory__cat memory__cat--${m.category}`}>
                {m.category.toUpperCase()}
              </span>
              <span className="memory__ts">
                {new Date(m.ts * 1000).toLocaleDateString([], {
                  month: "short", day: "numeric", year: "numeric",
                })}
              </span>
              <span className="memory__conf">{m.confidence}</span>
              {m.pinned && <span className="memory__pinbadge">PINNED</span>}
            </div>
            {editing === m.id ? (
              <textarea
                className="memory__edit"
                value={editText}
                autoFocus
                onChange={(e) => setEditText(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) saveEdit(m.id);
                  if (e.key === "Escape") setEditing(null);
                }}
              />
            ) : (
              <p className="memory__content">{m.content}</p>
            )}
            <div className="memory__actions">
              <button className="ghost-btn" onClick={() => togglePin(m)}>
                {m.pinned ? "UNPIN" : "PIN"}
              </button>
              {editing === m.id ? (
                <button className="ghost-btn" onClick={() => saveEdit(m.id)}>SAVE</button>
              ) : (
                <button className="ghost-btn"
                        onClick={() => { setEditing(m.id); setEditText(m.content); }}>
                  EDIT
                </button>
              )}
              <button className="ghost-btn ghost-btn--danger" onClick={() => forget(m.id)}>
                FORGET
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
