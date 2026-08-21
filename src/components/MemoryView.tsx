import { useEffect, useState } from "react";
import { api } from "../lib/sidecar";

interface Memory {
  id: number;
  ts: number;
  category: string;
  content: string;
  source: string;
  confidence: string;
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
          <div key={m.id} className="memory__card">
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
            </div>
            <p className="memory__content">{m.content}</p>
            <div className="memory__actions">
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
