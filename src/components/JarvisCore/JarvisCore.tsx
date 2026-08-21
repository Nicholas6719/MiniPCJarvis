// The central reactive orb — pure CSS/SVG animation driven by the state machine.
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

export function JarvisCore({ state }: { state: JarvisState }) {
  return (
    <div className={`core core--${state}`}>
      <div className="core__halo" />
      <div className="core__ring core__ring--outer" />
      <div className="core__ring core__ring--mid" />
      <div className="core__ring core__ring--inner" />
      <div className="core__nucleus" />
      <div className="core__label">{STATE_LABEL[state]}</div>
    </div>
  );
}
