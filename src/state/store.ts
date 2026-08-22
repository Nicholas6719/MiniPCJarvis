import { create } from "zustand";

export type JarvisState =
  | "offline" | "starting" | "idle" | "listening" | "processing"
  | "thinking" | "searching" | "executing" | "waiting" | "speaking"
  | "interrupted" | "error" | "sleeping";

export interface TranscriptEntry {
  id: string;
  role: "user" | "assistant";
  text: string;
  ts: number;
}

export interface ActivityEntry {
  id: string;
  ts: number;
  kind: string;
  summary: string;
  detail?: any;
  status?: string;
}

export interface Confirmation {
  confirmId: string;
  tool: string;
  args: any;
  risk: string;
}

export type View = "conversation" | "memory" | "research" | "media" | "browser" | "tasks" | "diagnostics" | "settings";
export type RightPanel = "activity" | "web";

export interface WebResult { title?: string; url: string; snippet?: string; host?: string }
export interface WebState {
  query: string;
  stage: string;
  results: WebResult[];
  read: Record<string, { ok: boolean; title?: string }>;
  error?: string;
  ts: number;
}
export interface MediaState {
  query: string;
  images: { src: string; alt: string; w: number; h: number; page?: string }[];
  ts: number;
}

export interface BrowserState {
  url?: string;
  title?: string;
  text?: string;
  shot?: string | null;
  action?: string;
  error?: string;
  ts: number;
}

export interface ResearchSource {
  title?: string;
  url: string;
}

export interface ResearchRun {
  id: string;
  ts: number;
  query: string;
  stage: "searching" | "reading" | "done";
  sources: ResearchSource[];
  fetched?: number;
  answer?: string;
}

interface Store {
  state: JarvisState;
  view: View;
  wakeMode: string;
  rightPanel: RightPanel;
  web: WebState | null;
  media: MediaState | null;
  browser: BrowserState | null;
  armedUntil: number;      // epoch seconds; follow-up window open while now < armedUntil
  configVersion: number;   // bumps on config_changed so views can refetch
  autoSwitch: boolean;
  transcript: TranscriptEntry[];
  activity: ActivityEntry[];
  confirmation: Confirmation | null;
  assistantDraft: string;
  researchRuns: ResearchRun[];
  setState: (s: JarvisState) => void;
  setView: (v: View) => void;
  setAutoSwitch: (b: boolean) => void;
  onEvent: (evt: any) => void;
  clearConfirmation: () => void;
  hydrateTranscript: (rows: { role: string; content: string }[]) => void;
  setWakeMode: (m: string) => void;
  setRightPanel: (p: RightPanel) => void;
}

let draftId = "";

