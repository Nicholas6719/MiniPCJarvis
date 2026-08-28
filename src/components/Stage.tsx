// The stage (§6): one box, always in the same place, whose content type changes
// with the task. There is no Files view, no Browser view, no Research view to
// navigate to — the utterance selects the renderer.
import { useEffect, useMemo, useRef, useState } from "react";
import { useStore, STAGE_HOLD_MS, SettingsSection } from "../state/store";
import { api } from "../lib/sidecar";
import { SettingsView } from "./SettingsView";
import { MemoryView } from "./MemoryView";
import { TasksView } from "./TasksView";

// ---------------------------------------------------------------- shared chrome

function useElapsed(startedTs: number | undefined, final: number | null | undefined) {
  const [, force] = useState(0);
  useEffect(() => {
    if (final != null || !startedTs) return;
    const t = setInterval(() => force((n) => n + 1), 100);
    return () => clearInterval(t);
  }, [startedTs, final]);
  if (final != null) return (final / 1000).toFixed(2);
  if (!startedTs) return null;
  return ((Date.now() - startedTs) / 1000).toFixed(2);
}

function StageHeader({ eyebrow, word, meta, live }: { eyebrow: string; word: string; meta: string; live: boolean }) {
  return (
    <div className="stage__head">
      <div className="stage__headleft">
        <span className="stage__eyebrow">{eyebrow}</span>
        <span className="stage__word">{word}</span>
      </div>
      <div className="stage__headright">
        <span className="stage__meta">{meta}</span>
        <div className="stage__track">{live && <div className="stage__sweep" />}</div>
      </div>
    </div>
  );
}

// Machine panel data: real numbers, polled while visible.
function useMachine() {
  const [sys, setSys] = useState<any>(null);
  const [model, setModel] = useState("");
  useEffect(() => {
    let dead = false;
    const load = async () => {
      try { const r = await api("/system"); if (!dead) setSys(r); } catch {}
    };
    (async () => {
      try {
        const c = await api("/config");
        if (!dead) setModel(String(c.config?.llm?.active_model ?? ""));
      } catch {}
    })();
    load();
    const t = setInterval(() => { if (!document.hidden) load(); }, 5000);
    return () => { dead = true; clearInterval(t); };
  }, []);
  return { sys, model };
}

function Metric({ label, value, pct, color }: { label: string; value: string; pct: number; color?: string }) {
  return (
    <div className="metric">
      <div className="metric__row"><span>{label}</span><span className="metric__val">{value}</span></div>
      <div className="metric__bar"><div style={{ width: `${Math.min(100, Math.max(0, pct))}%`, background: color ?? "#27c7ff", boxShadow: `0 0 8px ${color ?? "#27c7ff"}` }} /></div>
    </div>
  );
}

function RunStrip() {
  const turn = useStore((s) => s.turn);
  const elapsed = useElapsed(turn?.startedTs, turn?.elapsedMs);
  const { sys, model } = useMachine();
  const steps = turn?.steps ?? [];
  return (
    <div className="stage__foot">
      <div className="stage__run">
        <div className="stage__eyebrow" style={{ color: "var(--rim)" }}>
          {turn?.elapsedMs != null ? `THIS TURN · ${elapsed} s` : elapsed ? `THIS TURN · ${elapsed} s` : "THIS TURN"}
        </div>
        <div className="run__steps">
          {steps.map((s, i) => (
            <div key={i} className="run__step" style={{ opacity: s.status === "pending" ? 0.4 : 1 }}>
              <div className="run__rail">
                <span className={`dot dot--${s.status === "done" ? (s.kind === "reflex" ? "amber2" : "green") : s.status === "active" ? "rim flick" : "hollow"}`} />
                {i < steps.length - 1 && <span className="run__rule" />}
              </div>
              <div className="run__title">{s.label}</div>
              <div className="run__sub mono-sub">{s.sub}</div>
            </div>
          ))}
        </div>
      </div>
      <div className="stage__machine">
        <div className="stage__eyebrow" style={{ color: "var(--rim)" }}>MACHINE</div>
        <Metric label="CPU" value={`${Math.round(sys?.stats?.cpu_percent ?? 0)}%`} pct={sys?.stats?.cpu_percent ?? 0} />
        <Metric label="MEMORY" value={`${sys?.stats?.ram_used_gb ?? "—"} / ${sys?.stats?.ram_total_gb ?? "—"} GB`} pct={sys?.stats?.ram_percent ?? 0} />
        <Metric label="DISK C:" value={`${((sys?.stats?.disk_c_free_gb ?? 0) / 1000).toFixed(1)} TB FREE`} pct={sys?.stats?.disk_c_percent ?? 0} color="#45ffc8" />
        <div className="machine__mono mono-sub">{model ? model.toUpperCase() : ""}{sys?.network?.ssid ? ` · ${sys.network.ssid.toUpperCase()}` : ""}</div>
      </div>
    </div>
  );
}

