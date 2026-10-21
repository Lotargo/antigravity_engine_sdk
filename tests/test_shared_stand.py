import http.client
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from python.testbench.go_backend import GoBackend
from python.testbench.server import Stand, handler


@pytest.fixture
def shared(tmp_path):
    calls = []

    class GoHandler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            self.do_POST()

        def do_POST(self):
            data = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            calls.append((self.command, self.path, dict(self.headers), data))
            value = {"sdk_language": "Go", "path": self.path, "body": data.hex()}
            code = 409 if self.path.endswith("/cancel") else 200
            raw = json.dumps(value).encode()
            self.send_response(code)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    go_server = ThreadingHTTPServer(("127.0.0.1", 0), GoHandler)
    go_thread = threading.Thread(target=go_server.serve_forever)
    go_thread.start()
    backend = GoBackend.__new__(GoBackend)
    backend.process = SimpleNamespace(poll=lambda: None)
    backend.port = go_server.server_port
    backend.token = "private-go-token"
    backend.config = {"version": "0.5.0"}
    app = Stand(tmp_path)
    app.go = backend
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(app))
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    yield app, server, calls
    app.go = None
    server.shutdown()
    server.server_close()
    app.close()
    go_server.shutdown()
    go_server.server_close()
    thread.join()
    go_thread.join()


def request(shared, method, path, body=None, **headers):
    app, server, _ = shared
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    connection.request(method, path, body, {"X-Testbench-Token": app.token, **headers})
    response = connection.getresponse()
    code, value = response.status, json.loads(response.read())
    connection.close()
    return code, value


def test_backend_listing_and_python_go_isolation(shared):
    code, value = request(shared, "GET", "/api/backends")
    assert code == 200 and value["default"] == "python"
    assert [(v["id"], v["available"]) for v in value["backends"]] == [
        ("python", True),
        ("go", True),
    ]
    assert request(shared, "GET", "/api/config")[1]["sdk_language"] == "Python"
    assert not shared[2]
    code, value = request(shared, "GET", "/api/config", **{"X-Testbench-SDK": "go"})
    assert code == 200 and value["sdk_language"] == "Go"
    assert shared[0].job is None and shared[0].editor is None
    assert "private-go-token" not in json.dumps(value)


@pytest.mark.parametrize(
    "path",
    ["/api/requests", "/api/editor/setup", "/api/editor/run", "/api/editor/cancel"],
)
def test_go_proxy_keeps_body_and_replaces_credentials(shared, path):
    raw = b'--boundary\r\nContent-Disposition: form-data; name="image"\r\n\r\n\x00\xff\x80\r\n'
    code, value = request(
        shared,
        "POST",
        path,
        raw,
        **{
            "X-Testbench-SDK": "go",
            "Content-Type": "multipart/form-data; boundary=boundary",
            "Cookie": "must-not-forward",
            "Authorization": "must-not-forward",
        },
    )
    assert (
        code == (409 if path.endswith("/cancel") else 200)
        and value["body"] == raw.hex()
    )
    method, actual, headers, received = shared[2][-1]
    assert method == "POST" and actual == path and received == raw
    assert headers["X-Testbench-Token"] == "private-go-token"
    assert headers["Content-Type"] == "multipart/form-data; boundary=boundary"
    assert "Cookie" not in headers and "Authorization" not in headers


def test_go_cancel_report_and_query_routes(shared):
    headers = {"X-Testbench-SDK": "go"}
    assert (
        request(shared, "POST", "/api/requests/abcdef/cancel", b"", **headers)[0] == 409
    )
    assert request(shared, "GET", "/api/requests/abcdef/report", **headers)[0] == 200
    assert request(shared, "GET", "/api/editor/example?kind=supercomplete", **headers)[
        1
    ]["path"].endswith("?kind=supercomplete")
    assert request(shared, "GET", "/api/editor/state", **headers)[0] == 200


def test_guards_run_before_proxy_and_unavailable_never_falls_back(shared):
    app, _, calls = shared
    for headers in (
        {"X-Testbench-Token": "wrong"},
        {"Origin": "http://foreign.invalid"},
        {"Host": "foreign.invalid"},
    ):
        assert (
            request(
                shared, "GET", "/api/config", **{"X-Testbench-SDK": "go", **headers}
            )[0]
            == 403
        )
    assert not calls
    assert (
        request(shared, "GET", "/api/config", **{"X-Testbench-SDK": "other"})[0] == 400
    )
    assert (
        request(shared, "GET", "/api/../private", **{"X-Testbench-SDK": "go"})[0] == 502
    )
    assert not calls
    app.go.process.poll = lambda: 1
    code, value = request(shared, "GET", "/api/config", **{"X-Testbench-SDK": "go"})
    assert code == 502 and value["error"]["code"] == "go_backend"
    assert (
        request(shared, "GET", "/api/backends")[1]["backends"][1]["available"] is False
    )
    assert not calls


def test_missing_go_executable_does_not_start_a_process(tmp_path):
    with pytest.raises(FileNotFoundError):
        GoBackend(tmp_path / "missing.exe", tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_owned_go_handshake_and_process_cleanup(tmp_path):
    root = Path(__file__).resolve().parents[1]
    binary = root / "go/testbench/bin/antigravity-testbench-shared.exe"
    if not binary.exists():
        pytest.skip(
            "Build the shared Go backend first; this check starts no model inference"
        )
    backend = GoBackend(binary, tmp_path)
    try:
        assert backend.available
        code, raw = backend.request("GET", "/api/config")
        assert code == 200 and json.loads(raw)["sdk_language"] == "Go"
        assert not list((tmp_path / "go").glob("*.json"))
    finally:
        backend.close()
    assert backend.process.poll() is not None
