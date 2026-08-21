import { useEffect } from "react";
import { JarvisCore } from "./components/JarvisCore/JarvisCore";
import { ConversationView } from "./components/ConversationView";
import { ActivityLog } from "./components/ActivityLog";
import { ConfirmationModal } from "./components/ConfirmationModal";
import { useStore } from "./state/store";
import { connectEvents, api } from "./lib/sidecar";

export default function App() {
  const state = useStore((s) => s.state);
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
      </header>
      <main className="hud__main">
        <section className="hud__left">
          <button className="hud__corebtn" onClick={micClick} title="Toggle listening (Ctrl+Shift+J)">
            <JarvisCore state={state} />
          </button>
        </section>
        <section className="hud__center">
          <ConversationView />
        </section>
        <section className="hud__right">
          <ActivityLog />
        </section>
      </main>
      <ConfirmationModal />
    </div>
  );
}