// Condensed one-line footer for visual stages: three dots + a voice hint.
function VisualStrip({ hint }: { hint: string }) {
  const turn = useStore((s) => s.turn);
  const steps = (turn?.steps ?? []).filter((s) => s.status !== "pending").slice(0, 3);
  return (
    <div className="stage__vfoot">
      {steps.map((s, i) => (
        <div key={i} className="vfoot__item" style={i > 0 ? { borderLeft: "1px solid color-mix(in srgb, var(--rim) 14%, transparent)" } : {}}>
          <span className={`dot dot--${s.status === "active" ? "rim flick" : s.kind === "reflex" ? "amber2" : "green"}`} style={{ width: "calc(8px * var(--s))", height: "calc(8px * var(--s))" }} />
          <span className="vfoot__label">{s.label}</span>
        </div>
      ))}
      <span className="vfoot__hint mono-sub">{hint}</span>
    </div>
  );
}

// The dismiss bar — deliberately quiet (§6.3). Label and drain length track the
// actual hold, which varies by stage kind and by "keep it for ten minutes".
function HoldBar() {
  const stage = useStore((s) => s.stage);
  if (!stage?.holdUntil || stage.pinned) return null;
  const remaining = Math.max(0, stage.holdUntil - Date.now());
  const label = remaining >= 90000 ? `HOLDING ${Math.round(remaining / 60000)} MIN`
    : `HOLDING ${Math.round(remaining / 1000)} s`;
  return (
    <div className="stage__hold">
      <div className="hold__track"><div className="hold__drain" style={{ animationDuration: `${remaining}ms` }} /></div>
      <span className="mono-sub">{label} · SAY "KEEP IT" OR "BRING THAT BACK"</span>
    </div>
  );
}

// ---------------------------------------------------------------- prose (§6.3)

function ProseStage() {
  const turn = useStore((s) => s.turn);
  const draft = useStore((s) => s.assistantDraft);
  const transcript = useStore((s) => s.transcript);
  const state = useStore((s) => s.state);
  const web = useStore((s) => s.web);
  const elapsed = useElapsed(turn?.startedTs, turn?.elapsedMs);

  const answering = state === "speaking" || draft.length > 0;
  const lastAssistant = [...transcript].reverse().find((t) => t.role === "assistant");
  const text = draft || (turn?.elapsedMs != null ? lastAssistant?.text ?? "" : "") || turn?.userText || "";

  const results = web?.results ?? [];
  const readCount = results.filter((r) => web?.read[r.url]).length;
  return (
    <>
      <StageHeader
        eyebrow="YOU ASKED"
        word={state === "speaking" ? "SPEAKING"
          : turn?.elapsedMs != null ? "ANSWERED"
          : state === "thinking" ? "THINKING"
          : state === "searching" ? "SEARCHING" : "WORKING"}
        meta={elapsed ? `ELAPSED ${elapsed} s` : ""}
        live={state !== "idle" && state !== "sleeping"}
      />
      <div className="prose__body">
        <p className="prose__text">{text}</p>
        {results.length > 0 && (
          <div className="chips">
            {results.slice(0, 6).map((r, i) => {
              const read = !!web?.read[r.url];
              const inflight = web?.opening === r.url;
              return (
                <span key={r.url} className={`chip ${read ? "chip--read" : inflight ? "chip--live" : ""}`}>
                  <span className={`chip__idx ${inflight ? "flick" : ""}`}>{String(i + 1).padStart(2, "0")}</span>
                  <span className="chip__host">{r.host ?? r.url}</span>
                </span>
              );
            })}
            {results.length - readCount > 1 && web?.stage !== "done" && (
              <span className="chip chip--queued"><span className="mono-sub">{results.length - readCount} QUEUED</span></span>
            )}
          </div>
        )}
      </div>
      <HoldBar />
      <RunStrip />
    </>
  );
}

