"""HTTP API and static files. GET only, 127.0.0.1 only, read-only.

This module never calls a model. Summaries are read from debugger.db, which
only the watcher writes.
"""
from __future__ import annotations

import json
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from . import scaffold
from .hermes_reader import JOB_ID_RE, OUTPUT_NAME_RE, hermes_font
from .state import step_traces

HOST = "127.0.0.1"
WEB = Path(__file__).resolve().parent.parent / "web"
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
}
FONT_RE = re.compile(r"^/fonts/([A-Za-z0-9_-]+\.(woff2|woff))$")
ALLOWED_HOSTS = ("127.0.0.1", "localhost")


class NotFound(Exception):
    pass


def scaffolded_jobs(context) -> dict[str, dict]:
    """job_id -> job record, for jobs named by a valid workflow file. Nothing
    else can be read through the API."""
    jobs = context.reader.jobs()
    allowed = {}
    for workflow in scaffold.load_dir(context.scaffold_dir, jobs):
        if not workflow["invalid"]:
            for step in workflow["steps"]:
                allowed[step["job_id"]] = jobs[step["job_id"]]
    return allowed


def _job(context, job_id: str) -> dict:
    if not JOB_ID_RE.match(job_id):
        raise NotFound
    job = scaffolded_jobs(context).get(job_id)
    if job is None:
        raise NotFound
    return job


def route(context, path: str):
    """Return (content type, body bytes). Raises NotFound."""
    if path == "/api/state":
        return "application/json", json.dumps(context.state()).encode()
    parts = path.split("/")
    if len(parts) == 5 and parts[:3] == ["", "api", "steps"] and parts[4] == "traces":
        job = _job(context, parts[3])
        traces = step_traces(context.reader, context.traces, job, parts[3], context.now())
        return "application/json", json.dumps({"job_id": parts[3], "traces": traces}).encode()
    if len(parts) == 5 and parts[:3] == ["", "api", "traces"]:
        _job(context, parts[3])
        name = parts[4]
        if not OUTPUT_NAME_RE.match(name) or name not in {n for n, _m, _s in context.reader.list_outputs(parts[3])}:
            raise NotFound
        return "text/plain; charset=utf-8", context.reader.read_output(parts[3], name).encode()
    if len(parts) == 4 and parts[:3] == ["", "api", "source"]:
        text = context.reader.read_script(_job(context, parts[3]))
        if text is None:
            raise NotFound
        return "text/plain; charset=utf-8", text.encode()
    if path in STATIC:
        name, kind = STATIC[path]
        return kind, (WEB / name).read_bytes()
    font = FONT_RE.match(path)
    if font:
        path = WEB / "fonts" / font.group(1)
        if not path.is_file():
            path = hermes_font(font.group(1))
        if path is not None:
            return "font/" + font.group(2), path.read_bytes()
    raise NotFound


def make_handler(context):
    class Handler(BaseHTTPRequestHandler):
        server_version = "wfd"
        protocol_version = "HTTP/1.1"

        def _send(self, status: int, kind: str, body: bytes, extra: dict | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy",
                             "default-src 'none'; script-src 'self'; style-src 'self'; font-src 'self'; "
                             "connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def _error(self, status: int, message: str, extra: dict | None = None) -> None:
            self._send(status, "application/json", json.dumps({"error": message}).encode(), extra)

        def do_GET(self):  # noqa: N802
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
            if host not in ALLOWED_HOSTS:
                return self._error(403, "this server only answers on 127.0.0.1")
            raw = urlsplit(self.path).path
            path = unquote(raw)
            if ".." in path or "\\" in path or "\x00" in path or "//" in path:
                return self._error(404, "not found")
            try:
                kind, body = route(context, path)
            except NotFound:
                return self._error(404, "not found")
            except Exception as exc:  # a bad read must not stop the server
                print(f"wfd: read failed for {path}: {type(exc).__name__}: {exc}", file=sys.stderr)
                return self._error(503, f"could not read Hermes data ({type(exc).__name__})")
            self._send(200, kind, body)

        def _refuse(self):
            length = int(self.headers.get("Content-Length") or 0)
            if 0 < length <= 1_000_000:
                self.rfile.read(length)
            elif length:
                self.close_connection = True
            self._error(405, "read-only: GET is the only method", {"Allow": "GET"})

        do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_HEAD = _refuse  # noqa: N815

        def log_message(self, format, *args):  # quiet: the dashboard polls
            pass

    return Handler


def make_server(context, port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((HOST, port), make_handler(context))
    server.daemon_threads = True
    return server


def serve(context, port: int) -> int:
    try:
        server = make_server(context, port)
    except OSError as exc:
        print(f"wfd: cannot listen on {HOST}:{port} ({exc.strerror or exc})", file=sys.stderr)
        return 2
    print(f"wfd: {context.mode} · http://{HOST}:{server.server_address[1]} · scaffolds: {context.scaffold_dir}")
    print("wfd: read-only. Ctrl-C to stop.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
