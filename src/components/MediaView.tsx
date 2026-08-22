// Pictures JARVIS was asked to show, rendered right in the interface.
import { useState } from "react";
import { useStore } from "../state/store";

export function MediaView() {
  const media = useStore((s) => s.media);
  const [big, setBig] = useState<number | null>(null);

  if (!media) {
    return (
      <div className="media">
        <div className="memory__head"><span className="panel-title">MEDIA</span></div>
        <div className="memory__empty">Ask: "Jarvis, show me a picture of a nebula."</div>
      </div>
    );
  }
  return (
    <div className="media">
      <div className="memory__head">
        <span className="panel-title">MEDIA</span>
        <span className="media__query">"{media.query}"</span>
      </div>
      {big !== null && media.images[big] && (
        <div className="media__lightbox" onClick={() => setBig(null)}>
          <img src={media.images[big].src} alt={media.images[big].alt} />
        </div>
      )}
      <div className="media__grid">
        {media.images.map((im, i) => (
          <button key={i} className="media__cell" onClick={() => setBig(i)} title={im.alt}>
            <img src={im.src} alt={im.alt} loading="lazy" />
          </button>
        ))}
      </div>
    </div>
  );
}