export const useStore = create<Store>((set, get) => ({
  state: "offline",
  view: "conversation",
  wakeMode: "push_to_talk",
  rightPanel: "activity",
  web: null,
  media: null,
  browser: null,
  armedUntil: 0,
  configVersion: 0,
  autoSwitch: true,
  transcript: [],
  activity: [],
  confirmation: null,
  assistantDraft: "",
  researchRuns: [],

  setState: (s) => set({ state: s }),
  setWakeMode: (m) => set({ wakeMode: m }),
  setRightPanel: (p) => set({ rightPanel: p }),
  setView: (v) => set({ view: v }),
  setAutoSwitch: (b) => set({ autoSwitch: b }),
  clearConfirmation: () => set({ confirmation: null }),
  hydrateTranscript: (rows) =>
    set((st) => st.transcript.length > 0 ? {} : {
      transcript: rows
        .filter((r) => r.role === "user" || r.role === "assistant")
        .map((r, i) => ({
          id: `hist-${i}`,
          role: r.role as "user" | "assistant",
          text: r.content,
          ts: 0,
        })),
    }),

  onEvent: (evt) => {
    const push = (a: ActivityEntry) =>
      set((st) => ({ activity: [...st.activity.slice(-199), a] }));

    switch (evt.kind) {
      case "state":
        set({ state: evt.state, ...(evt.state !== "idle" ? { armedUntil: 0 } : {}) });
        break;
      case "transcript":
        set((st) => ({
          transcript: [
            ...st.transcript,
            { id: evt.id, role: "user", text: evt.text, ts: evt.ts },
          ],
          assistantDraft: "",
          // a new request returns to the conversation; research/media views only
          // take over again if the new turn actually produces that activity
          view: st.autoSwitch && (st.view === "media" || st.view === "research" || st.view === "browser") ? "conversation" : st.view,
          rightPanel: st.rightPanel === "web" && st.autoSwitch ? "activity" : st.rightPanel,
        }));
        draftId = "";
        break;
      case "assistant_delta": {
        if (!draftId) draftId = evt.id;
        set((st) => ({ assistantDraft: st.assistantDraft + evt.text }));
        break;
      }
      case "turn_done": {
        const draft = get().assistantDraft;
        // attach the synthesized answer to a finished research run
        set((st) => {
          const runs = [...st.researchRuns];
          const last = runs[runs.length - 1];
          if (last && last.stage === "done" && !last.answer && draft.trim()) {
            last.answer = draft.trim();
          }
          return { researchRuns: runs };
        });
        if (draft.trim()) {
          set((st) => ({
            transcript: [
              ...st.transcript,
              { id: draftId || evt.id, role: "assistant", text: draft, ts: evt.ts },
            ],
            assistantDraft: "",
          }));
        }
        draftId = "";
        push({ id: evt.id, ts: evt.ts, kind: "turn", summary: `turn complete (${evt.latency_ms} ms)` });
        break;
      }
      case "tool_call":
        push({
          id: evt.id, ts: evt.ts, kind: "tool", status: evt.status,
          summary: `${evt.tool} — ${evt.status}${evt.latency_ms ? ` (${evt.latency_ms} ms)` : ""}`,
          detail: evt.args ?? evt.result,
        });
        break;
      case "confirmation_required":
        set({
          confirmation: {
            confirmId: evt.confirm_id, tool: evt.tool,
            args: evt.args, risk: evt.risk,
          },
        });
        push({ id: evt.id, ts: evt.ts, kind: "confirm", summary: `confirmation: ${evt.tool}` });
        break;
      case "interrupted":
        set({ assistantDraft: "" });
        push({ id: evt.id, ts: evt.ts, kind: "interrupt", summary: "interrupted" });
        break;
      case "boot":
      case "boot_error":
      case "error":
        push({ id: evt.id, ts: evt.ts, kind: evt.kind, summary: evt.summary ?? evt.kind });
        break;
      case "research": {
        const label =
          evt.stage === "searching" ? `research: searching "${evt.query}"` :
          evt.stage === "reading" ? `research: reading ${evt.sources?.length ?? 0} sources` :
          `research: done (${evt.fetched}/${evt.total} sources read)`;
        push({
          id: evt.id, ts: evt.ts, kind: "research", summary: label,
          detail: evt.sources?.map((s: any) => s.title || s.url).join(" · "),
        });
        set((st) => {
          const runs = [...st.researchRuns];
          let run = runs.find((r) => r.query === evt.query && r.stage !== "done");
          if (evt.stage === "searching" || !run) {
            run = { id: evt.id, ts: evt.ts, query: evt.query, stage: evt.stage, sources: [] };
            runs.push(run);
          }
          run.stage = evt.stage;
          if (evt.sources) run.sources = evt.sources;
          if (evt.fetched != null) run.fetched = evt.fetched;
          const web = st.web && st.web.query === evt.query && evt.stage === "done"
            ? { ...st.web, stage: "done" } : st.web;
          return {
            researchRuns: runs.slice(-10),
            web,
            // dynamic view switching: research activity pulls up the research view
            view: st.autoSwitch ? "research" : st.view,
          };
        });
        break;
      }
      case "web": {
        // live web activity takes over the right panel so the user can watch
        set((st) => {
          const prev = st.web && st.web.query === evt.query ? st.web : null;
          const web: WebState = prev ?? { query: evt.query, stage: evt.stage, results: [], read: {}, ts: evt.ts };
          const next: WebState = { ...web, stage: evt.stage, read: { ...web.read } };
          if (evt.results) next.results = evt.results;
          if (evt.stage === "read" && evt.url) {
            next.read[evt.url] = { ok: !!evt.ok, title: evt.title };
            next.stage = "reading";
          }
          if (evt.error) next.error = evt.error;
          return { web: next, rightPanel: "web" };
        });
        push({ id: evt.id, ts: evt.ts, kind: "web", summary: `web: ${evt.stage}${evt.query ? ` "${evt.query}"` : ""}` });
        break;
      }
      case "browser":
        set((st) => ({
          browser: { url: evt.url, title: evt.title, text: evt.text, shot: evt.shot, action: evt.action, error: evt.error, ts: evt.ts },
          view: st.autoSwitch ? "browser" : st.view,
        }));
        push({ id: evt.id, ts: evt.ts, kind: "web", summary: `browser: ${evt.action} ${evt.title ? `"${evt.title}"` : evt.url ?? ""}` });
        break;
      case "images":
        set((st) => ({
          media: { query: evt.query, images: evt.images ?? [], ts: evt.ts },
          view: st.autoSwitch ? "media" : st.view,
        }));
        push({ id: evt.id, ts: evt.ts, kind: "web", summary: `images: ${(evt.images ?? []).length} for "${evt.query}"` });
        break;
      case "reflex":
        push({
          id: evt.id, ts: evt.ts, kind: "reflex",
          summary: `brain: ${evt.skill} (${Math.round((evt.confidence ?? 0) * 100)}%)${evt.mode === "tool_then_llm" ? " → tool, then LLM" : evt.mode === "answer_directly" ? " → LLM answers directly (tools off)" : evt.mode === "answer_hint" ? " → LLM answers directly" : " — no LLM"}`,
          detail: evt.args && Object.keys(evt.args).length ? evt.args : undefined,
        });
        break;
      case "brain_learned":
        push({ id: evt.id, ts: evt.ts, kind: "reflex", summary: `brain learned: "${evt.text}" → ${evt.skill} (${evt.examples} examples)` });
        break;
      case "proactive":
        push({ id: evt.id, ts: evt.ts, kind: "proactive", summary: `proactive: ${evt.alert}`, detail: evt.text });
        break;
      case "task_due":
        push({ id: evt.id, ts: evt.ts, kind: "task", summary: `reminder fired: ${evt.text}` });
        break;
      case "announcement":
        set((st) => ({
          transcript: [
            ...st.transcript,
            { id: evt.id, role: "assistant", text: evt.text, ts: evt.ts },
          ],
        }));
        break;
      case "repair":
        push({ id: evt.id, ts: evt.ts, kind: "repair", summary: `repair ${evt.subsystem}: ${evt.ok ? evt.action : evt.error}` });
        break;
      case "wake":
        push({ id: evt.id, ts: evt.ts, kind: "wake", summary: `wake word (${evt.score})` });
        break;
      case "config_changed":
        push({ id: evt.id, ts: evt.ts, kind: "config", summary: `settings applied: ${(evt.applied ?? []).join(", ") || "saved"}` });
        set((st) => ({ configVersion: st.configVersion + 1 }));
        break;
      case "conversation":
        set({ armedUntil: evt.armed ? Number(evt.until) : 0 });
        break;
      case "speaking":
        break;
      default:
        push({ id: evt.id, ts: evt.ts, kind: evt.kind, summary: evt.kind });
    }
  },
}));
