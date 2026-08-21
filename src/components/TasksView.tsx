import { useEffect, useState } from "react";
import { api } from "../lib/sidecar";
import { useStore } from "../state/store";

interface Task {
  id: number;
  due: string;
  text: string;
  recurrence: string;
}

export function TasksView() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [error, setError] = useState("");
  const activity = useStore((s) => s.activity);

  const load = async () => {
    try {
      const r = await api("/tasks");
      setTasks(r.tasks);
      setError("");
    } catch {
      setError("tasks unavailable");
    }
  };

  useEffect(() => {
    load();
  }, [activity.length]); // refresh when new activity (e.g. reminder set) arrives

  const cancel = async (id: number) => {
    try {
      await api(`/tasks/${id}`, { method: "DELETE" });
      setTasks((t) => t.filter((x) => x.id !== id));
    } catch {}
  };

  return (
    <div className="tasks">
      <div className="memory__head">
        <span className="panel-title">TASKS &amp; ROUTINES</span>
        <button className="ghost-btn" onClick={load}>REFRESH</button>
      </div>
      <div className="memory__list">
        {error && <div className="memory__empty">{error}</div>}
        {!error && tasks.length === 0 && (
          <div className="memory__empty">
            No pending reminders. Try: "Remind me in twenty minutes to take a break."
          </div>
        )}
        {tasks.map((t) => (
          <div key={t.id} className="memory__card tasks__card">
            <div className="memory__meta">
              <span className="tasks__due">{t.due}</span>
              {t.recurrence !== "none" && (
                <span className="tasks__recur">{t.recurrence.toUpperCase()}</span>
              )}
            </div>
            <p className="memory__content">{t.text}</p>
            <div className="memory__actions">
              <button className="ghost-btn ghost-btn--danger" onClick={() => cancel(t.id)}>
                CANCEL
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
