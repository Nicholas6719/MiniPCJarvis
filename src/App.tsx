import { useEffect } from "react";
import { JarvisCore } from "./components/JarvisCore/JarvisCore";
import { ConversationView } from "./components/ConversationView";
import { ActivityLog } from "./components/ActivityLog";
import { ConfirmationModal } from "./components/ConfirmationModal";
import { MemoryView } from "./components/MemoryView";
import { SettingsView } from "./components/SettingsView";
import { BootOverlay, FirstRunSetup } from "./components/FirstRun";
import { useStore, View } from "./state/store";
import { connectEvents, api } from "./lib/sidecar";

const VIEWS: { id: View; label: string }[] = [
  { id: "conversation", label: "CONVERSATION" },
  { id: "memory", label: "MEMORY" },
  { id: "settings", label: "SETTINGS" },
];

export default function App() {
  const state = useStore((s) => s.state);
  const view = useStore((s) => s.view);
  const setView = useStore((s) => s.setView);
  const onEvent = useStore((s) => s.onEvent);

  useEffect(() => connectEvents(onEvent), [onEvent]);

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
            <JarvisCore state={state} />
          </button>
        </section>
        <section className="hud__center">
          {view === "conversation" && <ConversationView />}
          {view === "memory" && <MemoryView />}
          {view === "settings" && <SettingsView />}
        </section>
        <section className="hud__right">
          <ActivityLog />
        </section>
      </main>
      <ConfirmationModal />
      <FirstRunSetup />
      <BootOverlay />
    </div>
  );
}
