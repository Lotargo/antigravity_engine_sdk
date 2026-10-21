from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from antigravity_engine_sdk import Client, Installation, runtime

# A real process and HTTP boundary; only the native executable is replaced.
SERVER = r"""
import base64, json, os, sys, threading, struct, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit
config, record = map(Path, sys.argv[1:])
sent = {}; count = 0; editor_documents = []; editor_primed = False
class Handler(BaseHTTPRequestHandler):
 def log_message(self, *args): pass
 def do_POST(self):
  global count, editor_primed
  scenario = json.loads(config.read_text())["value"]
  assert self.headers.get("X-Codeium-Csrf-Token")
  assert self.headers.get("Connect-Protocol-Version") == "1"
  method = self.path.rsplit("/", 1)[-1]
  raw = self.rfile.read(int(self.headers["Content-Length"]))
  streaming = self.headers.get("Content-Type") == "application/connect+json"
  body = json.loads(raw[5:] if streaming else raw)
  with record.open("a", encoding="utf8") as stream:
   stream.write(json.dumps({"method": method, "body": body}) + "\n")
  result = {}
  if streaming:
   self.send_response(200); self.send_header("Content-Type", "application/connect+json"); self.end_headers()
   def frame(value, flags=0):
    data = json.dumps(value).encode()
    self.wfile.write(bytes([flags]) + struct.pack(">I", len(data)) + data); self.wfile.flush()
   doc = body["document"]; source = body["requestSource"]
   if doc["timestamp"] == "1970-01-01T00:00:00Z":
    assert source == "COMMAND_REQUEST_SOURCE_SUPERCOMPLETE"
    assert doc["text"] == "" and doc["cursorOffset"] == "0" and not doc["isDirty"]
    assert Path(unquote(urlsplit(doc["absoluteUri"]).path).lstrip("/") if os.name == "nt" else unquote(urlsplit(doc["absoluteUri"]).path)).stat().st_mtime == 0
    if scenario == "editor_bootstrap_data": frame({"rawText": "unexpected generation"})
    editor_primed = True; frame({}, 2); return
   assert editor_primed and len(editor_documents) >= 2
   assert body["requestedModelId"] == ("MODEL_TAB" if source.endswith("SUPERCOMPLETE") else "MODEL_JUMP")
   if scenario == "editor_running":
    frame({"completionId": "partial"}); self.connection.recv(1); return
   if scenario == "editor_empty": frame({}, 2); return
   if scenario == "editor_bad_flag": frame({}, 1); return
   result = {"completionId":"real", "selectionEndLine":"1", "characterDiff":{"changes":[{"text":doc["text"],"type":"DIFF_CHANGE_TYPE_UNCHANGED"},{"text":"!","type":"DIFF_CHANGE_TYPE_INSERT"}]},"requestInfo":{"testReplay":editor_documents}}
   if source.endswith("TAB_JUMP"): result["jumpPosition"] = {"row":"0","col":"0"}
   if scenario == "editor_filtered": result["filterReason"] = {"reason":"no useful suggestion"}
   if scenario == "editor_bad_position": result["selectionStartLine"] = "invalid"
   frame(result)
   if scenario == "editor_no_terminal": return
   frame({"error":{"code":"internal"}} if scenario == "editor_terminal_error" else {}, 2); return
  if method == "RefreshContextForIdeAction": editor_documents.append(body["activeDocument"])
  if method == "HasAuthToken":
   if scenario == "auth_paused":
    while not config.with_suffix(".ready").exists(): time.sleep(0.01)
   result = {"hasToken": scenario != "signed_out"}
  if method == "GetAuthStatus": result = {"authResult": {"hasValidAuth": True, "uiMessage": "native warning", "accessToken": "do-not-return"}}
  if method == "GetAvailableModels":
   result = {"response": {"models": {
    "test-low": {"model": "MODEL_TEST", "displayName": "Test (Low)", "supportsImages": True},
    "text-only": {"model": "MODEL_TEXT", "supportsImages": False},
    "gemini-2.5-pro": {"model": "MODEL_CAPACITY", "displayName": "Capacity-dependent"},
    "internal": {"model": "MODEL_INTERNAL", "isInternal": True},
    "bad": {"displayName": "No enum"}
   }}}
   if scenario == "catalog_changed":
    result["response"]["models"].pop("text-only")
    result["response"]["models"]["new-model"] = {"model": "MODEL_NEW", "displayName": "New model", "providerFlag": True}
   if scenario.startswith("editor_"):
    result["response"]["models"].update({"tab_flash_lite_preview":{"model":"MODEL_TAB"},"tab_jump_flash_lite_preview":{"model":"MODEL_JUMP"}})
  if method == "StartCascade":
   count += 1
   result = {"cascadeId": "cascade-%d" % count} if scenario != "no_id" else {}
  if method == "SendUserCascadeMessage":
   sent[body["cascadeId"]] = body
   if scenario == "send_error":
    self.send_response(503); self.end_headers(); self.wfile.write(b'{"message":"no capacity"}'); return
  if method == "GetCascadeTrajectory":
   request = sent[body["cascadeId"]]
   tools = request["cascadeConfig"]["plannerConfig"]["toolConfig"]
   schema = json.loads(tools.get("finish", {}).get("resultJsonSchemaString", "{}"))
   out = {"text": "Native SDK answer"} if "text" in schema.get("properties", {}) else {"answer": 42, "nested": {"ok": True}}
   if "generateImage" in tools: out = {"status": "generated"}
   if scenario == "invalid": out = {"answer": "wrong type", "nested": {"ok": True}}
   if scenario == "extra": out["unexpected"] = True
   if scenario == "missing": out.pop(next(iter(out)))
   steps = [{"type": "CORTEX_STEP_TYPE_USER_INPUT", "userInput": {"media": request.get("media", []) if scenario != "dropped_media" else []}}]
   if "generateImage" in tools and scenario != "image_missing":
    directory = Path(tools["generateImage"]["outputDirectory"])
    image = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a1ZkAAAAASUVORK5CYII=")
    data = {"mimeType": "image/png"}
    if scenario == "image_inline": data["inlineData"] = base64.b64encode(image).decode()
    elif scenario == "image_external": data["uri"] = config.as_uri()
    else:
     path = directory / "generated.png"; path.write_bytes(image)
     data["uri"] = path.as_uri()
    steps.append({"type": "CORTEX_STEP_TYPE_GENERATE_IMAGE", "generateImage": {"modelName": "different-model" if scenario == "image_mismatch" else "image-test", "generatedMedia": data}})
   if scenario == "failed": steps.append({"type": "CORTEX_STEP_TYPE_ERROR_MESSAGE", "errorMessage": {"error": "provider failed"}})
   elif scenario == "blocked": steps.append({"type": "CORTEX_STEP_TYPE_ERROR_MESSAGE", "errorMessage": {"error": "PROHIBITED_CONTENT"}})
   elif scenario == "retired": steps.append({"type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE", "plannerResponse": {"response": "This model is no longer available."}})
   elif scenario == "refused": steps.append({"type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE", "plannerResponse": {"response": "I cannot help with this request."}})
   elif scenario == "no_finish": steps.append({"type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE", "plannerResponse": {"response": json.dumps(out)}})
   elif scenario != "running":
    final = {"type": "CORTEX_STEP_TYPE_FINISH", "status": "CORTEX_STEP_STATUS_PENDING" if scenario == "pending_finish" else "CORTEX_STEP_STATUS_DONE", "finish": {"outputString": "not json" if scenario == "invalid_json" else json.dumps(out)}, "metadata": {"modelUsage": {"inputTokens": "17", "outputTokens": "9", "model": "MODEL_TEST"}}}
    if "finish" not in tools:
     final.pop("finish"); final.update(type="CORTEX_STEP_TYPE_PLANNER_RESPONSE", plannerResponse={"response":"Native planner text\nsecond line"})
    steps.append(final)
   result = {"status": "CASCADE_RUN_STATUS_RUNNING" if scenario == "running" else "CASCADE_RUN_STATUS_IDLE", "trajectory": {"steps": steps}}
  if method == "CancelCascadeInvocation" and scenario == "cancel_error":
   self.send_response(500); self.end_headers(); self.wfile.write(b'{}'); return
  self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
  try: self.wfile.write(json.dumps(result).encode())
  except (BrokenPipeError, ConnectionResetError): pass
scenario = json.loads(config.read_text())["value"]
if scenario == "crash": sys.exit(7)
server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
if scenario != "not_ready": print("Language server listening on random port at %d for HTTP" % server.server_port, flush=True)
threading.Thread(target=lambda: (sys.stdin.buffer.read(), os._exit(0)), daemon=True).start()
server.serve_forever()
"""


