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
      const params = new URLSearchParams(window.location.search);
      info = {
        port: Number(params.get("port") ?? 8790),
        token: params.get("token") ?? "",
      };
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
