import http.client
import io
import json
import os
import shutil
import threading
from contextlib import redirect_stdout

import build_fixtures
import pytest

from wfd import cli
from wfd.context import Context, build_context
from wfd.server import HOST, make_server

SCAFFOLDED = "a1f0c0de0002"
UNSCAFFOLDED = "f6f0c0de0001"   # in jobs.json, in no workflow file
TRACE = "2026-11-11_08-01-09.md"


class Client:
    def __init__(self, port):
        self.port = port

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection(HOST, self.port, timeout=10)
        try:
            conn.putrequest(method, path, skip_host=True)
            sent = {"Host": f"{HOST}:{self.port}", **(headers or {})}
            if body is not None:
                sent["Content-Length"] = str(len(body))
            for key, value in sent.items():
                conn.putheader(key, value)
            conn.endheaders(body)
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            conn.close()

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def json(self, path):
        status, _headers, body = self.get(path)
        return status, json.loads(body)


def start(context):
    server = make_server(context, 0)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.02), daemon=True)
    thread.start()
    return server, Client(server.server_address[1])


def fixture_context(root, data, scaffolds="scaffolds"):
    return Context(mode="fixtures", profile_root=root / "profile", scaffold_dir=root / scaffolds,
                   data_dir=data, summary_db=root / "debugger.db", fixed_now=build_fixtures.NOW)


@pytest.fixture()
def served(fx, tmp_path):
    server, client = start(fixture_context(fx, tmp_path / "data"))
    yield client
    server.shutdown()
    server.server_close()


def snapshot(root):
    return sorted((str(p.relative_to(root)), p.stat().st_mtime_ns, p.stat().st_size)
                  for p in root.rglob("*"))


# ---- 3.1 ------------------------------------------------------------------
def test_state_matches_wfd_status_json(tmp_path):
    out = io.StringIO()
    with redirect_stdout(out):
        code = cli.main(["status", "--json", "--fixtures", "--data-dir", str(tmp_path)])
    assert code == 1  # the fixtures include one invalid file on purpose
    server, client = start(build_context(fixtures=True, data_dir=tmp_path))
    try:
        status, served_state = client.json("/api/state")
    finally:
        server.shutdown()
        server.server_close()
    assert status == 200
    assert served_state == json.loads(out.getvalue())
    assert served_state["mode"] == "fixtures" and len(served_state["workflows"]) == 9


def test_state_headers(served):
    status, headers, _body = served.get("/api/state")
    assert status == 200
    assert headers["Content-Type"] == "application/json"
    assert headers["Cache-Control"] == "no-store"
    assert "default-src 'none'" in headers["Content-Security-Policy"]


def test_static_files(served):
    assert served.get("/")[0] == 200
    assert served.get("/app.js")[1]["Content-Type"].startswith("text/javascript")
    assert served.get("/app.css")[0] == 200
    assert served.get("/pyproject.toml")[0] == 404
    assert served.get("/fonts/../app.js")[0] == 404


# ---- 3.2 / AC-21 -----------------------------------------------------------
def test_binds_to_loopback_only(served):
    assert HOST == "127.0.0.1"
    server, _client = start(fixture_context(build_fixtures.REPO / "var" / "nonexistent", build_fixtures.REPO / "var"))
    try:
        assert server.server_address[0] == "127.0.0.1"
    finally:
        server.shutdown()
        server.server_close()


def test_valid_trace_list_file_and_source(served):
    status, listing = served.json(f"/api/steps/{SCAFFOLDED}/traces")
    assert status == 200 and listing["traces"][0]["file"] == TRACE
    assert [t["state"] for t in listing["traces"]] == ["failed", "ok", "ok", "failed", "ok"]
    status, headers, body = served.get(f"/api/traces/{SCAFFOLDED}/{TRACE}")
    assert status == 200 and headers["Content-Type"].startswith("text/plain")
    assert b"403 Forbidden" in body
    status, _h, body = served.get(f"/api/source/{SCAFFOLDED}")
    assert status == 200 and b"Placeholder script" in body


def test_missed_traces_are_listed(served):
    _status, listing = served.json("/api/steps/c3f0c0de0001/traces")
    assert (listing["traces"][0]["state"], listing["traces"][0]["file"]) == ("missed", None)