// ---------------------------------------------------------------- browser (§6.4)
// The full embedded-Brave-with-real-tabs decision is flagged in §17; until that
// lands, this renders JARVIS's REAL browsing — live results, the action marker on
// the result it is about to open, read progression — from sidecar events. No captures.

function BrowserStage() {
  const web = useStore((s) => s.web);
  const state = useStore((s) => s.state);
  const results = web?.results ?? [];
  const readCount = results.filter((r) => web?.read[r.url]).length;
  const total = results.length || 0;
  const handoff = state === "waiting";
  const own = handoff ? "#ffc94d" : "#45d7ff";
  const ownLabel = handoff ? "OVER TO YOU" : "JARVIS DRIVING";
  const openingIdx = results.findIndex((r) => r.url === web?.opening);

  return (
    <>
      <StageHeader
        eyebrow={web?.query ? `YOU SAID "${web.query.toUpperCase()}"` : "ON THE WEB"}
        word={handoff ? "OVER TO YOU" : web?.stage === "done" ? "READ" : "BROWSING"}
        meta={total ? (readCount ? `${readCount} OF ${total} READ` : `${total} RESULTS`) : "SEARCHING"}
        live={web?.stage !== "done"}
      />
      <div className="browser__wrap">
        <div className="browser" style={{ "--own": own } as React.CSSProperties}>
          <div className="browser__tabs">
            <div className="btab btab--active" style={{ borderTopColor: own }}>
              <span className="dot dot--rim flick" style={{ width: "calc(7px * var(--s))", height: "calc(7px * var(--s))" }} />
              <span className="btab__title">{web?.query ? `${web.query} — Brave` : "Brave"}</span>
              <span className="btab__badge">{total ? `${readCount}/${total}` : ""}</span>
            </div>
            <div className="btab"><span className="btab__title" style={{ color: "#6f6f7d" }}>New tab</span></div>
            <div className="btab__plus">+</div>
            <div className="browser__owner">
              <span className="dot" style={{ width: "calc(6px * var(--s))", height: "calc(6px * var(--s))", background: own, boxShadow: `0 0 8px ${own}` }} />
              <span style={{ color: own }}>{ownLabel}</span>
            </div>
          </div>
          <div className="browser__url">
            <div className="browser__nav"><span>←</span><span>→</span><span>↻</span></div>
            <div className="browser__addr">
              <span className="browser__tls">TLS</span>
              <span className="browser__href">{web?.opening ?? (web?.query ? `duckduckgo.com/?q=${encodeURIComponent(web.query)}` : "")}</span>
            </div>
          </div>
          <div className="browser__page">
            {web?.error ? (
              <div className="browser__err">{web.error}</div>
            ) : (
              <div className="serp">
                {total > 0 && <div className="serp__count mono-sub">{total} results · {readCount} read</div>}
                {results.slice(0, 6).map((r, i) => {
                  const read = !!web?.read[r.url];
                  const isOpening = i === openingIdx;
                  return (
                    <div key={r.url} className={`serp__hit ${isOpening ? "serp__hit--live" : ""}`}
                         style={!isOpening ? { opacity: read ? 0.5 : i < 3 ? 0.5 : i < 5 ? 0.32 : 0.22 } : {}}>
                      {isOpening && (
                        <>
                          {/* the action marker is anchored INSIDE its result (§6.4) */}
                          <div className="marker">
                            <div className="marker__ping" />
                            <div className="marker__dot" />
                          </div>
                          <div className="marker__chip mono-sub">OPENING RESULT {i + 1}</div>
                        </>
                      )}
                      <span className="serp__host">{r.host ?? r.url}</span>
                      <span className="serp__title" style={isOpening ? { paddingLeft: "calc(154px * var(--s))", color: "#c3d4f5" } : {}}>{r.title ?? r.url}</span>
                      {r.snippet && <span className="serp__snip">{r.snippet}</span>}
                      {read && <span className="serp__read mono-sub">READ ✓</span>}
                    </div>
                  );
                })}
                {total === 0 && <div className="browser__err mono-sub" style={{ color: "#4d6b80" }}>SEARCHING…</div>}
              </div>
            )}
          </div>
        </div>
      </div>
      <VisualStrip hint={handoff ? 'OR SAY "SKIP IT" AND I\'LL ANSWER WITHOUT IT' : 'SAY "STOP" TO TAKE IT'} />
    </>
  );
}

