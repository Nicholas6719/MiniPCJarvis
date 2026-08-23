// Ambient mode: nothing is happening, so nothing is on screen but the orb, the last
// exchange, and a quiet input line. Panels surface themselves only when JARVIS uses them.
import { useState } from "react";
import { useStore } from "../state/store";
import { api } from "../lib/sidecar";

export function AmbientView() {
  const transcript = useStore((s) => s.transcript);
  const draft = useStore((s) => s.assistantDraft);
  const doing = useStore((s) => s.doing);
  const [input, setInput] = useState("");

  const live = transcript.filter((m) => !m.id.startsWith("hist-"));
  const lastUser = [...live].reverse().find((m) => m.role === "user");
  const lastJarvis = draft ? null : [...live].reverse().find((m) => m.role === "assistant" && (!lastUser || m.ts >= lastUser.ts));

  const send = async () => {
    const text = input.trim();
    if (!text) return;
    setInput("");
    try { await api("/text", { method: "POST", body: JSON.stringify({ text }) }); } catch {}
  };

  return (
    <div className="ambient">
      <div className="ambient__exchange">
        {lastUser && <div className="ambient__you">{lastUser.text}</div>}
        {(draft || lastJarvis) && (
          <div className="ambient__jarvis">{draft || lastJarvis!.text}{draft ? <span className="convo__caret" /> : null}</div>
        )}
        {!lastUser && !draft && <div className="ambient__hint">say "hey Jarvis", press Ctrl+Shift+J, or type</div>}
      </div>
      {doing && <div className="ambient__doing">{doing}</div>}
      <input className="ambient__input" value={input} placeholder=""
             onChange={(e) => setInput(e.target.value)}
             onKeyDown={(e) => { if (e.key === "Enter") send(); }} />
    </div>
  );
}
