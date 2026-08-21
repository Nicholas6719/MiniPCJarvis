import { useStore } from "../state/store";

export function ResearchView() {
  const runs = useStore((s) => s.researchRuns);
  const latestFirst = [...runs].reverse();

  return (
    <div className="research">
      <div className="memory__head">
        <span className="panel-title">WEB RESEARCH</span>
      </div>
      <div className="memory__list">
        {latestFirst.length === 0 && (
          <div className="memory__empty">
            No research yet. Ask: "Research the best mini PC under five hundred dollars."
          </div>
        )}
        {latestFirst.map((r) => (
          <div key={r.id} className="research__run">
            <div className="research__query">"{r.query}"</div>
            <div className={`research__stage research__stage--${r.stage}`}>
              {r.stage === "searching" && "SEARCHING…"}
              {r.stage === "reading" && `READING ${r.sources.length} SOURCES…`}
              {r.stage === "done" && `COMPLETE — ${r.fetched ?? r.sources.length}/${r.sources.length} sources read`}
            </div>
            {r.sources.length > 0 && (
              <div className="research__sources">
                {r.sources.map((s, i) => (
                  <a key={i} className="research__source" href={s.url} target="_blank" rel="noreferrer">
                    <span className="research__source-n">{i + 1}</span>
                    <span className="research__source-title">{s.title || s.url}</span>
                    <span className="research__source-host">{hostOf(s.url)}</span>
                  </a>
                ))}
              </div>
            )}
            {r.answer && (
              <div className="research__answer">
                <span className="research__answer-label">CONCLUSION</span>
                <p>{r.answer}</p>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return "";
  }
}
