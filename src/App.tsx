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
import { AppsView } from "./components/AppsView";
import { SystemView } from "./components/SystemView";
import { BootOverlay, FirstRunSetup } from "./components/FirstRun";
import { AmbientView } from "./components/AmbientView";
import { useStore, View } from "./state/store";
import { connectEvents, api } from "./lib/sidecar";

// Icon-first tabs: the glyph is the control, the name is the tooltip (and is shown for
// whichever tab is active, so you never have to guess what you are looking at).
const VIEWS: { id: View; label: string; icon: string }[] = [
  { id: "conversation", label: "CONVERSATION", icon: "◈" },
  { id: "research", label: "RESEARCH", icon: "◎" },
  { id: "media", label: "MEDIA", icon: "▣" },
  { id: "browser", label: "BROWSER", icon: "◐" },
  { id: "files", label: "FILES", icon: "▤" },
  { id: "apps", label: "APPS", icon: "▦" },
  { id: "system", label: "SYSTEM", icon: "◍" },
  { id: "memory", label: "MEMORY", icon: "❖" },
  { id: "tasks", label: "TASKS", icon: "◔" },
  { id: "diagnostics", label: "DIAGNOSTICS", icon: "⌁" },
  { id: "settings", label: "SETTINGS", icon: "⚙" },
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
  const ambient = useStore((s) => s.ambient);
  const pinned = useStore((s) => s.pinned);
  const navVisible = useStore((s) => s.navVisible);
  const setNavVisible = useStore((s) => s.setNavVisible);
  const setHovering = useStore((s) => s.setHovering);
  const setPinned = useStore((s) => s.setPinned);
  const collapse = useStore((s) => s.collapse);

  useEffect(() => connectEvents(onEvent), [onEvent]);

  // don't animate a window nobody is looking at
  useEffect(() => {
    const sync = () => document.body.classList.toggle("is-hidden", document.hidden);
    sync();
    document.addEventListener("visibilitychange", sync);
    return () => document.removeEventListener("visibilitychange", sync);
  }, []);

  // fade back to ambient once the turn is over, the hold has elapsed, nothing is pinned,
  // and the cursor isn't inside the panel
  useEffect(() => {
    const t = setInterval(() => {
      const st = useStore.getState();
      if (st.ambient || st.pinned || st.hovering) return;
      if (st.state !== "idle") return;
      if (st.panelUntil && Date.now() > st.panelUntil) st.collapse();
    }, 500);
    return () => clearInterval(t);
  }, []);

  // the tab bar lives at the top edge: reveal on approach, hide when the cursor leaves
  useEffect(() => {
    let hideTimer: number | undefined;
    const onMove = (e: MouseEvent) => {
      const st = useStore.getState();
      if (e.clientY < 60) { if (!st.navVisible) setNavVisible(true); if (hideTimer) { window.clearTimeout(hideTimer); hideTimer = undefined; } }
      else if (st.navVisible && !st.pinned && e.clientY > 140 && !hideTimer) {
        hideTimer = window.setTimeout(() => { setNavVisible(false); hideTimer = undefined; }, 1500);
      }
    };
    window.addEventListener("mousemove", onMove);
    return () => window.removeEventListener("mousemove", onMove);
  }, []);

  // settings: hold time
  useEffect(() => {
    (async () => {
      try {
        const r = await api("/config");
        const secs = Number(r.config?.ui?.panel_hold_s ?? 12);
        useStore.getState().setHoldMs(Math.max(3, secs) * 1000);
      } catch {}
    })();
  }, [configVersion]);

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

  const showNav = navVisible || pinned;
  return (
    <div className={`hud ${ambient ? "hud--ambient" : "hud--panel"} ${showNav ? "hud--nav" : ""}`}>
      <div className="hud__grid" />
      <header className="hud__header">
        <span className="hud__logo">J.A.R.V.I.S.</span>
        <nav className="hud__nav">
          {VIEWS.map((v) => (
            <button key={v.id}
                    title={v.label}
                    aria-label={v.label}
                    className={`hud__navbtn hud__navicon ${view === v.id ? "is-active" : ""}`}
                    onClick={() => setView(v.id)}>
              <span className="hud__glyph">{v.icon}</span>
              {view === v.id && <span className="hud__navname">{v.label}</span>}
            </button>
          ))}
          {!ambient && (
            <button className={`hud__navbtn hud__pin ${pinned ? "is-active" : ""}`} title={pinned ? "Unpin: fade back when done" : "Pin: keep this panel"}
                    onClick={() => (pinned ? (setPinned(false), collapse()) : setPinned(true))}>{pinned ? "PINNED" : "PIN"}</button>
          )}
        </nav>
      </header>
      <main className="hud__main">
        <section className="hud__left">
          <button className="hud__corebtn" onClick={micClick} title="Toggle listening (Ctrl+Shift+J)">
            <JarvisCore state={state} wakeMode={wakeMode} armedUntil={armedUntil} />
          </button>
          {!ambient && <div className="hud__sidechat"><AmbientView compact /></div>}
        </section>
        {ambient && <section className="hud__ambient"><AmbientView /></section>}
        {!ambient && (
        <section className="hud__center" onMouseEnter={() => setHovering(true)} onMouseLeave={() => setHovering(false)}>
          {view === "conversation" && (rightPanel === "web" && !pinned ? <WebPanel /> : <ConversationView />)}
          {view === "research" && <ResearchView />}
          {view === "media" && <MediaView />}
          {view === "browser" && <BrowserView />}
          {view === "files" && <FilesView />}
          {view === "apps" && <AppsView />}
          {view === "system" && <SystemView />}
          {view === "memory" && <MemoryView />}
          {view === "tasks" && <TasksView />}
          {view === "diagnostics" && <DiagnosticsView />}
          {view === "settings" && <SettingsView />}
        </section>
        )}
        {!ambient && rightPanel === "web" && (pinned || view !== "conversation") && (
          <section className="hud__right" onMouseEnter={() => setHovering(true)} onMouseLeave={() => setHovering(false)}>
            <WebPanel />
          </section>
        )}
        {!ambient && rightPanel !== "web" && view === "diagnostics" && (
          <section className="hud__right"><ActivityLog /></section>
        )}
      </main>
      <StatusBar />
      <ConfirmationModal />
      <FirstRunSetup />
      <BootOverlay />
    </div>
  );
}
