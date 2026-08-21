// Persistent HUD status strip: live model/CPU/RAM/mic-mode from the sidecar.
import { useEffect, useState } from "react";
import { api } from "../lib/sidecar";
import { useStore } from "../state/store";

interface Stats {
  cpu: number;
  ram_percent: number;
  ram_used_gb: number;
  model: string | null;
  model_external: boolean;
  wake_mode: string;
}

const WAKE_LABEL: Record<string, string> = {
  push_to_talk: "PTT",
  wake_word: "WAKE",
  both: "WAKE+PTT",
};

export function StatusBar() {
  const [stats, setStats] = useState<Stats | null>(null);
  const state = useStore((s) => s.state);
  const [clock, setClock] = useState(new Date());

  useEffect(() => {
    const load = async () => {
      try {
        setStats(await api("/stats"));
      } catch {
        setStats(null);
      }
    };
    load();
    const t = setInterval(load, 5000);
    const c = setInterval(() => setClock(new Date()), 1000);
    return () => {
      clearInterval(t);
      clearInterval(c);
    };
  }, []);

  return (
    <footer className="statusbar">
      <span className={`statusbar__item statusbar__state statusbar__state--${state}`}>
        ● {state.toUpperCase()}
      </span>
      {stats && (
        <>
          <span className="statusbar__item">
            MODEL {stats.model ?? "—"}{stats.model_external ? " (shared)" : ""}
          </span>
          <span className="statusbar__item">MIC {WAKE_LABEL[stats.wake_mode] ?? stats.wake_mode}</span>
          <span className="statusbar__item">CPU {Math.round(stats.cpu)}%</span>
          <span className="statusbar__item">RAM {Math.round(stats.ram_percent)}% ({stats.ram_used_gb} GB)</span>
        </>
      )}
      <span className="statusbar__item statusbar__clock">
        {clock.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
      </span>
    </footer>
  );
}
