import { useEffect, useState } from "react";
import { api } from "../lib/sidecar";

interface Check {
  name: string;
  status: "ok" | "warn" | "error";
  detail: string;
  repairable: boolean;
}

export function DiagnosticsView() {
  const [checks, setChecks] = useState<Check[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");

  const load = async () => {
    try {
      const r = await api("/diagnostics");
      setChecks(r.checks);
      setError("");
    } catch {
      setError("diagnostics unavailable");
    }
  };

  useEffect(() => {
    load();
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, []);

  const repair = async (name: string) => {
    setBusy(name);
    try {
      await api("/repair", { method: "POST", body: JSON.stringify({ subsystem: name }) });
    } catch {}
    await load();
    setBusy(null);
  };

  return (
    <div className="diag">
      <div className="diag__head">
        <span className="panel-title">SYSTEM DIAGNOSTICS</span>
        <button className="ghost-btn" onClick={load}>RUN CHECKS</button>
      </div>
      {error && <div className="memory__empty">{error}</div>}
      <div className="diag__list">
        {checks.map((c) => (
          <div key={c.name} className={`diag__row diag__row--${c.status}`}>
            <span className="diag__dot" />
            <span className="diag__name">{c.name}</span>
            <span className="diag__detail">{c.detail}</span>
            {c.repairable && c.status !== "ok" && (
              <button className="ghost-btn diag__repair"
                      disabled={busy === c.name}
                      onClick={() => repair(c.name)}>
                {busy === c.name ? "…" : "REPAIR"}
              </button>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
