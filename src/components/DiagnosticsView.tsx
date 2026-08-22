import { useEffect, useState } from "react";
import { api } from "../lib/sidecar";

interface Check {
  name: string;
  status: "ok" | "warn" | "error";
  detail: string;
  repairable: boolean;
}

interface BrainStatus {
  examples: number;
  skills: { name: string; tool: string | null; examples: number; llm_after: boolean }[];
  stats: { reflex: number; llm: number; learned: number };
  threshold: number;
  recent: { ts: number; text: string; skill: string; source: string }[];
  commands: { phrase: string; steps: { skill: string; args: any }[] }[];
}

function BrainPanel() {
  const [b, setB] = useState<BrainStatus | null>(null);
  useEffect(() => {
    const load = async () => { try { setB(await api("/brain")); } catch {} };
    load();
    const t = setInterval(load, 10000);
    return () => clearInterval(t);
  }, []);
  if (!b) return null;
  const total = b.stats.reflex + b.stats.llm;
  return (
    <div className="brain">
      <span className="panel-title">BRAIN — REFLEXES (NO LLM)</span>
      <div className="brain__stats">
        <span><b>{b.examples}</b> examples</span>
        <span><b>{b.skills.length}</b> skills</span>
        <span><b>{total ? Math.round((b.stats.reflex / total) * 100) : 0}%</b> of turns handled by reflex</span>
        <span><b>{b.stats.learned}</b> learned this session</span>
      </div>
      <div className="brain__grid">
        {b.skills.map((s) => (
          <div key={s.name} className="brain__skill">
            <span>{s.name}{s.llm_after ? " +LLM" : ""}</span>
            <span>{s.examples}</span>
          </div>
        ))}
      </div>
      {b.commands?.length > 0 && (
        <div className="brain__recent">
          <div className="brain__sub">TAUGHT COMMANDS — say "when I say X, do Y"</div>
          {b.commands.map((c) => (
            <div key={c.phrase}>
              <b>"{c.phrase}"</b> → {c.steps.map((s) => s.skill.replace("_", " ")).join(", then ")}
              <button className="ghost-btn brain__forget"
                      onClick={async () => { try { await api("/brain/forget_command", { method: "POST", body: JSON.stringify({ phrase: c.phrase }) }); setB(await api("/brain")); } catch {} }}>
                FORGET
              </button>
            </div>
          ))}
        </div>
      )}
      {b.recent.length > 0 && (
        <div className="brain__recent">
          {b.recent.slice(0, 8).map((r) => (
            <div key={r.ts + r.text}>learned <b>"{r.text}"</b> → {r.skill}</div>
          ))}
        </div>
      )}
    </div>
  );
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
      <BrainPanel />
    </div>
  );
}