@pytest.mark.parametrize("path", [
    f"/api/traces/{SCAFFOLDED}/../../jobs.json",
    f"/api/traces/{SCAFFOLDED}/..%2f..%2fjobs.json",
    f"/api/traces/{SCAFFOLDED}/%2e%2e/%2e%2e/jobs.json",
    f"/api/traces/{SCAFFOLDED}/....//jobs.json",
    f"/api/traces/{SCAFFOLDED}/jobs.json",
    f"/api/traces/{SCAFFOLDED}/2026-11-11_08-01-09.md%00.txt",
    f"/api/traces/{SCAFFOLDED}/1999-01-01_00-00-00.md",
    "/api/traces/..%2f..%2fscripts/industry-analysis-runner.py",
    "/api/source/..%2f..%2fconfig.yaml",
    "/api/source/../../config.yaml",
    "/../profile/config.yaml",
    "/api/steps/../../traces",
    "//etc/hosts",
])
def test_paths_outside_scaffolded_files_are_refused(served, path):
    status, _headers, body = served.get(path)
    assert status == 404, path
    assert json.loads(body) == {"error": "not found"}


@pytest.mark.parametrize("path", [
    f"/api/steps/{UNSCAFFOLDED}/traces",
    f"/api/traces/{UNSCAFFOLDED}/2026-11-12_04-00-09.md",
    f"/api/source/{UNSCAFFOLDED}",
    "/api/steps/nosuchjob/traces",
])
def test_job_not_in_any_scaffold_is_refused(served, path, fx):
    assert (fx / "profile" / "cron" / "output" / UNSCAFFOLDED / "2026-11-12_04-00-09.md").is_file()
    assert served.get(path)[0] == 404


def test_unscaffolded_job_is_not_in_state(served):
    _status, body = served.json("/api/state")
    assert UNSCAFFOLDED not in json.dumps(body)


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
@pytest.mark.parametrize("path", ["/api/state", f"/api/traces/{SCAFFOLDED}/{TRACE}", "/", "/api/anything"])
def test_every_method_but_get_is_rejected(served, method, path):
    body = b'{"x": 1}' if method in ("POST", "PUT", "PATCH") else None
    status, headers, _body = served.request(method, path, body=body)
    assert status == 405 and headers["Allow"] == "GET"


def test_other_host_header_is_refused(served):
    assert served.get("/api/state", headers={"Host": "evil.example.com"})[0] == 403
    assert served.get("/api/state", headers={"Host": f"localhost:{served.port}"})[0] == 200


def test_serving_writes_nothing_into_the_profile(fx, tmp_path):
    before = snapshot(fx)
    server, client = start(fixture_context(fx, tmp_path / "data"))
    try:
        for path in ("/api/state", f"/api/steps/{SCAFFOLDED}/traces", f"/api/traces/{SCAFFOLDED}/{TRACE}",
                     f"/api/source/{SCAFFOLDED}", "/api/steps/d4f0c0de0001/traces"):
            assert client.get(path)[0] == 200
        client.request("POST", "/api/state", body=b"{}")
    finally:
        server.shutdown()
        server.server_close()
    assert snapshot(fx) == before


def test_empty_scaffold_folder(fx, tmp_path):
    server, client = start(fixture_context(fx, tmp_path / "data", "scaffolds-empty"))
    try:
        status, state = client.json("/api/state")
        assert status == 200 and state["workflows"] == []
        assert client.get(f"/api/steps/{SCAFFOLDED}/traces")[0] == 404
    finally:
        server.shutdown()
        server.server_close()


# ---- 3.3 ------------------------------------------------------------------
def test_bad_read_returns_an_error_and_the_server_keeps_running(tmp_path):
    root = build_fixtures.build(tmp_path / "fixtures")
    server, client = start(fixture_context(root, tmp_path / "data"))
    jobs = root / "profile" / "cron" / "jobs.json"
    try:
        assert client.get("/api/state")[0] == 200
        shutil.move(jobs, tmp_path / "jobs.json.away")
        status, body = client.json("/api/state")
        assert status == 503 and body == {"error": "could not read Hermes data (FileNotFoundError)"}
        assert client.get(f"/api/steps/{SCAFFOLDED}/traces")[0] == 503
        assert client.get("/")[0] == 200, "static files still served"
        jobs.write_text("{ not json")
        assert client.json("/api/state")[0] == 503
        os.remove(jobs)
        shutil.move(tmp_path / "jobs.json.away", jobs)
        assert client.get("/api/state")[0] == 200
    finally:
        server.shutdown()
        server.server_close()


def test_port_in_use_is_reported(tmp_path, capsys):
    from wfd.server import serve

    root = build_fixtures.build(tmp_path / "fixtures")
    server, client = start(fixture_context(root, tmp_path / "data"))
    try:
        assert serve(fixture_context(root, tmp_path / "data2"), client.port) == 2
        assert "cannot listen on 127.0.0.1" in capsys.readouterr().err
    finally:
        server.shutdown()
        server.server_close()
