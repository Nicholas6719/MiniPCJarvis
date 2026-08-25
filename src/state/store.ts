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

export type View = "conversation" | "memory" | "research" | "media" | "browser" | "files" | "apps" | "system" | "tasks" | "diagnostics" | "settings";
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

export interface FileEntry { name: string; path: string; kind: string; type?: string; size: number; modified?: number }
export interface FilesState {
  path: string | null;
  label: string;
  parent: string | null;
  count: number;
  entries: FileEntry[];
  roots: Record<string, string>;
  query?: string;
  ts: number;
}
export interface FilePreview { path: string; name: string; type: string; text?: string; data?: string; size: number }

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
  files: FilesState | null;
  filePreview: FilePreview | null;
  armedUntil: number;      // epoch seconds; follow-up window open while now < armedUntil
  configVersion: number;   // bumps on config_changed so views can refetch
  autoSwitch: boolean;
  // ---- ambient HUD: panels surface when used, fade back after the turn ----
  ambient: boolean;        // true = only orb + last exchange on screen
  pinned: boolean;         // user pinned the current panel (stays until unpinned)
  panelUntil: number;      // epoch ms; after this (and idle) the HUD returns to ambient
  navVisible: boolean;     // tab bar revealed (mouse at top edge / pinned / voice)
  hovering: boolean;       // mouse is inside the panel: never auto-hide under the cursor
  doing: string;           // one-line "what I'm doing" shown under the orb during a turn
  holdMs: number;          // how long a panel stays after a turn (Settings)
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
  setFilePreview: (p: FilePreview | null) => void;
  surface: (v: View, opts?: { pin?: boolean; hold?: number }) => void;
  collapse: () => void;
  setPinned: (b: boolean) => void;
  setNavVisible: (b: boolean) => void;
  setHovering: (b: boolean) => void;
  setHoldMs: (n: number) => void;
}

// How long a tab you opened yourself stays before the HUD settles back to the orb.
// Longer than the post-turn hold (holdMs, 12 s) because you opened this one to read it.
// The timer only runs while idle and while the cursor is outside the panel, so it never
// closes something you are actively looking at.
const MANUAL_HOLD_MS = 45000;

// Showing a view is never a permanent pin. Every entry point (tab click, "show me the
// files tab" by voice, and the debug/self-test hook) used to set pinned:true, and the
// collapse timer skips anything pinned — so the HUD never found its way back to the orb.
// Only the PIN button pins now; everything else gets a timed hold.
//
// It also no longer forces the tab strip open. Panels surface themselves when JARVIS uses
// them and fade back to the orb on their own, so the tabs are not how you get anywhere —
// they only appear when you deliberately reach for the top edge.
const showView = (v: View) => ({
  view: v, ambient: false, pinned: false,
  panelUntil: Date.now() + MANUAL_HOLD_MS,
});

let draftId = "";
let pendingDelta = "";
let deltaFlush = 0;

function flushDelta(set: any) {
  if (deltaFlush) {
    cancelAnimationFrame(deltaFlush);
    deltaFlush = 0;
  }
  const chunk = pendingDelta;
  pendingDelta = "";
  if (chunk) set((st: any) => ({ assistantDraft: st.assistantDraft + chunk }));
}