// ---------------------------------------------------------------- images (§6.5)

function ImagesStage() {
  const images = useStore((s) => s.images);
  const imgs = images?.images ?? [];
  const focus = images?.focus;
  const focused = focus != null ? imgs[focus] : null;
  return (
    <>
      <StageHeader
        eyebrow={images?.query ? `YOU SAID "SHOW ME ${images.query.toUpperCase()}"` : "IMAGES"}
        word={focused ? `IMAGE ${(focus ?? 0) + 1} OF ${imgs.length}` : "SHOWING"}
        meta={`${imgs.length} IMAGES`}
        live={false}
      />
      {focused ? (
        <>
          <div className="images__note">
            <span>say <span style={{ color: "var(--rim)" }}>"back to the grid"</span> or another one — <span style={{ color: "var(--rim)" }}>"the third one"</span></span>
            <span className="mono-sub">{focused.page ? hostOf(focused.page).toUpperCase() : ""}</span>
          </div>
          <div className="images__focus">
            <img src={focused.src} alt={focused.alt} />
          </div>
        </>
      ) : (
        <>
          <div className="images__note">
            <span>{imgs.length} images — say <span style={{ color: "var(--rim)" }}>"bigger"</span> or <span style={{ color: "var(--rim)" }}>"the second one"</span></span>
            <span className="mono-sub">DUCKDUCKGO IMAGES · KEYLESS</span>
          </div>
          <div className="images__grid">
            {imgs.slice(0, 8).map((im, i) => (
              <div key={i} className={`imtile ${i === 0 ? "imtile--best" : ""}`}>
                <img src={im.src} alt={im.alt} loading="lazy" />
                {i === 0 && <div className="imtile__best mono-sub">BEST MATCH</div>}
                <div className="imtile__src mono-sub">{im.page ? hostOf(im.page) : ""}</div>
              </div>
            ))}
          </div>
        </>
      )}
      <HoldBar />
      <VisualStrip hint={focused ? 'SAY "BACK TO THE GRID" FOR ALL OF THEM' : 'HOLDING · SAY "KEEP IT" TO PIN'} />
    </>
  );
}

function hostOf(u: string) {
  try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return u; }
}

// ---------------------------------------------------------------- file (§6.6)

function FileStage() {
  const fp = useStore((s) => s.filePreview);
  const files = useStore((s) => s.files);
  const query = (files?.query ?? "").toLowerCase();
  const lines = useMemo(() => (fp?.text ?? "").split("\n").slice(0, 400), [fp?.text]);
  const matches = useMemo(() => {
    if (!query) return new Set<number>();
    const out = new Set<number>();
    lines.forEach((l, i) => { if (l.toLowerCase().includes(query)) out.add(i); });
    return out;
  }, [lines, query]);

  if (!fp) return null;
  return (
    <>
      <StageHeader eyebrow="YOUR FILES" word="READING" meta={`${(fp.size / 1024).toFixed(1)} KB`} live={false} />
      <div className="filebox__wrap">
        <div className="filebox">
          <div className="filebox__head">
            <div className="filebox__names">
              <span className="filebox__name">{fp.name}</span>
              <span className="filebox__path mono-sub">{fp.path} · {(fp.size / 1024).toFixed(1)} KB</span>
            </div>
            {matches.size > 0 && (
              <span className="filebox__chip mono-sub">{matches.size} OF {lines.length} LINES MATCHED</span>
            )}
          </div>
          <div className="filebox__body">
            {fp.type === "image" && fp.data ? (
              <img src={fp.data} alt={fp.name} style={{ maxWidth: "100%", maxHeight: "100%", objectFit: "contain" }} />
            ) : (
              lines.map((l, i) => {
                const hit = matches.has(i);
                return (
                  <div key={i} className={`fline ${hit ? "fline--hit" : ""}`} style={!hit && matches.size > 0 ? { opacity: 0.4 } : {}}>
                    <span className="fline__no mono-sub" style={hit ? { color: "var(--rim)" } : {}}>{i + 1}</span>
                    <span className="fline__text">{l || " "}</span>
                  </div>
                );
              })
            )}
          </div>
        </div>
      </div>
      <HoldBar />
      <VisualStrip hint='HOLDING · SAY "KEEP IT" TO PIN' />
    </>
  );
}

