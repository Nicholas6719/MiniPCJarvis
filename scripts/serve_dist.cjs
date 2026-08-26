// Dead-simple static server for dist/ — vite's dev server silently wedges on this
// machine (accepts connections, never responds, even freshly restarted), so UI
// iteration is `vite build` (~0.6 s) + this. No magic, nothing to wedge.
const http = require("http");
const fs = require("fs");
const path = require("path");

const ROOT = path.join(__dirname, "..", "dist");
const PORT = 5173;
const MIME = {
  ".html": "text/html", ".js": "text/javascript", ".css": "text/css",
  ".ttf": "font/ttf", ".png": "image/png", ".svg": "image/svg+xml",
  ".json": "application/json", ".ico": "image/x-icon",
};

http.createServer((req, res) => {
  const url = new URL(req.url, "http://x");
  let p = path.join(ROOT, decodeURIComponent(url.pathname));
  if (!p.startsWith(ROOT)) { res.writeHead(403); return res.end(); }
  if (url.pathname === "/" || !fs.existsSync(p) || fs.statSync(p).isDirectory()) {
    p = path.join(ROOT, "index.html");
  }
  fs.readFile(p, (err, data) => {
    if (err) { res.writeHead(404); return res.end(); }
    res.writeHead(200, { "Content-Type": MIME[path.extname(p)] ?? "application/octet-stream" });
    res.end(data);
  });
}).listen(PORT, "127.0.0.1", () => console.log(`dist server on http://127.0.0.1:${PORT}`));