class Boundary:
    def __init__(self, root: Path):
        self.config = root / "scenario.json"
        self.record = root / "requests.jsonl"
        self.set("success")
        self.processes = []
        self.launches = []
        self.clients = []

    def set(self, value):
        self.config.write_text(json.dumps({"value": value}), encoding="utf8")

    def calls(self, method=None):
        if not self.record.exists():
            return []
        calls = [
            json.loads(line)
            for line in self.record.read_text(encoding="utf8").splitlines()
        ]
        return [call for call in calls if method is None or call["method"] == method]

    def client(self, **kwargs):
        client = Client(timeout=kwargs.pop("timeout", 5), poll_interval=0.02, **kwargs)
        self.clients.append(client)
        return client


@pytest.fixture
def boundary(tmp_path, monkeypatch):
    script = tmp_path / "engine.py"
    script.write_text(SERVER, encoding="utf8")
    boundary = Boundary(tmp_path)
    real_popen = subprocess.Popen

    def launch(command, **kwargs):
        boundary.launches.append((command, kwargs))
        process = real_popen(
            [
                sys.executable,
                "-u",
                str(script),
                str(boundary.config),
                str(boundary.record),
            ],
            **kwargs,
        )
        boundary.processes.append(process)
        return process

    monkeypatch.setattr(
        runtime,
        "find_installation",
        lambda *_: Installation("language_server.exe", "2.23.1"),
    )
    monkeypatch.setattr(runtime.subprocess, "Popen", launch)
    runtime.begin_runtime()
    yield boundary
    for client in boundary.clients:
        client.close()
    runtime.shutdown()
    assert all(process.poll() is not None for process in boundary.processes), (
        "owned process leaked"
    )
    assert all(not Path(options["cwd"]).exists() for _, options in boundary.launches), (
        "temporary profile leaked"
    )
    assert not runtime._active
    runtime.begin_runtime()