export const useStore = create<Store>((set, get) => ({
  state: "offline",
  view: "conversation",
  wakeMode: "push_to_talk",
  rightPanel: "activity",
  web: null,
  media: null,
  browser: null,
  files: null,
  filePreview: null,
  armedUntil: 0,
  configVersion: 0,
  autoSwitch: true,
  ambient: true,
  pinned: false,
  panelUntil: 0,
  navVisible: false,
  hovering: false,
  doing: "",
  holdMs: 12000,
  transcript: [],
  activity: [],
  confirmation: null,
  assistantDraft: "",
  researchRuns: [],

  setState: (s) => set({ state: s }),
  setWakeMode: (m) => set({ wakeMode: m }),
  setRightPanel: (p) => set({ rightPanel: p }),
  setFilePreview: (p) => set({ filePreview: p }),
  setView: (v) => set(showView(v)),
  surface: (v, opts) => set((st) => ({
    view: v, ambient: false,
    pinned: opts?.pin ?? st.pinned,
    panelUntil: Date.now() + (opts?.hold ?? 10 * 60 * 1000),   // held until the turn ends
  })),
  collapse: () => set({ ambient: true, pinned: false, navVisible: false, view: "conversation", rightPanel: "activity", panelUntil: 0 }),
  setPinned: (b) => set({ pinned: b }),
  setNavVisible: (b) => set({ navVisible: b }),
  setHovering: (b) => set({ hovering: b }),
  setHoldMs: (n) => set({ holdMs: n }),
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
      set((st) => ({ activity: [...st.activity.slice(-119), a] }));

    switch (evt.kind) {
      case "state":
        set({ state: evt.state, ...(evt.state !== "idle" ? { armedUntil: 0 } : {}) });
        break;
      case "transcript":
        flushDelta(set);
        set((st) => ({
          transcript: [
            ...st.transcript,
            { id: evt.id, role: "user", text: evt.text, ts: evt.ts },
          ],
          assistantDraft: "",
          doing: "",
        }));
        draftId = "";
        break;
      case "assistant_delta": {
        if (!draftId) draftId = evt.id;
        // Tokens arrive faster than the screen refreshes. Buffer them and commit once per
        // frame: same text, a fraction of the React renders (this is what made streaming
        // answers feel heavy while a panel was open).
        pendingDelta += evt.text;
        if (!deltaFlush) {
          deltaFlush = requestAnimationFrame(() => {
            deltaFlush = 0;
            const chunk = pendingDelta;
            pendingDelta = "";
            if (chunk) set((st) => ({ assistantDraft: st.assistantDraft + chunk }));
          });
        }
        break;
      }
      case "turn_done": {
        flushDelta(set);
        // The streamed deltas are raw model output; turn_done carries the same reply with
        // markdown removed. He is told never to emit any, but "*Jaws*" still slipped
        // through to the transcript. Fall back to the draft for older sidecars.
        const draft = (evt.text as string | undefined)?.trim() || get().assistantDraft;
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
        set((st) => ({ doing: "", panelUntil: st.ambient ? 0 : Date.now() + st.holdMs }));
        break;
      }
      case "tool_call":
        if (evt.status === "pending") set({ doing: `${String(evt.tool).replace(/_/g, " ")}…` });
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
        flushDelta(set);
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
            ambient: st.autoSwitch ? false : st.ambient,
            panelUntil: Date.now() + 10 * 60 * 1000,
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
          return {
            web: next, rightPanel: "web", ambient: false,
            // Claim the main panel, the way files and media do. Without this the view
            // stayed on whatever the LAST turn opened — asking for research while the
            // files panel was up left the files there and squeezed the results into a
            // narrow strip beside them. Research runs keep their own view.
            view: st.autoSwitch ? (st.view === "research" ? "research" : "conversation") : st.view,
            panelUntil: Date.now() + 10 * 60 * 1000,
          };
        });
        push({ id: evt.id, ts: evt.ts, kind: "web", summary: `web: ${evt.stage}${evt.query ? ` "${evt.query}"` : ""}` });
        break;
      }
      case "browser":
        set((st) => ({
          browser: { url: evt.url, title: evt.title, text: evt.text, shot: evt.shot, action: evt.action, error: evt.error, ts: evt.ts },
          view: st.autoSwitch ? "browser" : st.view,
          ambient: st.autoSwitch ? false : st.ambient, panelUntil: Date.now() + 10 * 60 * 1000,
        }));
        push({ id: evt.id, ts: evt.ts, kind: "web", summary: `browser: ${evt.action} ${evt.title ? `"${evt.title}"` : evt.url ?? ""}` });
        break;
      case "files":
        set((st) => ({
          files: { path: evt.path, label: evt.label, parent: evt.parent, count: evt.count, entries: evt.entries ?? [], roots: evt.roots ?? {}, query: evt.query, ts: evt.ts },
          view: st.autoSwitch ? "files" : st.view,
          ambient: st.autoSwitch ? false : st.ambient, panelUntil: Date.now() + 10 * 60 * 1000,
        }));
        push({ id: evt.id, ts: evt.ts, kind: "files", summary: `files: ${evt.label} (${evt.count})` });
        break;
      case "file_preview":
        set((st) => ({ filePreview: { path: evt.path, name: evt.name, type: evt.type, text: evt.text, data: evt.data, size: evt.size },
                       view: st.autoSwitch ? "files" : st.view,
                       ambient: st.autoSwitch ? false : st.ambient, panelUntil: Date.now() + 10 * 60 * 1000 }));
        push({ id: evt.id, ts: evt.ts, kind: "files", summary: `preview: ${evt.name}` });
        break;
      case "images":
        set((st) => ({
          media: { query: evt.query, images: evt.images ?? [], ts: evt.ts },
          view: st.autoSwitch ? "media" : st.view,
          // The image search announces itself as a "web" stage so the progress shows,
          // which parks an empty WEB panel beside the pictures once they arrive. The
          // pictures ARE the result — drop the progress panel and give them the room.
          rightPanel: "activity",
          ambient: st.autoSwitch ? false : st.ambient, panelUntil: Date.now() + 10 * 60 * 1000,
        }));
        push({ id: evt.id, ts: evt.ts, kind: "web", summary: `images: ${(evt.images ?? []).length} for "${evt.query}"` });
        break;
      case "reflex":
        if (evt.skill && evt.skill !== "general") set({ doing: String(evt.skill).replace(/_/g, " ") });
        push({
          id: evt.id, ts: evt.ts, kind: "reflex",
          summary: `brain: ${evt.skill} (${Math.round((evt.confidence ?? 0) * 100)}%)${evt.mode === "tool_then_llm" ? " → tool, then LLM" : evt.mode === "answer_directly" ? " → LLM answers directly (tools off)" : evt.mode === "answer_hint" ? " → LLM answers directly" : " — no LLM"}`,
          detail: evt.args && Object.keys(evt.args).length ? evt.args : undefined,
        });
        break;
      case "brain_learned":
        push({ id: evt.id, ts: evt.ts, kind: "reflex", summary: `brain learned: "${evt.text}" → ${evt.skill} (${evt.examples} examples)` });
        break;
      case "filler":
        push({ id: evt.id, ts: evt.ts, kind: "speaking", summary: `filler: "${evt.text}"` });
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
      case "set_view":   // debug/remote: switch the HUD view (used by UI self-tests)
        set(showView(evt.view as View));
        break;
      case "ui": {       // voice: "show the files tab" / "show me the tabs" / "pin that" / "hide everything"
        const a = evt.action;
        if (a === "show" && evt.view) set(showView(evt.view as View));
        else if (a === "tabs") set((st) => ({ navVisible: true, ambient: false, panelUntil: Date.now() + st.holdMs * 2 }));
        else if (a === "pin") set({ pinned: true, ambient: false });
        else if (a === "unpin") set((st) => ({ pinned: false, panelUntil: Date.now() + st.holdMs }));
        else if (a === "hide") set({ ambient: true, pinned: false, navVisible: false, view: "conversation", rightPanel: "activity", panelUntil: 0 });
        break;
      }
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
