// Wedge panels (§7): radial only, used by faults and the confirmation gate.
// The 96px corner always faces the core; they deploy OUTWARD from it.
import { useEffect, useState } from "react";
import { useStore } from "../state/store";
import { api } from "../lib/sidecar";

type Pos = "tl" | "tr" | "bl" | "br";

// The corner light spill makes the core read as the actual light source.
const SPILL: Record<Pos, string> = {
  tl: "100% 100%", tr: "0% 100%", bl: "100% 0%", br: "0% 0%",
};
const GRAD_ANGLE: Record<Pos, number> = { tl: 160, tr: 200, bl: 20, br: 340 };

export function Wedge({ pos, tint, children }: { pos: Pos; tint?: "red" | "amber"; children: React.ReactNode }) {
  const rim = tint === "red" ? "rgba(255,92,106," : tint === "amber" ? "rgba(255,201,77," : null;
  const spill = rim
    ? `radial-gradient(120% 120% at ${SPILL[pos]}, ${rim}.16) 0%, transparent 58%)`
    : `radial-gradient(120% 120% at ${SPILL[pos]}, color-mix(in srgb, var(--rim) 13%, transparent) 0%, transparent 58%)`;
  const panel = tint === "red"
    ? `linear-gradient(${GRAD_ANGLE[pos]}deg, rgba(30,12,16,.92), rgba(12,6,9,.9))`
    : tint === "amber"
    ? `linear-gradient(${GRAD_ANGLE[pos]}deg, rgba(30,24,10,.92), rgba(12,9,4,.9))`
    : `linear-gradient(${GRAD_ANGLE[pos]}deg, rgba(9,22,32,.9), rgba(5,13,19,.9))`;
  return (
    <div className={`wedge wedge--${pos}${tint ? ` wedge--${tint}` : ""}`}
         style={{ background: `${spill}, ${panel}` }}>
      {children}
    </div>
  );
}

// ------------------------------------------------------------- confirmation gate (§9)
// Plain language, never JSON. It never times out into yes — non-negotiable.

const GATE_SENTENCE: Record<string, (a: any) => string> = {
  delete_file: (a) => `You asked me to delete ${short(a.path)}. Say the word and it's gone — or tell me no and nothing happens.`,
  move_file: (a) => `You asked me to move ${short(a.path)}${a.destination ? ` to ${short(a.destination)}` : ""}. Nothing has moved yet.`,
  rename_file: (a) => `You asked me to rename ${short(a.path ?? "")} to ${a.new_name ?? "a new name"}. Nothing has changed yet.`,
  power_action: (a) => `You asked me to ${a.action ?? "power down"} the computer. Everything unsaved goes with it — say the word.`,
  browser_submit: () => `You asked me to submit this form. Once it's sent I can't unsend it.`,
  _debug_confirm: () => `This is a drill — a no-op behind the same gate the real tools use.`,
};

const GATE_RISKS: Record<string, { head: string; badge: string; note: string }> = {
  delete_file: { head: "Goes to the recycle bin", badge: "RECOVERABLE", note: "It can be restored from the bin afterwards." },
  power_action: { head: "Interrupts everything", badge: "UNSAVED WORK", note: "Open programs close with the session." },
  browser_submit: { head: "Cannot be unsent", badge: "LEAVES THIS MACHINE", note: "The form's contents go to the site." },
};

function short(p?: string) {
  if (!p) return "that file";
  const parts = String(p).split(/[\\/]/);
  return parts[parts.length - 1] || p;
}