// -------------------------------------------------------- folder (undesigned; minimal)

function FolderStage() {
  const files = useStore((s) => s.files);
  const setFilePreview = useStore((s) => s.setFilePreview);
  const openStage = useStore((s) => s.openStage);
  if (!files) return null;
  const preview = async (path: string) => {
    try {
      const r = await api(`/files/preview?path=${encodeURIComponent(path)}`);
      setFilePreview(r);
      openStage("file");
    } catch {}
  };
  return (
    <>
      <StageHeader
        eyebrow={files.query ? `YOU SAID "FIND ${files.query.toUpperCase()}"` : "YOUR FILES"}
        word={files.query ? "FOUND" : files.label.toUpperCase()}
        meta={`${files.count} ITEMS`}
        live={false}
      />
      <div className="filebox__wrap">
        <div className="filebox">
          <div className="filebox__head">
            <div className="filebox__names">
              <span className="filebox__name">{files.label}</span>
              <span className="filebox__path mono-sub">{files.path ?? ""}</span>
            </div>
            <span className="filebox__chip mono-sub">{files.count} ITEMS</span>
          </div>
          <div className="filebox__body">
            {files.entries.slice(0, 60).map((e) => (
              <div key={e.path} className="fline fline--row" onClick={() => e.kind === "file" && preview(e.path)}
                   style={{ cursor: e.kind === "file" ? "pointer" : "default" }}>
                <span className="fline__kind mono-sub">{e.kind === "dir" ? "DIR" : (e.type ?? "").toUpperCase().slice(0, 4)}</span>
                <span className="fline__text">{e.name}</span>
                <span className="fline__meta mono-sub">{e.kind === "file" ? `${(e.size / 1024).toFixed(0)} KB` : ""}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
      <HoldBar />
      <VisualStrip hint='SAY "OPEN" AND A FILE NAME' />
    </>
  );
}

// ------------------------------------------------------- settings + history (§6.9)

const SECTIONS: { id: SettingsSection; label: string }[] = [
  { id: "voice", label: "Voice & listening" },
  { id: "model", label: "Model" },
  { id: "tools", label: "Tools & permissions" },
  { id: "memory", label: "What you've taught me" },
  { id: "history", label: "History" },
  { id: "tasks", label: "Tasks & reminders" },
  { id: "learned", label: "What he's learned" },
  { id: "about", label: "About this machine" },
];

function HistoryPane() {
  const [rows, setRows] = useState<{ role: string; content: string }[]>([]);
  const [q, setQ] = useState("");
  useEffect(() => {
    (async () => {
      try { const r = await api("/transcript?limit=200"); setRows(r.transcript ?? []); } catch {}
    })();
  }, []);
  // group into turns: user line + following assistant line
  const turns = useMemo(() => {
    const out: { user: string; answer: string }[] = [];
    for (let i = 0; i < rows.length; i++) {
      if (rows[i].role === "user") {
        out.push({ user: rows[i].content, answer: rows[i + 1]?.role === "assistant" ? rows[i + 1].content : "" });
      }
    }
    return out.reverse();
  }, [rows]);
  const shown = q ? turns.filter((t) => (t.user + " " + t.answer).toLowerCase().includes(q.toLowerCase())) : turns;
  return (
    <div className="hist">
      <div className="hist__bar">
        <div className="hist__search">
          <span style={{ color: "var(--rim)" }} className="mono-sub">FIND</span>
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="search your turns" />
          <span className="mono-sub">{q ? `${shown.length} TURNS` : ""}</span>
        </div>
        <span className="mono-sub">{turns.length} TURNS KEPT · LOCAL ONLY</span>
      </div>
      <div className="hist__list">
        {shown.slice(0, 40).map((t, i) => (
          <div key={i} className="hist__turn" style={i > 6 ? { opacity: 0.78 } : {}}>
            <div className="hist__body">
              <span className="hist__q">"{t.user}"</span>
              {t.answer && <span className="hist__a">{t.answer}</span>}
            </div>
          </div>
        ))}
        {shown.length === 0 && <div className="mono-sub" style={{ padding: "20px 0", color: "#4d6b80" }}>NOTHING KEPT YET</div>}
      </div>
    </div>
  );
}

function AboutPane() {
  const [diag, setDiag] = useState<any>(null);
  const { sys, model } = useMachine();
  useEffect(() => {
    (async () => { try { setDiag(await api("/diagnostics")); } catch {} })();
  }, []);
  const checks: any[] = diag?.checks ?? [];
  const healthy = checks.filter((c) => c.status === "ok").length;
  return (
    <div className="about">
      <div className="about__grid">
        <Metric label="CPU" value={`${Math.round(sys?.stats?.cpu_percent ?? 0)}%`} pct={sys?.stats?.cpu_percent ?? 0} />
        <Metric label="MEMORY" value={`${sys?.stats?.ram_used_gb ?? "—"} / ${sys?.stats?.ram_total_gb ?? "—"} GB`} pct={sys?.stats?.ram_percent ?? 0} />
        <Metric label="DISK C:" value={`${((sys?.stats?.disk_c_free_gb ?? 0) / 1000).toFixed(2)} TB FREE`} pct={sys?.stats?.disk_c_percent ?? 0} color="#45ffc8" />
      </div>
      <div className="about__sub mono-sub">{model.toUpperCase()}{sys?.uptime_s ? ` · UP ${Math.floor(sys.uptime_s / 3600)} H` : ""}</div>
      <div className="about__head">
        <span className="stage__eyebrow" style={{ color: "var(--rim)" }}>SUBSYSTEMS</span>
        <span className="mono-sub" style={{ color: "#59e0a5" }}>{healthy} OF {checks.length} HEALTHY</span>
      </div>
      <div className="about__checks">
        {checks.map((c) => (
          <div key={c.name} className="about__check">
            <span className={`dot dot--${c.status === "ok" ? "green" : c.status === "warn" ? "amber" : "red"}`} />
            <span className="about__name">{c.name}</span>
            <span className="about__detail mono-sub">{String(c.detail ?? "").slice(0, 72)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}


// What he has learned on his own (brain roadmap): the design said overnight
// findings are inspectable but never announced — until now they were emitted as
// events nothing rendered, so they were invisible.
function LearnedPane() {
  const [facts, setFacts] = useState<any[]>([]);
  const [stats, setStats] = useState<any>(null);
  const [night, setNight] = useState<any>(null);
  useEffect(() => {
    (async () => {
      try { const r = await api("/facts"); setFacts(r.facts ?? []); setStats(r.stats); } catch {}
      try { setNight((await api("/night_school")).last_report); } catch {}
    })();
  }, []);
  const active = facts.filter((f) => f.status === "active");
  const demoted = facts.filter((f) => f.status !== "active");
  const when = (ts: number) => ts ? new Date(ts * 1000).toLocaleDateString([], { month: "short", day: "numeric" }) : "";
  const host = (f: any) => { try { return new URL(f.sources?.[0]?.url).hostname.replace(/^www\./, ""); } catch { return ""; } };
  return (
    <div className="about">
      <div className="about__head">
        <span className="stage__eyebrow" style={{ color: "var(--rim)" }}>FACTS HE KEEPS</span>
        <span className="mono-sub">{active.length} VERIFIED{demoted.length ? ` · ${demoted.length} RETIRED` : ""}</span>
      </div>
      <div className="about__checks">
        {active.map((f) => (
          <div key={f.id} className="learned">
            <div className="learned__a">{f.answer}</div>
            <div className="learned__meta mono-sub">
              {host(f)} · verified {when(f.verified_ts)}{f.hits ? ` · used ${f.hits}×` : ""}
            </div>
          </div>
        ))}
        {active.length === 0 && (
          <div className="mono-sub" style={{ color: "var(--text-dim)", padding: "10px 0" }}>
            NOTHING YET — ASK HIM SOMETHING TIMELESS AND HE'LL VERIFY IT ON THE WEB
          </div>
        )}
      </div>
      <div className="about__head">
        <span className="stage__eyebrow" style={{ color: "var(--rim)" }}>LAST NIGHT SCHOOL</span>
        <span className="mono-sub">{night?.finished ? new Date(night.finished * 1000).toLocaleString() : "NOT RUN YET"}</span>
      </div>
      <div className="about__checks">
        {night ? (
          <div className="learned__meta mono-sub">
            {night.audited} facts re-checked · {night.confirmed} confirmed ·{" "}
            {night.changed} changed and retired · {night.curiosity} researched ·{" "}
            {night.learned} new phrasings{night.aborted ? " · stopped when you woke him" : ""}
          </div>
        ) : (
          <div className="learned__meta mono-sub">
            HE RE-VERIFIES HIS FACTS WHILE ASLEEP, INSIDE QUIET HOURS
          </div>
        )}
        {stats && (
          <div className="learned__meta mono-sub">
            this session: {stats.served} answered from memory · {stats.stored} learned · {stats.rejected} rejected as changeable
          </div>
        )}
      </div>
    </div>
  );
}

function SettingsStage() {
  const stage = useStore((s) => s.stage);
  const setSection = useStore((s) => s.setSettingsSection);
  const section = stage?.settingsSection ?? "voice";
  return (
    <>
      <StageHeader
        eyebrow={section === "history" ? 'YOU SAID "WHAT DID WE TALK ABOUT"' : "SETTINGS"}
        word={SECTIONS.find((s) => s.id === section)?.label.toUpperCase() ?? "SETTINGS"}
        meta="SETTINGS · LOCAL ONLY"
        live={false}
      />
      <div className="setwrap">
        <div className="setrail">
          {SECTIONS.map((s) => (
            <div key={s.id}
                 className={`setrail__item ${s.id === section ? "is-active" : ""}`}
                 onClick={() => setSection(s.id)}>
              {s.id === section && <span className="setrail__mark" />}
              <span>{s.label}</span>
            </div>
          ))}
          <div className="setrail__hint mono-sub">SAY "SETTINGS, HISTORY"<br />TO LAND HERE DIRECTLY</div>
        </div>
        <div className="setpane">
          {(section === "voice" || section === "model" || section === "tools") && <SettingsView />}
          {section === "memory" && <MemoryView />}
          {section === "history" && <HistoryPane />}
          {section === "tasks" && <TasksView />}
          {section === "learned" && <LearnedPane />}
          {section === "about" && <AboutPane />}
        </div>
      </div>
    </>
  );
}

// ---------------------------------------------------------------- dispatcher

export function Stage() {
  const stage = useStore((s) => s.stage);
  const dismiss = useStore((s) => s.dismissStage);

  // the hold: when it elapses (and nothing pinned it), the core comes home
  useEffect(() => {
    if (!stage?.holdUntil || stage.pinned) return;
    const ms = stage.holdUntil - Date.now();
    if (ms <= 0) { dismiss(); return; }
    const t = setTimeout(() => {
      const cur = useStore.getState().stage;
      if (cur && cur.holdUntil && !cur.pinned && Date.now() >= cur.holdUntil) dismiss();
    }, ms + 50);
    return () => clearTimeout(t);
  }, [stage?.holdUntil, stage?.pinned, dismiss]);

  // a timed pin ("keep it for ten minutes") expires into the normal drain
  useEffect(() => {
    if (!stage?.pinned || !stage.pinUntil) return;
    const ms = stage.pinUntil - Date.now();
    const t = setTimeout(() => {
      const cur = useStore.getState().stage;
      if (cur?.pinned && cur.pinUntil && Date.now() >= cur.pinUntil) {
        useStore.setState({ stage: { ...cur, pinned: false, pinUntil: undefined, holdUntil: Date.now() + STAGE_HOLD_MS } });
      }
    }, Math.max(0, ms));
    return () => clearTimeout(t);
  }, [stage?.pinned, stage?.pinUntil]);

  if (!stage) return null;
  switch (stage.kind) {
    case "prose": return <ProseStage />;
    case "browser": return <BrowserStage />;
    case "images": return <ImagesStage />;
    case "file": return <FileStage />;
    case "folder": return <FolderStage />;
    case "settings": return <SettingsStage />;
    default: return null;
  }
}
