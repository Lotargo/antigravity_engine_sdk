"""Shared browser UI with direct Python SDK and an owned Go SDK backend."""

from __future__ import annotations

import argparse
import copy
import hmac
import ipaddress
import json
import secrets
import signal
import socket
import sys
import threading
import time
from dataclasses import asdict
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY / "src"))
sys.path.insert(0, str(REPOSITORY))

from antigravity_engine_sdk import (  # noqa: E402
    AntigravityError,
    Client,
    EditorDocument,
    EditorError,
    EditorSession,
    Media,
    __version__,
    runtime,
)
from antigravity_engine_sdk.models import MAX_MEDIA_BYTES  # noqa: E402
from python.testbench.go_backend import GoBackend  # noqa: E402

ASSETS = REPOSITORY / "go/testbench/web"
STATIC = {
    "app.js",
    "editor.js",
    "code-editor.js",
    "style.css",
    "editor.css",
    "editor-licenses.txt",
    "sdk.js",
}
BASE_POLICY = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def editor_example(kind):
    if kind == "supercomplete":
        text = "def add(a, b):\n    return \n"
        return {
            "before": text,
            "after": text,
            "cursor": len("def add(a, b):\n    return "),
        }
    before = (
        "def calculate_total(prices):\n    tax_rate = 0.1\n    subtotal = sum(prices)\n"
    )
    before += "".join(f"    # pricing calculation step {i}\n" for i in range(25))
    before += "    tax = subtotal * tax_rate\n    return subtotal + tax\n"
    after = before.replace("tax_rate", "vat_rate", 1)
    return {
        "before": before,
        "after": after,
        "cursor": after.index("vat_rate") + len("vat_rate"),
    }


def error_value(error):
    return {"code": getattr(error, "code", "stand_error"), "message": str(error)}


def result_value(result):
    value = asdict(result)
    value.pop("images")
    value["raw_response_sha256"] = result.raw_response_sha256
    return value