export function ConfirmationGate() {
  const confirmation = useStore((s) => s.confirmation);
  const clear = useStore((s) => s.clearConfirmation);
  if (!confirmation) return null;

  const { tool, args, risk } = confirmation;
  const sentence = (GATE_SENTENCE[tool] ?? ((a: any) =>
    `You asked me to run ${tool.replace(/_/g, " ")}. Nothing has happened yet — say the word.`))(args ?? {});
  const meta = GATE_RISKS[tool] ?? { head: "Before I do this", badge: risk.toUpperCase(), note: "Nothing has run yet." };
  const rows: [string, string][] = Object.entries(args ?? {}).map(([k, v]) => [
    k.replace(/_/g, " "), String(v),
  ]);

  const answer = async (approved: boolean) => {
    try {
      await api("/confirm", {
        method: "POST",
        body: JSON.stringify({ confirm_id: confirmation.confirmId, approved }),
      });
    } catch {}
    clear();
  };

  return (
    <>
      <div className="radial__prose"><p>{sentence}</p></div>
      <Wedge pos="tl">
        <div className="wedge__eyebrow">EXACTLY WHAT RUNS</div>
        <div className="wedge__mono">{String(args?.path ?? args?.url ?? args?.name ?? tool.replace(/_/g, " "))}</div>
        <div className="wedge__rows">
          {rows.slice(0, 4).map(([k, v]) => (
            <div key={k} className="wedge__row"><span>{k}</span><span className="wedge__rowval">{v.slice(0, 42)}</span></div>
          ))}
          <div className="wedge__row"><span>Other files touched</span><span style={{ color: "#59e0a5" }}>none</span></div>
        </div>
        <div className="wedge__note">One action. Exactly what you see here, nothing else.</div>
      </Wedge>
      <Wedge pos="tr" tint="amber">
        <div className="wedge__toprow">
          <span className="wedge__eyebrow" style={{ color: "#ffc94d" }}>{meta.head.toUpperCase()}</span>
          <span className="mono-sub" style={{ color: "#c9a86a" }}>{meta.badge}</span>
        </div>
        <div className="wedge__headline" style={{ color: "#ffeec4" }}>{tool.replace(/_/g, " ")}</div>
        <div className="wedge__monosub">tool: {tool} · risk: {risk}<br />nothing has run yet</div>
        <div className="wedge__actions">
          <button className="wbtn wbtn--amber" onClick={() => answer(true)}>DO IT</button>
          <button className="wbtn wbtn--ghost" onClick={() => answer(false)}>NO</button>
        </div>
      </Wedge>
    </>
  );
}

// ---------------------------------------------------------------- fault wedges (§8)

interface Check { name: string; status: string; detail?: string }

