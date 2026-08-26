import { create } from "zustand";

export type JarvisState =
  | "offline" | "starting" | "idle" | "listening" | "processing"
  | "thinking" | "searching" | "executing" | "waiting" | "speaking"
  | "interrupted" | "error" | "sleeping";

// ---------------------------------------------------------------------------
// The stage: one box beside the core whose CONTENT TYPE changes with the task.
// There is nothing to navigate to — the utterance (via sidecar events) selects
// the renderer. This replaces the eleven view tabs outright.
// ---------------------------------------------------------------------------
export type StageKind =
  | "prose"      // the answer, at 40px
  | "browser"    // live web work: results, the action marker, read progression
  | "images"     // grid, four across
  | "file"       // the file, open, matches lit
  | "folder"     // a folder listing (no designed surface; kept minimal)
  | "settings";  // settings rail incl. History

export type SettingsSection =
  | "voice" | "model" | "tools" | "memory" | "history" | "tasks" | "about";

export interface StageState {
  kind: StageKind;
  openedTs: number;
  holdUntil: number;      // epoch ms; 0 = held by activity (turn still running)
  pinned: boolean;        // "keep it" — stays until dismissed
  settingsSection?: SettingsSection;
}

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

export interface WebResult { title?: string; url: string; snippet?: string; host?: string }
export interface WebState {
  query: string;
  stage: string;                                  // searching | results | reading | done | error
  results: WebResult[];
  read: Record<string, { ok: boolean; title?: string }>;
  opening?: string;                               // url of the result being opened right now
  error?: string;
  ts: number;
}

