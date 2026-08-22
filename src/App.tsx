import { useEffect } from "react";
import { JarvisCore } from "./components/JarvisCore/JarvisCore";
import { ConversationView } from "./components/ConversationView";
import { ActivityLog } from "./components/ActivityLog";
import { ConfirmationModal } from "./components/ConfirmationModal";
import { MemoryView } from "./components/MemoryView";
import { SettingsView } from "./components/SettingsView";
import { ResearchView } from "./components/ResearchView";
import { TasksView } from "./components/TasksView";
import { DiagnosticsView } from "./components/DiagnosticsView";
import { StatusBar } from "./components/StatusBar";
import { WebPanel } from "./components/WebPanel";
import { MediaView } from "./components/MediaView";
import { BrowserView } from "./components/BrowserView";
import { FilesView } from "./components/FilesView";
import { BootOverlay, FirstRunSetup } from "./components/FirstRun";
import { useStore, View } from "./state/store";
import { connectEvents, api } from "./lib/sidecar";

const VIEWS: { id: View; label: string }[] = [
  { id: "conversation", label: "CONVERSATION" },
  { id: "research", label: "RESEARCH" },
  { id: "media", label: "MEDIA" },
  { id: "browser", label: "BROWSER" },
  { id: "files", label: "FILES" },
  { id: "memory", label: "MEMORY" },
  { id: "tasks", label: "TASKS" },
  { id: "diagnostics", label: "DIAGNOSTICS" },
  { id: "settings", label: "SETTINGS" },
];

export default function App() {
  const state = useStore((s) => s.state);
  const view = useStore((s) => s.view);
  const setView = useStore((s) => s.setView);
  const onEvent = useStore((s) => s.onEvent);
  const wakeMode = useStore((s) => s.wakeMode);
  const armedUntil = useStore((s) => s.armedUntil);
  const configVersion = useStore((s) => s.configVersion);
  const rightPanel = useStore((s) => s.rightPanel);

  useEffect(() => connectEvents(onEvent), [onEvent]);

  // keep the orb's mode label in sync with settings
  useEffect(() => {
    (async () => {
      try {
        const r = await api("/config");
        useStore.getState().setWakeMode(r.config?.wake?.mode ?? "push_to_talk");
      } catch {}
    })();
  }, [configVersion, state === "idle"]);

  // hydrate conversation history so restarting the window keeps continuity
  useEffect(() => {
    (async () => {
      try {
        const r = await api("/transcript");
        useStore.getState().hydrateTranscript(r.transcript);
      } catch {}
    })();
  }, []);

  const micClick = async () => {
    try {
      await api("/listen/toggle", { method: "POST" });
    } catch {}
  };

  return (
    <div className="hud">
      <div className="hud__grid" />
      <header className="hud__header">
        <span className="hud__logo">J.A.R.V.I.S.</span>
        <span className="hud__sub">Just A Rather Very Intelligent System</span>
        <nav className="hud__nav">
          {VIEWS.map((v) => (
            <button key={v.id}
                    className={`hud__navbtn ${view === v.id ? "is-active" : ""}`}
                    onClick={() => setView(v.id)}>
              {v.label}
            </button>
          ))}
        </nav>
      </header>
      <main className="hud__main">
        <section className="hud__left">
          <button className="hud__corebtn" onClick={micClick} title="Toggle listening (Ctrl+Shift+J)">
            <JarvisCore state={state} wakeMode={wakeMode} armedUntil={armedUntil} />
          </button>
        </section>
        <section className="hud__center">
          {view === "conversation" && <ConversationView />}
          {view === "research" && <ResearchView />}
          {view === "media" && <MediaView />}
          {view === "browser" && <BrowserView />}
          {view === "files" && <FilesView />}
          {view === "memory" && <MemoryView />}
          {view === "tasks" && <TasksView />}
          {view === "diagnostics" && <DiagnosticsView />}
          {view === "settings" && <SettingsView />}
        </section>
        <section className="hud__right">
          {rightPanel === "web" ? <WebPanel /> : <ActivityLog />}
        </section>
      </main>
      <StatusBar />
      <ConfirmationModal />
      <FirstRunSetup />
      <BootOverlay />
    </div>
  );
}
