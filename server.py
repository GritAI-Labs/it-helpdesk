"""Local server for the IT Helpdesk agent — stdlib only, no framework.

    python server.py          # http://127.0.0.1:8013

- Serves the static site (index.html) + the eval report.
- POST /ticket {"ticket": "..."}  -> runs the AUTOMATED graph, returns the full trace
  (category, the LIVE facts pulled from the free APIs, the draft, the critic verdict, and the
  resolved/escalated decision). No human gate — the independent critic decides.
- GET /eval  /eval-metrics        -> the evaluation report + numbers (how we proved the gate).
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from graph import app

PORT = int(os.environ.get("PORT", "8013"))
CTYPES = {".html": "text/html", ".js": "application/javascript", ".css": "text/css",
          ".json": "application/json", ".png": "image/png"}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _file(self, relpath, ctype):
        fp = os.path.join(HERE, relpath)
        if not os.path.isfile(fp):
            return self._send(404, "not found", "text/plain")
        with open(fp, "rb") as f:
            self._send(200, f.read(), ctype)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/eval":
            return self._file(os.path.join("eval", "report.html"), "text/html")
        if path == "/eval-metrics":
            fp = os.path.join(HERE, "eval", "metrics.json")
            if not os.path.isfile(fp):
                return self._send(200, "{}")
            return self._file(os.path.join("eval", "metrics.json"), "application/json")
        if path == "/":
            path = "/index.html"
        fp = os.path.normpath(os.path.join(HERE, path.lstrip("/")))
        if not fp.startswith(HERE) or not os.path.isfile(fp):
            return self._send(404, "not found", "text/plain")
        with open(fp, "rb") as f:
            self._send(200, f.read(), CTYPES.get(os.path.splitext(fp)[1], "application/octet-stream"))

    def do_POST(self):
        n = int(self.headers.get("Content-Length", "0"))
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            body = {}
        if self.path == "/ticket":
            ticket = (body.get("ticket") or "").strip()
            if not ticket:
                return self._send(400, '{"error":"empty ticket"}')
            print("\n" + "=" * 64 + f"\nTICKET: {ticket}\n" + "-" * 64)
            try:
                out = app.invoke({"ticket": ticket})
            except Exception as e:
                return self._send(200, json.dumps({"error": str(e)[:300]}))
            for k in ("category", "verdict", "status", "escalate_reason"):
                print(f"  {k}: {out.get(k)}")
            print("=" * 64, flush=True)
            return self._send(200, json.dumps({
                "category": out.get("category"), "facts": out.get("facts") or {},
                "runbook": out.get("runbook") or {}, "draft": out.get("draft"),
                "verdict": out.get("verdict"), "status": out.get("status"),
                "escalate_reason": out.get("escalate_reason")}, ensure_ascii=False))
        return self._send(404, '{"error":"not found"}')

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print(f"IT Helpdesk agent  ->  http://127.0.0.1:{PORT}")
    print("(paste a ticket; it classifies -> pulls LIVE EOL/CVE/status -> drafts -> critic gate -> resolve/escalate)\n")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
