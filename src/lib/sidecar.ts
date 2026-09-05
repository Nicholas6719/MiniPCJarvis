// Client for the Python sidecar (loopback WS + REST), connection details injected
// by the Rust core via Tauri.
import { invoke } from "@tauri-apps/api/core";

export interface SidecarInfo {
  port: number;
  token: string;
}

let info: SidecarInfo | null = null;

export async function sidecarInfo(): Promise<SidecarInfo> {
  if (!info) {
    try {
      info = await invoke<SidecarInfo>("sidecar_info");
    } catch {
      // Browser-based dev (no Tauri): take port/token from the URL.
      // NOT cached. One transient invoke failure inside the real app used to
      // pin {port: 8790, token: ""} for the life of the process - the HUD then
      // talked to the wrong port with no token and never recovered short of a
      // restart. A dev fallback should not be able to become permanent.
      const params = new URLSearchParams(window.location.search);
      const fallback = {
        port: Number(params.get("port") ?? 8790),
        token: params.get("token") ?? "",
      };
      if (!("__TAURI_INTERNALS__" in window)) info = fallback;
      return fallback;
    }
  }
  return info;
}

export async function api(path: string, opts: RequestInit = {}): Promise<any> {
  const { port, token } = await sidecarInfo();
  // A DEADLINE, ALWAYS. On a wedged event loop (the forty-minute case) a fetch
  // with no timeout never rejects, so the machine panel's error path never ran
  // and the last healthy numbers sat there looking healthy while the 5-second
  // poll stacked hung requests behind them until the supervisor killed the
  // process. Eight seconds is longer than anything the HUD asks for takes;
  // a caller that needs more passes its own signal.
  const res = await fetch(`http://127.0.0.1:${port}${path}`, {
    signal: AbortSignal.timeout(8000),
    ...opts,
    headers: {
      "Content-Type": "application/json",
      "X-Jarvis-Token": token,
      ...(opts.headers ?? {}),
    },
  });
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return res.json();
}

export function connectEvents(onEvent: (evt: any) => void): () => void {
  let ws: WebSocket | null = null;
  let closed = false;

  const open = async () => {
    const { port, token } = await sidecarInfo();
    if (closed) return; // effect was cleaned up while we awaited
    ws = new WebSocket(`ws://127.0.0.1:${port}/ws?token=${token}`);
    // HYDRATE ON CONNECT. The socket only carries CHANGES of state, so a HUD
    // that connects to a sidecar already running - a reconnect after a
    // supervisor restart, a reload, the dev page - sat on the boot checklist
    // until the next turn happened to move the state. Ask once, on open.
    ws.onopen = async () => {
      try {
        const h = await api("/health");
        if (h && typeof h.state === "string") onEvent({ kind: "state", state: h.state, hydrated: true });
      } catch {}
    };
    ws.onmessage = (m) => {
      try {
        onEvent(JSON.parse(m.data));
      } catch {}
    };
    ws.onclose = () => {
      // Tell the UI the truth. "offline" was only ever the store's initial
      // value - nothing ever set it - so when the sidecar died or wedged the orb
      // kept showing whatever it last displayed, indefinitely if the supervisor
      // gave up. A HUD that looks fine while the backend is gone is worse than
      // one that looks broken.
      try { onEvent({ kind: "state", state: "offline" }); } catch {}
      // Ask the core again before reconnecting. A supervisor restart keeps
      // the port unless something else took it in the meantime, in which
      // case the sidecar comes back on a NEW one — and a cached port would
      // have the HUD reconnecting to a dead address forever.
      if ("__TAURI_INTERNALS__" in window) info = null;
      if (!closed) setTimeout(open, 1500);
    };
    ws.onerror = () => ws?.close();
  };
  open();
  return () => {
    closed = true;
    ws?.close();
  };
}
