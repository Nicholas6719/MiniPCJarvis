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
  const res = await fetch(`http://127.0.0.1:${port}${path}`, {
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