class Stand:
    def __init__(self, output, *, client_options=None, model="gemini-3.8-flash-low"):
        self.options = client_options or {}
        self.client = Client(**self.options)
        self.model, self.output = model, Path(output)
        self.token = secrets.token_hex(24)
        self.lock = threading.RLock()
        self.stopped = threading.Event()
        self.editor = None
        self.baseline = ""
        self.editor_busy = False
        self.editor_cancel = None
        self.editor_view = {"status": "not_ready", "frames": 0}
        self.job = None
        self.job_cancel = None
        self.job_started = 0
        self.workers = set()
        self.go = None
        self.go_error = "Соберите Go backend и перезапустите общий стенд. См. python/testbench/README.md."

    def backends(self):
        available = bool(self.go and self.go.available)
        return {
            "default": "python",
            "backends": [
                {
                    "id": "python",
                    "label": "Python",
                    "version": __version__,
                    "available": True,
                },
                {
                    "id": "go",
                    "label": "Go",
                    "version": self.go.config.get("version", "") if self.go else "",
                    "available": available,
                    "reason": "" if available else self.go_error,
                },
            ],
        }

    def close(self):
        self.stopped.set()
        with self.lock:
            for event in (self.editor_cancel, self.job_cancel):
                if event:
                    event.set()
            editor = self.editor
            workers = tuple(self.workers)
        self.client.close()
        if editor:
            editor.close()
        if self.go:
            self.go.close()
        for worker in workers:
            worker.join(timeout=10)

    def start(self, task):
        def run():
            try:
                task()
            finally:
                with self.lock:
                    self.workers.discard(threading.current_thread())

        worker = threading.Thread(target=run, daemon=True)
        with self.lock:
            if self.stopped.is_set():
                raise AntigravityError("Stand is closing.", code="cancelled")
            self.workers.add(worker)
            try:
                worker.start()
            except BaseException:
                self.workers.discard(worker)
                raise

    def save(self, name, value):
        path = self.output / name
        with path.open("x", encoding="utf8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
        return str(path)

    def editor_setup(self, document):
        document.validate()
        with self.lock:
            if self.editor_busy:
                raise AntigravityError("Editor is busy.", code="busy")
            self.editor_busy = True
        candidate = EditorSession(**self.options)
        try:
            candidate.sync(document, cancelled=self.stopped.is_set)
            with self.lock:
                if self.stopped.is_set():
                    raise AntigravityError("Stand is closing.", code="cancelled")
                old, self.editor = self.editor, candidate
                self.baseline = document.text
                self.editor_view = {"status": "ready", "frames": 0}
            if old:
                old.close()
        except BaseException:
            candidate.close()
            raise
        finally:
            with self.lock:
                self.editor_busy = False

    def editor_run(self, kind, document):
        if kind not in {"supercomplete", "tab_jump"}:
            raise AntigravityError("Unknown editor command.", code="invalid_input")
        document.validate()
        with self.lock:
            if self.editor_busy or self.editor is None:
                raise AntigravityError(
                    "Initialize the editor and wait for its current command.",
                    code="busy",
                )
            editor, baseline = self.editor, self.baseline
            event = self.editor_cancel = threading.Event()
            self.editor_busy = True
            identity = secrets.token_hex(24)
            view = self.editor_view = {"id": identity, "status": "running", "frames": 0}

        def task():
            result = None
            outcome = {"status": "completed"}

            def stopped():
                return event.is_set() or self.stopped.is_set()

            try:
                editor.sync(document, cancelled=stopped)

                def frame(response):
                    with self.lock:
                        view["frames"] += 1
                        view["last"] = copy.deepcopy(response.raw)

                method = (
                    editor.supercomplete
                    if kind == "supercomplete"
                    else editor.tab_to_jump
                )
                result = method(cancelled=stopped, on_frame=frame)
                if (
                    result.status in {"edit", "jump"}
                    and result.response
                    and result.response.character_diff
                ):
                    try:
                        outcome["preview"] = result.preview(document.text)
                    except AntigravityError as error:
                        outcome["preview_error"] = str(error)
            except Exception as error:
                result = error.result if isinstance(error, EditorError) else None
                outcome.update(
                    status="cancelled"
                    if getattr(error, "code", None) == "cancelled"
                    else "error",
                    error=str(error),
                )
            finally:
                with self.lock:
                    view.update(outcome)
                    view["result"] = result.to_dict() if result else None
                    report = {
                        "date": now()[:10],
                        "sdk_language": "Python",
                        "sdk_version": __version__,
                        "baseline": baseline,
                        "document": asdict(document),
                        "request_kind": kind,
                        "view": copy.deepcopy(view),
                        "usage": "UNKNOWN",
                        "model_selection": "native editor configuration",
                    }
                    try:
                        view["report"] = self.save(
                            "editor-" + identity + ".json", report
                        )
                    except OSError as error:
                        view["report_error"] = str(error)
                    self.editor_busy = False
                    self.editor_cancel = None

        self.start(task)
        return {"id": identity}

    def snapshot(self):
        with self.lock:
            value = copy.deepcopy(self.job)
            if value and value["status"] in {"running", "cancelling"}:
                value["duration_ms"] = round(
                    (time.monotonic() - self.job_started) * 1000
                )
            return value

    def submit(self, prompt, model, response_mode, media, attachment):
        if (
            not prompt.strip()
            or not model.strip()
            or response_mode not in {"finish", "text"}
        ):
            raise AntigravityError(
                "Prompt, model and valid response mode are required.",
                code="invalid_input",
            )
        with self.lock:
            if self.job and self.job["status"] in {"running", "cancelling"}:
                raise AntigravityError("A request is already running.", code="busy")
            identity = secrets.token_hex(24)
            event = self.job_cancel = threading.Event()
            self.job_started = time.monotonic()
            started = self.job_started
            job = self.job = {
                "id": identity,
                "mode": "live",
                "sdk_version": __version__,
                "sdk_language": "Python",
                "response_mode": response_mode,
                "status": "running",
                "phase": "starting",
                "model": model,
                "prompt": prompt,
                "attachment": attachment,
                "started_at": now(),
                "duration_ms": 0,
                "usage_note": "UNKNOWN until completed",
            }

        def task():
            def progress(value):
                with self.lock:
                    job["phase"] = value.phase
                    if value.trajectory:
                        job["partial_trajectory"] = value.trajectory

            try:
                method = (
                    self.client.complete
                    if response_mode == "text"
                    else self.client.chat
                )
                result = method(
                    prompt,
                    model=model,
                    media=media,
                    cancelled=lambda: event.is_set() or self.stopped.is_set(),
                    on_progress=progress,
                )
                outcome = {
                    "status": "completed",
                    "result": result_value(result),
                    "usage_note": "trajectory_model_steps",
                }
            except Exception as error:
                outcome = {
                    "status": "cancelled"
                    if getattr(error, "code", None) == "cancelled"
                    else "error",
                    "error": error_value(error),
                }
            finally:
                with self.lock:
                    job.update(outcome)
                    job["duration_ms"] = round((time.monotonic() - started) * 1000)
                    try:
                        job["report"] = "request-" + identity + ".json"
                        self.save(job["report"], job)
                    except OSError as error:
                        job.pop("report", None)
                        job["report_error"] = str(error)
                    self.job_cancel = None

        self.start(task)
        return self.snapshot()


def handler(stand):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def send(self, status, value, kind="application/json", *, nonce=None):
            body = (
                json.dumps(value, ensure_ascii=False).encode("utf8")
                if kind == "application/json"
                else value
            )
            self.send_response(status)
            self.send_header("Content-Type", kind + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            csp = (
                BASE_POLICY.replace(
                    "style-src 'self'", "style-src 'self' 'nonce-" + nonce + "'"
                )
                if nonce
                else BASE_POLICY
            )
            self.send_header("Content-Security-Policy", csp)
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def guard(self):
            host = self.headers.get("Host", "")
            try:
                if not ipaddress.ip_address(urlsplit("//" + host).hostname).is_loopback:
                    raise ValueError()
            except ValueError:
                self.send(403, {"error": {"message": "Loopback host required"}})
                return False
            if self.headers.get("Origin") not in {None, "http://" + host}:
                self.send(403, {"error": {"message": "Origin rejected"}})
                return False
            if urlsplit(self.path).path.startswith("/api/") and not hmac.compare_digest(
                self.headers.get("X-Testbench-Token", "").encode("utf8"),
                stand.token.encode("ascii"),
            ):
                self.send(403, {"error": {"message": "Reload the stand page"}})
                return False
            return True

        def do_GET(self):
            if not self.guard():
                return
            path = urlsplit(self.path).path
            if path == "/api/backends":
                self.send(200, stand.backends())
                return
            if path.startswith("/api/") and self.route_sdk():
                return
            try:
                if path in {"/", "/editor"}:
                    nonce = secrets.token_hex(24) if path == "/editor" else None
                    text = (
                        ASSETS / ("editor.html" if path == "/editor" else "index.html")
                    ).read_text(encoding="utf8")
                    text = (
                        text.replace("{{.Token}}", stand.token)
                        .replace("{{.StyleNonce}}", nonce or "")
                        .replace("{{.SDKLabel}}", "Python")
                    )
                    self.send(200, text.encode("utf8"), "text/html", nonce=nonce)
                elif path[1:] in STATIC:
                    kind = (
                        "text/javascript"
                        if path.endswith(".js")
                        else "text/css"
                        if path.endswith(".css")
                        else "text/plain"
                    )
                    self.send(200, (ASSETS / path[1:]).read_bytes(), kind)
                elif path == "/api/config":
                    self.send(
                        200,
                        {
                            "mode": "live",
                            "model": stand.model,
                            "version": __version__,
                            "sdk_language": "Python",
                            "max_media_bytes": MAX_MEDIA_BYTES,
                        },
                    )
                elif path == "/api/models":
                    self.send(
                        200,
                        [
                            asdict(model)
                            for model in sorted(
                                stand.client.models(concurrent=True), key=lambda m: m.id
                            )
                        ],
                    )
                elif path == "/api/request":
                    self.send(200, stand.snapshot())
                elif path == "/api/editor/example":
                    self.send(
                        200,
                        editor_example(
                            parse_qs(urlsplit(self.path).query).get("kind", [""])[0]
                        ),
                    )
                elif path == "/api/editor/state":
                    with stand.lock:
                        self.send(200, copy.deepcopy(stand.editor_view))
                elif path.startswith("/api/requests/") and path.endswith("/report"):
                    with stand.lock:
                        job = stand.job
                        if (
                            not job
                            or path != "/api/requests/" + job["id"] + "/report"
                            or not job.get("report")
                        ):
                            self.send(404, {"error": {"message": "Report not found"}})
                        else:
                            self.send(
                                200,
                                json.loads(
                                    (stand.output / job["report"]).read_text(
                                        encoding="utf8"
                                    )
                                ),
                            )
                else:
                    self.send(404, {"error": {"message": "Not found"}})
            except Exception as error:
                self.send(500, {"error": error_value(error)})

        def read_body(self, maximum):
            size = int(self.headers.get("Content-Length", "-1"))
            if not 0 <= size <= maximum:
                raise AntigravityError(
                    "Request body is missing or too large.", code="invalid_input"
                )
            self.connection.settimeout(10)
            data = self.rfile.read(size)
            if len(data) != size:
                raise AntigravityError("Truncated request body.", code="invalid_input")
            return data

        def read_form(self):
            kind = self.headers.get("Content-Type", "")
            if not kind.startswith("multipart/form-data;"):
                raise AntigravityError("Expected multipart form.", code="invalid_input")
            data = self.read_body(MAX_MEDIA_BYTES + 1024 * 1024)
            message = BytesParser(policy=policy.default).parsebytes(
                ("Content-Type: " + kind + "\r\nMIME-Version: 1.0\r\n\r\n").encode()
                + data
            )
            fields, media, attachment = {}, [], ""
            for part in message.iter_parts():
                name = part.get_param("name", header="content-disposition")
                raw = part.get_payload(decode=True)
                if name == "image":
                    if media or not raw or len(raw) > MAX_MEDIA_BYTES:
                        raise AntigravityError(
                            "Attach one image, up to 20 MiB.", code="invalid_input"
                        )
                    mime = (
                        "image/png"
                        if raw.startswith(b"\x89PNG\r\n\x1a\n")
                        else "image/jpeg"
                        if raw.startswith(b"\xff\xd8\xff")
                        else "image/gif"
                        if raw[:6] in {b"GIF87a", b"GIF89a"}
                        else "image/webp"
                        if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP"
                        else ""
                    )
                    if not mime:
                        raise AntigravityError(
                            "Use PNG, JPEG, WebP or GIF.", code="invalid_input"
                        )
                    media.append(Media(raw, mime))
                    attachment = (
                        (part.get_filename() or "image")
                        .replace("\\", "/")
                        .rsplit("/", 1)[-1]
                    )
                elif name in {"prompt", "model", "response_mode"}:
                    fields[name] = raw.decode("utf8")
            return fields, media, attachment

        def route_sdk(self):
            selected = self.headers.get("X-Testbench-SDK", "python")
            if selected == "python":
                return False
            if selected != "go":
                self.send(400, {"error": {"message": "Unknown SDK"}})
                return True
            try:
                if not stand.go or not stand.go.available:
                    raise RuntimeError(stand.go_error)
                body = (
                    self.read_body(MAX_MEDIA_BYTES + 5 * 1024 * 1024)
                    if self.command == "POST"
                    else None
                )
                status, raw = stand.go.request(
                    self.command, self.path, body, self.headers.get("Content-Type")
                )
                self.send(status, json.loads(raw))
            except Exception as error:
                self.send(502, {"error": {"code": "go_backend", "message": str(error)}})
            return True

        def do_POST(self):
            if not self.guard():
                return
            if self.route_sdk():
                return
            path = urlsplit(self.path).path
            try:
                if path == "/api/requests":
                    fields, media, attachment = self.read_form()
                    self.send(
                        202,
                        stand.submit(
                            fields.get("prompt", ""),
                            fields.get("model", ""),
                            fields.get("response_mode", "finish"),
                            media,
                            attachment,
                        ),
                    )
                elif path.startswith("/api/editor/"):
                    data = json.loads(self.read_body(25 * 1024 * 1024) or b"{}")
                    if not isinstance(data, dict):
                        raise AntigravityError(
                            "Expected JSON object.", code="invalid_input"
                        )
                    if path == "/api/editor/setup":
                        stand.editor_setup(EditorDocument(**data))
                        self.send(200, {"status": "ready"})
                    elif path == "/api/editor/run":
                        self.send(
                            202,
                            stand.editor_run(
                                data.get("kind"), EditorDocument(**data["document"])
                            ),
                        )
                    elif path == "/api/editor/cancel":
                        with stand.lock:
                            if stand.editor_cancel:
                                stand.editor_cancel.set()
                                stand.editor_view["status"] = "cancelling"
                            self.send(200, {"status": stand.editor_view["status"]})
                    else:
                        self.send(404, {"error": {"message": "Not found"}})
                elif path.startswith("/api/requests/") and path.endswith("/cancel"):
                    self.read_body(1024)
                    with stand.lock:
                        if (
                            not stand.job
                            or path != "/api/requests/" + stand.job["id"] + "/cancel"
                        ):
                            self.send(404, {"error": {"message": "Request not found"}})
                        else:
                            if stand.job_cancel and stand.job["status"] in {
                                "running",
                                "cancelling",
                            }:
                                stand.job_cancel.set()
                                stand.job["status"] = "cancelling"
                            self.send(200, stand.snapshot())
                else:
                    self.send(404, {"error": {"message": "Not found"}})
            except Exception as error:
                status = 409 if getattr(error, "code", None) == "busy" else 400
                self.send(status, {"error": error_value(error)})

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listen", default="127.0.0.1:8773")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--executable")
    parser.add_argument("--model", default="gemini-3.8-flash-low")
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument(
        "--go-testbench",
        type=Path,
        default=REPOSITORY
        / "go/testbench/bin"
        / (
            "antigravity-testbench-shared.exe"
            if sys.platform == "win32"
            else "antigravity-testbench-shared"
        ),
    )
    parser.add_argument(
        "--python-only", action="store_true", help="do not start an owned Go backend"
    )
    args = parser.parse_args()
    host, port = args.listen.rsplit(":", 1)
    host = host.strip("[]")
    if not ipaddress.ip_address(host).is_loopback:
        parser.error("--listen must be a loopback IP:port")
    output = args.output or REPOSITORY / "reports" / (
        "python-live-" + datetime.now().strftime("%Y%m%d-%H%M%S.%f")
    )
    output.mkdir(parents=True, exist_ok=False)
    stand = Stand(
        output,
        client_options={"executable": args.executable, "timeout": args.timeout},
        model=args.model,
    )

    class Server(ThreadingHTTPServer):
        address_family = socket.AF_INET6 if ":" in host else socket.AF_INET

    server = Server((host, int(port)), handler(stand))
    if not args.python_only:
        try:
            stand.go = GoBackend(
                args.go_testbench,
                output,
                native=args.executable,
                timeout=args.timeout,
                model=args.model,
            )
            stand.go_error = "Go backend остановлен. Перезапустите общий стенд."
        except Exception as error:
            print(f"Go SDK unavailable: {error}", flush=True)

    def stop(*_):
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, stop)
    print(
        f"Antigravity shared testbench | LIVE | Python {__version__}"
        f"{' + Go ' + stand.go.config['version'] if stand.go else ''}\n"
        f"Open http://{args.listen}/editor\nReports: {output}",
        flush=True,
    )
    try:
        server.serve_forever()
    finally:
        server.server_close()
        stand.close()
        runtime.shutdown()


if __name__ == "__main__":
    main()
