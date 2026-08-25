// The central reactive orb — pure CSS/SVG animation driven by the state machine.
// The idle label reflects HOW JARVIS is listening, not just that it's idle.
import { useEffect, useState } from "react";
import { JarvisState } from "../../state/store";
import "./JarvisCore.css";

const STATE_LABEL: Record<JarvisState, string> = {
  offline: "OFFLINE",
  starting: "INITIALIZING",
  idle: "STANDING BY",
  listening: "LISTENING",
  processing: "PROCESSING",
  thinking: "THINKING",
  searching: "SEARCHING",
  executing: "EXECUTING",
  waiting: "AWAITING CONFIRMATION",
  speaking: "SPEAKING",
  interrupted: "INTERRUPTED",
  error: "FAULT",
  sleeping: "SLEEPING",
};

interface Props {
  state: JarvisState;
  wakeMode?: string;
  armedUntil?: number; // epoch seconds
}

export function JarvisCore({ state, wakeMode = "push_to_talk", armedUntil = 0 }: Props) {
  const [now, setNow] = useState(Date.now() / 1000);

  // tick while the follow-up window is open so the countdown is live
  useEffect(() => {
    if (state !== "idle" || armedUntil <= now) return;
    const t = setInterval(() => setNow(Date.now() / 1000), 250);
    return () => clearInterval(t);
  }, [state, armedUntil, now]);

  // A running CSS animation keeps the compositor at 60 fps forever. After a stretch of
  // real idle, settle the orb completely still; any state change wakes it instantly.
  const [calm, setCalm] = useState(false);
  useEffect(() => {
    setCalm(false);
    if (state !== "idle" && state !== "sleeping") return;
    const t = setTimeout(() => setCalm(true), 20000);
    return () => clearTimeout(t);
  }, [state]);

  const armed = state === "idle" && armedUntil > now;
  const remaining = Math.max(0, Math.ceil(armedUntil - now));

  let label = STATE_LABEL[state];
  let sub = "";
  let modeClass = "";
  if (state === "idle") {
    if (armed) {
      label = "CONVERSATION";
      sub = `listening · ${remaining}s`;
      modeClass = "core--armed";
    } else if (wakeMode === "wake_word") {
      label = "WAKE MODE";
      sub = 'say "hey jarvis"';
    } else if (wakeMode === "both") {
      label = "WAKE MODE";
      sub = '"hey jarvis" · or ctrl+shift+j';
    } else {
      label = "STANDING BY";
      sub = "ctrl+shift+j to talk";
    }
  } else if (state === "sleeping") {
    // He is minimised and out of the way; say how to get him back.
    sub = wakeMode === "push_to_talk" ? "ctrl+shift+j to wake" : '"jarvis" · or ctrl+shift+j';
  }

  return (
    <div className={`core core--${state} ${modeClass} ${calm && !armed ? "core--calm" : ""}`}>
      <div className="core__halo" />
      <div className="core__ring core__ring--outer" />
      <div className="core__ring core__ring--mid" />
      <div className="core__ring core__ring--inner" />
      <div className="core__glow" />
      <div className="core__nucleus" />
      <div className="core__label">{label}</div>
      {sub && <div className="core__sublabel">{sub}</div>}
    </div>
  );
}
