// How a voice id reads on screen. A Kokoro id is "bm_george"; a BLEND is
// "bm_george:0.6+bm_lewis:0.4" — a weighted sum of two pack voices, which is
// how a JARVIS-like voice that exists in no single pack voice gets made
// (sidecar: audio.tts.parse_voice). Shown as "George + Lewis", never raw.

export function voiceParts(v: string): { name: string; weight: number }[] {
  return v
    .replace(/\s+/g, "")
    .split("+")
    .filter(Boolean)
    .map((p) => {
      const [name, w] = p.split(":");
      const weight = w ? Number(w) : 1;
      return { name, weight: Number.isFinite(weight) ? weight : 1 };
    });
}

function oneName(v: string) {
  const name = v.replace(/^en_[A-Z]{2}-/, "").replace(/-\w+$/, "").replace(/^[abm][fm]_/, "");
  return name.charAt(0).toUpperCase() + name.slice(1);
}

export function prettyVoice(v: string) {
  const parts = voiceParts(v);
  if (parts.length <= 1) return oneName(v);
  return parts.map((p) => oneName(p.name)).join(" + ");
}

// A bare name ("george") is a Pocket TTS voice: the streaming engine that
// starts speaking ~100 ms after the request, and his pick.
export function isPocketVoice(v: string) {
  return !/^(en_|[abm][fm]_)/.test(v) && !v.includes("+");
}

export function voiceMeta(v: string) {
  if (isPocketVoice(v)) return "POCKET · STREAMING";
  const parts = voiceParts(v);
  const gb = parts.some((p) => /en_GB|bf_|bm_/.test(p.name));
  const warm = parts.some((p) => /f_|female/i.test(p.name));
  const kind = parts.length > 1 ? "BLEND" : warm ? "WARM" : "DRY, CLIPPED";
  return `${gb ? "EN-GB" : "EN-US"} · ${kind}`;
}
