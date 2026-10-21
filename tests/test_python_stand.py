from __future__ import annotations

import http.client
import json
import re
import threading
import time
from dataclasses import asdict
from http.server import ThreadingHTTPServer

import pytest

from antigravity_engine_sdk import EditorDocument, Installation, runtime
from python.testbench.server import Stand, handler


@pytest.fixture
def stand(boundary, monkeypatch, tmp_path):
    monkeypatch.setattr(
        runtime,
        "find_installation",
        lambda *_: Installation("language_server.exe", "2.24.0"),
    )
    app = Stand(
        tmp_path, client_options={"timeout": 5, "poll_interval": 0.02}, model="test-low"
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(app))
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    yield app, server
    server.shutdown()
    server.server_close()
    app.close()
    thread.join(timeout=2)


def call(stand, method, path, body=None, headers=None):
    app, server = stand
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
    fields = {"X-Testbench-Token": app.token, **(headers or {})}
    if isinstance(body, dict):
        body = json.dumps(body).encode()
        fields["Content-Type"] = "application/json"
    connection.request(method, path, body=body, headers=fields)
    response = connection.getresponse()
    result = response.status, dict(response.getheaders()), response.read()
    connection.close()
    return result


def settled(stand, path):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        code, _, raw = call(stand, "GET", path)
        assert code == 200
        value = json.loads(raw)
        if value and value["status"] not in {"running", "cancelling"}:
            return value
        time.sleep(0.01)
    pytest.fail("Python stand request did not finish")


def test_python_stand_security_templates_and_shared_assets(stand):
    code, _, raw = call(
        stand, "GET", "/api/config", headers={"X-Testbench-Token": "wrong"}
    )
    assert code == 403
    assert (
        call(stand, "GET", "/api/config", headers={"Origin": "http://evil.invalid"})[0]
        == 403
    )
    assert call(stand, "GET", "/api/config", headers={"Host": "evil.invalid"})[0] == 403
    code, _, raw = call(stand, "GET", "/api/config")
    assert json.loads(raw)["sdk_language"] == "Python"
    nonces = []
    for _ in range(2):
        code, headers, raw = call(stand, "GET", "/editor")
        assert code == 200 and b"Python SDK" in raw and b"{{." not in raw
        nonce = re.search(rb'name="editor-style-nonce" content="([a-f0-9]+)"', raw)[
            1
        ].decode()
        nonces.append(nonce)
        assert "'nonce-" + nonce + "'" in headers["Content-Security-Policy"]
        assert "unsafe-inline" not in headers["Content-Security-Policy"]
    assert nonces[0] != nonces[1]
    assert b"Python" in call(stand, "GET", "/")[2]
    code, headers, raw = call(stand, "GET", "/code-editor.js")
    assert (
        code == 200
        and headers["Content-Type"].startswith("text/javascript")
        and b"createCodeEditor" in raw
    )
    assert call(stand, "GET", "/../pyproject.toml")[0] == 404


def test_python_stand_editor_contract_and_cancellation(stand, boundary):
    boundary.set("editor_success")
    document = asdict(EditorDocument("custom.py", "python", "old", 1))
    assert call(stand, "POST", "/api/editor/setup", document)[0] == 200
    document["text"] = "new"
    assert (
        call(
            stand,
            "POST",
            "/api/editor/run",
            {"kind": "supercomplete", "document": document},
        )[0]
        == 202
    )
    value = settled(stand, "/api/editor/state")
    assert value["status"] == "completed" and value["preview"] == "new!"
    assert value["result"]["preparation"]["bootstrap_frames"] == []
    assert value["result"]["kind"] == "SUPERCOMPLETE"
    boundary.set("editor_running")
    assert (
        call(
            stand, "POST", "/api/editor/run", {"kind": "tab_jump", "document": document}
        )[0]
        == 202
    )
    assert call(stand, "POST", "/api/editor/setup", document)[0] == 409
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        current = json.loads(call(stand, "GET", "/api/editor/state")[2])
        if current["frames"]:
            break
        time.sleep(0.01)
    assert current["frames"] == 1
    assert call(stand, "POST", "/api/editor/cancel", {})[0] == 200
    value = settled(stand, "/api/editor/state")
    assert value["status"] == "cancelled" and value["result"]["status"] == "incomplete"
    boundary.set("editor_success")
    assert (
        call(
            stand, "POST", "/api/editor/run", {"kind": "tab_jump", "document": document}
        )[0]
        == 202
    )
    assert settled(stand, "/api/editor/state")["result"]["response"][
        "jumpPosition"
    ] == {"row": "0", "col": "0"}


def form_request(stand, mode):
    boundary = "python-stand-test"
    fields = {"prompt": "test prompt", "model": "test-low", "response_mode": mode}
    body = b"".join(
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'
        ).encode()
        for key, value in fields.items()
    )
    body += f"--{boundary}--\r\n".encode()
    return call(
        stand,
        "POST",
        "/api/requests",
        body,
        headers={"Content-Type": "multipart/form-data; boundary=" + boundary},
    )


@pytest.mark.parametrize("mode", ["finish", "text"])
def test_python_stand_chat_modes_and_report(stand, mode):
    assert form_request(stand, mode)[0] == 202
    value = settled(stand, "/api/request")
    assert value["status"] == "completed"
    assert value["result"]["output_mode"] == (
        "planner_text" if mode == "text" else "native_finish"
    )
    assert call(stand, "GET", "/api/requests/" + value["id"] + "/report")[0] == 200
