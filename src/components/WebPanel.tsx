// Live view of JARVIS working the web: query → results → pages read → done.
// Takes over the right column while web activity is happening.
import { useStore } from "../state/store";

export function WebPanel() {
  const web = useStore((s) => s.web);
  const setRightPanel = useStore((s) => s.setRightPanel);
  if (!web) return null;

  const stageLabel =
    web.stage === "searching" ? "SEARCHING" :
    web.stage === "images_searching" ? "FINDING IMAGES" :
    web.stage === "results" ? "RESULTS" :
    web.stage === "reading" ? "READING SOURCES" :
    web.stage === "done" ? "COMPLETE" :
    web.stage === "empty" ? "NO RESULTS" :
    web.stage === "error" ? "SEARCH FAILED" : web.stage.toUpperCase();
  const live = ["searching", "images_searching", "reading"].includes(web.stage);

  return (
    <div className="webpanel">
      <div className="webpanel__head">
        <span className="activity__title">WEB</span>
        <button className="ghost-btn webpanel__back" onClick={() => setRightPanel("activity")}>ACTIVITY</button>
      </div>
      <div className="webpanel__query">"{web.query}"</div>
      <div className={`webpanel__stage ${live ? "is-live" : ""} webpanel__stage--${web.stage}`}>{stageLabel}</div>
      {web.error && <div className="webpanel__error">{web.error}</div>}
      <div className="webpanel__list">
        {web.results.map((r, i) => {
          const read = web.read[r.url];
          return (
            <a key={i} className={`webpanel__result ${read ? (read.ok ? "is-read" : "is-failed") : ""}`}
               href={r.url} target="_blank" rel="noreferrer" title={r.url}>
              <span className="webpanel__n">{i + 1}</span>
              <span className="webpanel__body">
                <span className="webpanel__title">{r.title || r.url}</span>
                <span className="webpanel__host">{r.host || hostOf(r.url)}{read ? (read.ok ? " · read" : " · unreadable") : ""}</span>
                {r.snippet && <span className="webpanel__snippet">{r.snippet}</span>}
              </span>
            </a>
          );
        })}
      </div>
    </div>
  );
}

function hostOf(url: string): string {
  try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return ""; }
}
