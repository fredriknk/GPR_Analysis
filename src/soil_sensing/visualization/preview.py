"""Read-only loopback preview of a verified synthetic explorer, never field data."""

import json
import mimetypes
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from soil_sensing.export.report import verify_run

ALLOWED = (
    "index.html",
    "assets/plotly.min.js",
    "assets/viewer.js",
    "assets/plotly.LICENSE.txt",
    "scene.json",
    "evaluation.json",
    "run.json",
)


def preview_files(root):
    root = Path(root).resolve()
    manifest = verify_run(root)
    if any(name not in manifest.get("artifacts", {}) for name in ALLOWED if name != "run.json"):
        raise ValueError("Preview assets must all be declared in the verified run")
    scene = json.loads((root / "scene.json").read_text(encoding="utf-8"))
    if scene.get("synthetic") is not True:
        raise ValueError("Loopback preview is restricted to explicitly synthetic scenes")
    result = {}
    for name in ALLOWED:
        path = root / name
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError("Preview assets must remain inside their verified synthetic run")
        result["/" + name] = (
            path.read_bytes(),
            mimetypes.guess_type(name)[0] or "application/octet-stream",
        )
    result["/"] = result["/index.html"]
    return result


def preview_run(root, *, port=0, duration_seconds=600):
    if (
        type(port) is not int
        or not 0 <= port <= 65535
        or type(duration_seconds) is not int
        or not 1 <= duration_seconds <= 3600
    ):
        raise ValueError("Invalid preview port/duration")
    files = preview_files(root)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            resource = files.get(urlsplit(self.path).path)
            if resource is None:
                self.send_error(404)
                return
            body, mime = resource
            self.send_response(200)
            self.send_header("Content-Type", mime + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy", "connect-src 'none'; frame-ancestors 'none'"
            )
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", port), Handler) as server:
        timer = threading.Timer(duration_seconds, server.shutdown)
        timer.daemon = True
        timer.start()
        print(
            f"Synthetic-only preview http://127.0.0.1:{server.server_port}/ (PID {os.getpid()})",
            flush=True,
        )
        try:
            server.serve_forever()
        finally:
            timer.cancel()