export function FaultWedges() {
  const [checks, setChecks] = useState<Check[]>([]);
  const [sys, setSys] = useState<any>(null);
  const [busy, setBusy] = useState("");
  const state = useStore((s) => s.state);
  const activity = useStore((s) => s.activity);

  useEffect(() => {
    if (state !== "error") return;
    let dead = false;
    (async () => {
      try { const d = await api("/diagnostics"); if (!dead) setChecks(d.checks ?? []); } catch {}
      try { const r = await api("/system"); if (!dead) setSys(r); } catch {}
    })();
    return () => { dead = true; };
  }, [state]);

  const failing = checks.find((c) => c.status === "error");
  const healthy = checks.filter((c) => c.status === "ok");
  const prior = activity.filter((a) => a.kind === "error" || a.kind === "repair").slice(-3);
  const lastError = [...activity].reverse().find((a) => a.kind === "error" || a.kind === "boot_error");

  const restart = async () => {
    if (!failing) return;
    setBusy(failing.name);
    try { await api("/repair", { method: "POST", body: JSON.stringify({ subsystem: failing.name }) }); } catch {}
    try { const d = await api("/diagnostics"); setChecks(d.checks ?? []); } catch {}
    setBusy("");
  };

  return (
    <>
      <div className="radial__prose">
        <p>{lastError?.summary
          ? `${failing ? `${failing.name} stopped answering. ` : ""}${lastError.summary}`
          : failing
          ? `${failing.name} stopped answering. I've kept everything else running${failing ? ", and I can restart it now if you like" : ""}.`
          : "Something went wrong — diagnostics has the detail."}</p>
      </div>

      {/* TELEMETRY — idle numbers on purpose: the machine isn't busy, it's broken */}
      <Wedge pos="tl">
        <div className="wedge__toprow">
          <span className="wedge__eyebrow">TELEMETRY</span>
          <span className="mono-sub">LIVE</span>
        </div>
        <div className="wedge__metrics">
          <div className="metric">
            <div className="metric__row"><span>CPU</span><span className="metric__val">{Math.round(sys?.stats?.cpu_percent ?? 0)}%</span></div>
            <div className="metric__bar"><div style={{ width: `${sys?.stats?.cpu_percent ?? 0}%` }} /></div>
          </div>
          <div className="metric">
            <div className="metric__row"><span>MEMORY</span><span className="metric__val">{sys?.stats?.ram_used_gb ?? "—"} / {sys?.stats?.ram_total_gb ?? "—"} GB</span></div>
            <div className="metric__bar"><div style={{ width: `${sys?.stats?.ram_percent ?? 0}%` }} /></div>
          </div>
          <div className="metric">
            <div className="metric__row"><span>DISK C:</span><span className="metric__val">{((sys?.stats?.disk_c_free_gb ?? 0) / 1000).toFixed(1)} TB FREE</span></div>
            <div className="metric__bar"><div style={{ width: `${sys?.stats?.disk_c_percent ?? 0}%`, background: "#45ffc8", boxShadow: "0 0 8px #45ffc8" }} /></div>
          </div>
        </div>
      </Wedge>

      {/* FAILING SUBSYSTEM */}
      <Wedge pos="tr" tint="red">
        <div className="wedge__toprow">
          <span className="wedge__eyebrow" style={{ color: "#ff5c6a" }}>FAILING SUBSYSTEM</span>
          <span className="mono-sub flick" style={{ color: "#ff9aa4" }}>DOWN</span>
        </div>
        <div className="wedge__headline" style={{ color: "#ffd8dc" }}>{failing?.name ?? "unknown"}</div>
        <div className="wedge__monosub" style={{ color: "#a8808a" }}>{failing?.detail ?? lastError?.summary ?? "no detail available"}</div>
        <div className="wedge__actions">
          <button className="wbtn wbtn--red" onClick={restart} disabled={!failing || !!busy}>
            {busy ? "RESTARTING…" : "RESTART IT"}
          </button>
          <button className="wbtn wbtn--ghost" onClick={() => useStore.getState().setState("idle")}>LEAVE IT</button>
        </div>
      </Wedge>

      {/* HAPPENED BEFORE */}
      <Wedge pos="bl">
        <div className="wedge__eyebrow" style={{ marginBottom: "calc(14px * var(--s))" }}>HAPPENED BEFORE</div>
        <div className="wedge__list">
          {prior.length === 0 && <div className="wedge__note" style={{ borderTop: "none", paddingTop: 0, marginTop: 0 }}>First time this session.</div>}
          {prior.map((p) => (
            <div key={p.id} className="wedge__histrow">
              <span className="mono-sub" style={{ width: "calc(52px * var(--s))" }}>
                {new Date(p.ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
              </span>
              <span className="wedge__histtext">{p.summary}</span>
            </div>
          ))}
          {prior.filter((p) => p.kind === "error").length >= 3 && (
            <div className="wedge__histrow" style={{ paddingTop: "calc(8px * var(--s))", borderTop: "1px solid rgba(39,199,255,.1)" }}>
              <span className="mono-sub" style={{ width: "calc(52px * var(--s))", color: "var(--rim)" }}>NOTE</span>
              <span className="wedge__histtext" style={{ color: "#d7ecf7" }}>Repeat fault — worth looking at the cause rather than restarting again.</span>
            </div>
          )}
        </div>
      </Wedge>

      {/* EVERYTHING ELSE — reassurance that one failure didn't take the assistant down */}
      <Wedge pos="br">
        <div className="wedge__toprow">
          <span className="wedge__eyebrow">EVERYTHING ELSE</span>
          <span className="mono-sub" style={{ color: "#59e0a5" }}>{healthy.length} OF {checks.length || "?"} HEALTHY</span>
        </div>
        <div className="wedge__list">
          {healthy.slice(0, 5).map((c) => (
            <div key={c.name} className="wedge__sysrow">
              <span className="dot dot--green" />
              <span className="wedge__sysname">{c.name}</span>
              <span className="wedge__sysdetail mono-sub">{String(c.detail ?? "").slice(0, 34)}</span>
            </div>
          ))}
        </div>
      </Wedge>
    </>
  );
}
