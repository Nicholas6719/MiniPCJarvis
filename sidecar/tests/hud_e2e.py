"""HUD regression: every promise the stage UI prints must actually work.

Serves ../dist itself (vite dev wedges on this machine; `npm run build` first),
drives it headless via Playwright+Brave against a RUNNING sidecar, and checks the
store-level contract (window.__jarvis) that the voice hooks ride on:
prose-on-transcript, stage swaps, "keep it" (+ timed), "bring that back",
image focus, hide, per-kind holds.

Usage:  python tests/hud_e2e.py PORT TOKEN
        (PORT/TOKEN of any running sidecar - install or dev; read-only against it)
"""
import http.server
import json
import os
import socketserver
import sys
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DIST = REPO / "dist"
BRAVE = r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"

# The whole frontend contract in one page-side script. Returns {name: bool}.
CHECKS_JS = """
(() => {
  const S = window.__jarvis; if (!S) return {noHandle: true};
  const ev = (e) => S.getState().onEvent(e);
  const out = {};
  ev({kind:"transcript", id:"t1", text:"how fast is this thing", ts: 1});
  out.prose_on_transcript = S.getState().stage?.kind === "prose";
  out.turn_started = S.getState().turn?.userText === "how fast is this thing";
  ev({kind:"web", id:"w1", stage:"results", query:"q", ts:2,
      results:[{title:"A",url:"https://a.com/x"},{title:"B",url:"https://b.com/y"}]});
  out.browser_stage = S.getState().stage?.kind === "browser";
  ev({kind:"web", id:"w2", stage:"opening", query:"q", url:"https://a.com/x", ts:3});
  out.opening_marker = S.getState().web?.opening === "https://a.com/x";
  ev({kind:"web", id:"w3", stage:"read", query:"q", url:"https://a.com/x", ok:true, ts:4});
  out.read_progress = !!S.getState().web?.read["https://a.com/x"];
  ev({kind:"images", id:"i1", query:"arc reactors", ts:5,
      images:[{src:"a.png",alt:"a",w:1,h:1},{src:"b.png",alt:"b",w:1,h:1}]});
  out.images_stage = S.getState().stage?.kind === "images";
  ev({kind:"transcript", id:"t2", text:"make it bigger", ts:6});
  out.new_turn_reclaims = S.getState().stage?.kind === "prose";
  ev({kind:"ui", action:"focus", index:1});
  out.focus_restores_snapshot = S.getState().stage?.kind === "images"
    && S.getState().images?.focus === 1;
  ev({kind:"ui", action:"focus", index:null});
  out.back_to_grid = S.getState().images?.focus === null;
  ev({kind:"transcript", id:"t3", text:"hide everything", ts:7});
  ev({kind:"ui", action:"hide"});
  out.hide = S.getState().stage === null;
  ev({kind:"transcript", id:"t4", text:"bring that back", ts:8});
  ev({kind:"ui", action:"restore"});
  out.restore = S.getState().stage?.kind === "images"
    && (S.getState().images?.images?.length ?? 0) === 2;
  ev({kind:"ui", action:"pin", minutes:10});
  const p = S.getState().stage;
  out.timed_pin = !!p?.pinned && !!p?.pinUntil && p.pinUntil > Date.now() + 9*60000;
  ev({kind:"ui", action:"unpin"});
  out.unpin = S.getState().stage?.pinned === false;
  ev({kind:"files", id:"f1", path:"C:/x", label:"documents", parent:null, count:1, ts:9,
      entries:[{name:"a.txt", path:"C:/x/a.txt", kind:"file", size:10}], roots:{}});
  out.folder_stage = S.getState().stage?.kind === "folder";
  ev({kind:"state", state:"idle"});
  const hold = S.getState().stage?.holdUntil ?? 0;
  out.folder_holds_longer = hold > Date.now() + 10000;   // 30 s, not 5 s
  ev({kind:"file_preview", id:"p1", path:"C:/x/a.txt", name:"a.txt", type:"text",
      text:"hello", size:10, ts:10});
  out.file_stage = S.getState().stage?.kind === "file";
  ev({kind:"set_view", view:"history"});
  out.settings_history = S.getState().stage?.kind === "settings"
    && S.getState().stage?.settingsSection === "history";
  ev({kind:"confirmation_required", id:"c1", confirm_id:"x", tool:"t", args:{}, risk:"medium", ts:11});
  out.gate = S.getState().confirmation?.confirmId === "x";
  S.getState().clearConfirmation();
  S.getState().pinStage(false); S.getState().dismissStage();
  // stale-data guard: a fresh turn clears the last turn's sources
  ev({kind:"transcript", id:"t9", text:"how tall is everest", ts:12});
  out.no_stale_chips = S.getState().web === null && S.getState().images === null;
  S.getState().dismissStage();
  return out;
})()
"""


def serve_dist() -> tuple[socketserver.TCPServer, int]:
    handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(
        *a, directory=str(DIST), **k)
    srv = socketserver.TCPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def main() -> int:
    port, token = sys.argv[1], sys.argv[2]
    if not (DIST / "index.html").exists():
        print("FAIL dist/ missing - run npm run build first")
        return 1
    srv, http_port = serve_dist()
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=BRAVE, headless=True)
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        page.goto(f"http://127.0.0.1:{http_port}/?port={port}&token={token}")
        page.wait_for_function("() => !!window.__jarvis", timeout=10000)
        results: dict = page.evaluate(CHECKS_JS)
        browser.close()
    srv.shutdown()
    bad = [k for k, v in results.items() if not v]
    for k, v in sorted(results.items()):
        print(f"  {'PASS' if v else 'FAIL'} {k}")
    print(f"\nHUD CONTRACT {len(results) - len(bad)}/{len(results)}")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
