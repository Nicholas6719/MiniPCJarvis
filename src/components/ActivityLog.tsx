import { useEffect, useRef } from "react";
import { useStore } from "../state/store";

function fmtTime(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString([], {
    hour: "2-digit", minute: "2-digit", second: "2-digit",
  });
}

export function ActivityLog() {
  const activity = useStore((s) => s.activity);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    ref.current?.scrollTo({ top: ref.current.scrollHeight });
  }, [activity]);

  return (
    <div className="activity" ref={ref}>
      <div className="activity__title">ACTIVITY</div>
      {activity.map((a) => (
        <div key={a.id + a.ts} className={`activity__row activity__row--${a.kind} ${a.status ? `is-${a.status}` : ""}`}>
          <span className="activity__ts">{fmtTime(a.ts)}</span>
          <span className="activity__summary">{a.summary}</span>
          {a.detail != null && (
            <span className="activity__detail">
              {typeof a.detail === "string" ? a.detail : JSON.stringify(a.detail)}
            </span>
          )}
        </div>
      ))}
    </div>
  );
}
