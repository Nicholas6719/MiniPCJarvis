import { useEffect, useRef, useState } from "react";
import { useStore } from "../state/store";
import { api } from "../lib/sidecar";

export function ConversationView() {
  const transcript = useStore((s) => s.transcript);
  const draft = useStore((s) => s.assistantDraft);
  const scrollRef = useRef<HTMLDivElement>(null);
  const [input, setInput] = useState("");

  useEffect(() => {
    const toBottom = () => scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
    toBottom();
    // hydrated history paints after fonts/layout settle — scroll again shortly
    const r = requestAnimationFrame(toBottom);
    const t = setTimeout(toBottom, 250);
    return () => { cancelAnimationFrame(r); clearTimeout(t); };
  }, [transcript, draft]);

  const send = async () => {
    const text = input.trim();
    if (!text) return;
    setInput("");
    try {
      await api("/text", { method: "POST", body: JSON.stringify({ text }) });
    } catch {}
  };

  return (
    <div className="convo">
      <div className="convo__scroll" ref={scrollRef}>
        {transcript.length === 0 && !draft && (
          <div className="convo__empty">
            Press <kbd>Ctrl+Shift+J</kbd> and speak — or type below.
          </div>
        )}
        {transcript.some((m) => m.id.startsWith("hist-")) && (
          <div className="convo__divider">earlier conversation</div>
        )}
        {transcript.map((t) => (
          <div key={t.id + t.ts} className={`convo__msg convo__msg--${t.role}`}>
            <span className="convo__who">{t.role === "user" ? "YOU" : "JARVIS"}</span>
            <p>{t.text}</p>
          </div>
        ))}
        {draft && (
          <div className="convo__msg convo__msg--assistant convo__msg--live">
            <span className="convo__who">JARVIS</span>
            <p>{draft}<span className="convo__caret" /></p>
          </div>
        )}
      </div>
      <div className="convo__inputrow">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send()}
          placeholder="Type to JARVIS…"
          spellCheck={false}
        />
        <button onClick={send}>SEND</button>
      </div>
    </div>
  );
}
