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

export type View = "conversation" | "memory" | "research" | "tasks" | "diagnostics" | "settings";

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
}

let draftId = "";

export const useStore = create<Store>((set, get) => ({
  state: "offline",
  view: "conversation",
  autoSwitch: true,
  transcript: [],
  activity: [],
  confirmation: null,
  assistantDraft: "",
  researchRuns: [],

  setState: (s) => set({ state: s }),
  setView: (v) => set({ view: v }),
  setAutoSwitch: (b) => set({ autoSwitch: b }),
  clearConfirmation: () => set({ confirmation: null }),

  onEvent: (evt) => {
    const push = (a: ActivityEntry) =>
      set((st) => ({ activity: [...st.activity.slice(-199), a] }));

    switch (evt.kind) {
      case "state":
        set({ state: evt.state });
        break;
      case "transcript":
        set((st) => ({
          transcript: [
            ...st.transcript,
            { id: evt.id, role: "user", text: evt.text, ts: evt.ts },
          ],
          assistantDraft: "",
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
          return {
            researchRuns: runs.slice(-10),
            // dynamic view switching: research activity pulls up the research view
            view: st.autoSwitch ? "research" : st.view,
          };
        });
        break;
      }
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
        break;
      case "speaking":
        break;
      default:
        push({ id: evt.id, ts: evt.ts, kind: evt.kind, summary: evt.kind });
    }
  },
}));