export interface ImagesState {
  query: string;
  images: { src: string; alt: string; w: number; h: number; page?: string }[];
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

// The run timeline: four steps, left to right. Every dot is a real event.
export type StepStatus = "done" | "active" | "pending";
export interface RunStep {
  label: string;
  sub: string;
  status: StepStatus;
  kind: "stt" | "reflex" | "tool" | "tts";
}

export interface TurnState {
  userText: string;
  startedTs: number;       // epoch ms of the user transcript
  elapsedMs: number | null; // set on turn_done
  steps: RunStep[];
}

interface Store {
  state: JarvisState;
  wakeMode: string;
  stage: StageState | null;
  web: WebState | null;
  images: ImagesState | null;
  files: FilesState | null;
  filePreview: FilePreview | null;
  turn: TurnState | null;
  armedUntil: number;
  configVersion: number;
  transcript: TranscriptEntry[];
  activity: ActivityEntry[];
  confirmation: Confirmation | null;
  assistantDraft: string;
  onEvent: (evt: any) => void;
  clearConfirmation: () => void;
  hydrateTranscript: (rows: { role: string; content: string }[]) => void;
  setWakeMode: (m: string) => void;
  setState: (s: JarvisState) => void;
  setFilePreview: (p: FilePreview | null) => void;
  openStage: (kind: StageKind, extra?: Partial<StageState>) => void;
  dismissStage: () => void;
  pinStage: (pinned: boolean) => void;
  setSettingsSection: (s: SettingsSection) => void;
}

// After the answer is spoken the stage holds this long, then the core comes home.
// Signalled by the drain bar; "keep it" pins. (§6.3 — deliberately quiet.)
export const STAGE_HOLD_MS = 5000;
// A surface the user asked for by voice ("show settings") holds much longer.
const ASKED_FOR_HOLD_MS = 120000;

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

const stepDefaults = (): RunStep[] => [
  { label: "Heard you", sub: "PARAKEET", status: "active", kind: "stt" },
  { label: "Deciding", sub: "BRAIN", status: "pending", kind: "reflex" },
  { label: "Working", sub: "", status: "pending", kind: "tool" },
  { label: "Speak it", sub: "KOKORO", status: "pending", kind: "tts" },
];

function patchStep(turn: TurnState | null, i: number, patch: Partial<RunStep>): TurnState | null {
  if (!turn) return turn;
  const steps = turn.steps.map((s, j) => (j === i ? { ...s, ...patch } : s));
  // everything before an active/done step is done
  for (let j = 0; j < i; j++) if (steps[j].status !== "done") steps[j] = { ...steps[j], status: "done" };
  return { ...turn, steps };
}

export const useStore = create<Store>((set, get) => ({
  state: "offline",
  wakeMode: "push_to_talk",
  stage: null,
  web: null,
  images: null,
  files: null,
  filePreview: null,
  turn: null,
  armedUntil: 0,
  configVersion: 0,
  transcript: [],
  activity: [],
  confirmation: null,
  assistantDraft: "",

  setState: (s) => set({ state: s }),
  setWakeMode: (m) => set({ wakeMode: m }),
  setFilePreview: (p) => set({ filePreview: p }),

  openStage: (kind, extra) =>
    set((st) => ({
      stage: {
        kind,
        openedTs: Date.now(),
        holdUntil: 0,
        pinned: false,
        settingsSection: st.stage?.settingsSection,
        ...extra,
      },
    })),
  dismissStage: () => set({ stage: null }),
  pinStage: (pinned) =>
    set((st) => (st.stage ? { stage: { ...st.stage, pinned, holdUntil: 0 } } : {})),
  setSettingsSection: (s) =>
    set((st) => ({
      stage: {
        kind: "settings",
        openedTs: st.stage?.openedTs ?? Date.now(),
        holdUntil: Date.now() + ASKED_FOR_HOLD_MS,
        pinned: st.stage?.pinned ?? false,
        settingsSection: s,
      },
    })),

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
      case "state": {
        set((st) => {
          const next: Partial<Store> = { state: evt.state };
          if (evt.state !== "idle") next.armedUntil = 0;
          // Speaking finished → the stage holds 5 s then collapses (unless pinned or
          // the user asked for this surface explicitly).
          if (evt.state === "idle" && st.stage && !st.stage.pinned && !st.stage.holdUntil) {
            next.stage = { ...st.stage, holdUntil: Date.now() + STAGE_HOLD_MS };
          }
          return next;
        });
        break;
      }
      case "transcript":
        flushDelta(set);
        set((st) => ({
          transcript: [
            ...st.transcript,
            { id: evt.id, role: "user", text: evt.text, ts: evt.ts },
          ],
          assistantDraft: "",
          turn: { userText: evt.text, startedTs: Date.now(), elapsedMs: null, steps: stepDefaults() },
          // a new turn reclaims the stage: whatever we open next owns it
          stage: st.stage?.pinned ? st.stage : null,
        }));
        draftId = "";
        break;
      case "assistant_delta": {
        if (!draftId) draftId = evt.id;
        // Tokens arrive faster than the screen refreshes; commit once per frame.
        pendingDelta += evt.text;
        if (!deltaFlush) {
          deltaFlush = requestAnimationFrame(() => {
            deltaFlush = 0;
            const chunk = pendingDelta;
            pendingDelta = "";
            if (chunk) set((st) => ({ assistantDraft: st.assistantDraft + chunk }));
          });
        }
        // an answer streaming with no visual stage = the prose stage
        set((st) => ({
          stage: st.stage ?? { kind: "prose", openedTs: Date.now(), holdUntil: 0, pinned: false },
          turn: patchStep(st.turn, 3, { status: "active", label: "Speaking it" }),
        }));
        break;
      }
      case "turn_done": {
        flushDelta(set);
        const draft = (evt.text as string | undefined)?.trim() || get().assistantDraft;
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
        set((st) => ({
          turn: st.turn
            ? {
                ...st.turn,
                elapsedMs: evt.latency_ms ?? Date.now() - st.turn.startedTs,
                steps: st.turn.steps.map((s) => ({ ...s, status: "done" as StepStatus })),
              }
            : st.turn,
        }));
        push({ id: evt.id, ts: evt.ts, kind: "turn", summary: `turn complete (${evt.latency_ms} ms)` });
        break;
      }
      case "tool_call":
        if (evt.status === "pending") {
          set((st) => ({
            turn: patchStep(st.turn, 2, {
              status: "active",
              label: String(evt.tool).replace(/_/g, " "),
              sub: String(evt.risk ?? "").toUpperCase(),
            }),
          }));
        } else {
          set((st) => ({
            turn: patchStep(st.turn, 2, {
              status: "done",
              sub: evt.latency_ms ? `${evt.latency_ms} ms` : "",
            }),
          }));
        }
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
      case "research":
        // research progression rides the same web state (browser stage)
        set((st) => {
          const prev = st.web && st.web.query === evt.query ? st.web : null;
          const web: WebState = prev ?? { query: evt.query, stage: evt.stage, results: [], read: {}, ts: evt.ts };
          const next: WebState = { ...web, stage: evt.stage };
          if (evt.sources) {
            next.results = evt.sources.map((s: any) => ({
              title: s.title, url: s.url,
              host: s.url ? new URL(s.url).hostname.replace(/^www\./, "") : "",
            }));
          }
          return {
            web: next,
            stage: st.stage?.kind === "browser" ? st.stage
              : { kind: "browser" as StageKind, openedTs: Date.now(), holdUntil: 0, pinned: st.stage?.pinned ?? false },
          };
        });
        push({ id: evt.id, ts: evt.ts, kind: "research", summary: `research: ${evt.stage} "${evt.query ?? ""}"` });
        break;
      case "web": {
        set((st) => {
          const prev = st.web && st.web.query === evt.query ? st.web : null;
          const web: WebState = prev ?? { query: evt.query, stage: evt.stage, results: [], read: {}, ts: evt.ts };
          const next: WebState = { ...web, stage: evt.stage, read: { ...web.read } };
          if (evt.results) next.results = evt.results;
          if (evt.stage === "read" && evt.url) {
            next.read[evt.url] = { ok: !!evt.ok, title: evt.title };
            next.stage = "reading";
            next.opening = undefined;
          }
          if (evt.stage === "opening" && evt.url) next.opening = evt.url;
          if (evt.error) next.error = evt.error;
          return {
            web: next,
            stage: { kind: "browser" as StageKind, openedTs: st.stage?.openedTs ?? Date.now(), holdUntil: 0, pinned: st.stage?.pinned ?? false },
            turn: patchStep(st.turn, 2, {
              status: evt.stage === "done" ? "done" : "active",
              label:
                evt.stage === "searching" ? "Searching the web" :
                evt.stage === "reading" || evt.stage === "read" ? "Reading sources" :
                evt.stage === "done" ? "Read the sources" : "Working the web",
              sub: "KEYLESS BRAVE",
            }),
          };
        });
        push({ id: evt.id, ts: evt.ts, kind: "web", summary: `web: ${evt.stage}${evt.query ? ` "${evt.query}"` : ""}` });
        break;
      }
      case "browser":
        push({ id: evt.id, ts: evt.ts, kind: "web", summary: `browser: ${evt.action} ${evt.title ? `"${evt.title}"` : evt.url ?? ""}` });
        break;
      case "files":
        set((st) => ({
          files: { path: evt.path, label: evt.label, parent: evt.parent, count: evt.count, entries: evt.entries ?? [], roots: evt.roots ?? {}, query: evt.query, ts: evt.ts },
          stage: { kind: "folder" as StageKind, openedTs: Date.now(), holdUntil: 0, pinned: st.stage?.pinned ?? false },
        }));
        push({ id: evt.id, ts: evt.ts, kind: "files", summary: `files: ${evt.label} (${evt.count})` });
        break;
      case "file_preview":
        set((st) => ({
          filePreview: { path: evt.path, name: evt.name, type: evt.type, text: evt.text, data: evt.data, size: evt.size },
          stage: { kind: "file" as StageKind, openedTs: Date.now(), holdUntil: 0, pinned: st.stage?.pinned ?? false },
        }));
        push({ id: evt.id, ts: evt.ts, kind: "files", summary: `preview: ${evt.name}` });
        break;
      case "images":
        set((st) => ({
          images: { query: evt.query, images: evt.images ?? [], ts: evt.ts },
          stage: { kind: "images" as StageKind, openedTs: Date.now(), holdUntil: 0, pinned: st.stage?.pinned ?? false },
        }));
        push({ id: evt.id, ts: evt.ts, kind: "web", summary: `images: ${(evt.images ?? []).length} for "${evt.query}"` });
        break;
      case "reflex":
        set((st) => ({
          turn: patchStep(st.turn, 1, {
            status: "done",
            label: evt.skill === "general" ? "Straight to the model" : `Reflex matched ${evt.skill}`,
            sub: `${Math.round((evt.confidence ?? 0) * 100)}%${evt.mode === "direct" ? " · MODEL NEVER WOKE" : ""}`,
          }),
        }));
        push({
          id: evt.id, ts: evt.ts, kind: "reflex",
          summary: `brain: ${evt.skill} (${Math.round((evt.confidence ?? 0) * 100)}%)`,
          detail: evt.args && Object.keys(evt.args).length ? evt.args : undefined,
        });
        break;
      case "brain_learned":
        push({ id: evt.id, ts: evt.ts, kind: "reflex", summary: `brain learned: "${evt.text}" → ${evt.skill}` });
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
      case "set_view":
      case "ui": {
        // Voice / debug surface control, mapped onto the stage system. The eleven
        // views are gone; the old names land on the nearest designed surface.
        const action = evt.kind === "set_view" ? "show" : evt.action;
        const view = String(evt.view ?? "");
        const sectionFor: Record<string, SettingsSection> = {
          settings: "voice", memory: "memory", tasks: "tasks",
          system: "about", diagnostics: "about", history: "history",
        };
        if (action === "show") {
          if (sectionFor[view]) get().setSettingsSection(sectionFor[view]);
          else if (view === "media") get().openStage("images", { holdUntil: Date.now() + ASKED_FOR_HOLD_MS });
          else if (view === "files") get().openStage("folder", { holdUntil: Date.now() + ASKED_FOR_HOLD_MS });
          else if (view === "browser" || view === "research") get().openStage("browser", { holdUntil: Date.now() + ASKED_FOR_HOLD_MS });
          else if (view === "conversation") get().openStage("prose", { holdUntil: Date.now() + ASKED_FOR_HOLD_MS });
        } else if (action === "pin") get().pinStage(true);
        else if (action === "unpin") get().pinStage(false);
        else if (action === "hide") get().dismissStage();
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
