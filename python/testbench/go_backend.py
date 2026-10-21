"""Owned Go testbench process and a small, loopback-only JSON API proxy."""

from __future__ import annotations

import http.client
import json
import queue
import re
import subprocess
import threading
from pathlib import Path

from antigravity_engine_sdk._process import (
    _fallback_terminate_process_tree,
    _subprocess_options,
    _WindowsJob,
)

API_PATH = re.compile(
    r"/api/(?:config|models|request|requests|requests/[a-f0-9]+/(?:cancel|report)"
    r"|editor/(?:example|state|setup|run|cancel))"
)


class GoBackend:
    def __init__(self, executable, output, *, native=None, timeout=180, model=None):
        self.process = self.job = None
        self.reader = None
        self.log = None
        self.port = 0
        self.token = ""
        self.config = {}
        binary = Path(executable).resolve(strict=True)
        self.log = (Path(output) / "go-backend.log").open("x", encoding="utf8")
        args = [
            str(binary),
            "--live",
            "--ui",
            "--listen",
            "127.0.0.1:0",
            "--output",
            str(Path(output) / "go"),
            "--timeout",
            f"{timeout}s",
        ]
        if native:
            args += ["--executable", str(native)]
        if model:
            args += ["--model", model]
        ready = queue.Queue(maxsize=1)

        def read_output():
            announced = False
            for line in self.process.stdout:
                self.log.write(line)
                self.log.flush()
                match = re.fullmatch(r"Open http://127\.0\.0\.1:([0-9]+)\s*", line)
                if match and not announced:
                    ready.put(int(match[1]))
                    announced = True
            if not announced:
                ready.put(0)

        try:
            self.process = subprocess.Popen(
                args,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf8",
                errors="replace",
                **_subprocess_options(),
            )
            self.job = _WindowsJob.attach(self.process)
            self.reader = threading.Thread(target=read_output, daemon=True)
            self.reader.start()
            self.port = ready.get(timeout=20)
            if not self.port:
                raise RuntimeError(
                    "Go backend exited during startup; see go-backend.log"
                )
            _, page = self._call("GET", "/")
            match = re.search(rb'name="testbench-token" content="([a-f0-9]+)"', page)
            if not match:
                raise RuntimeError("Go backend returned no testbench token")
            self.token = match[1].decode("ascii")
            status, raw = self.request("GET", "/api/config")
            self.config = json.loads(raw)
            if (
                status != 200
                or self.config.get("sdk_language") != "Go"
                or self.config.get("mode") != "live"
            ):
                raise RuntimeError("Expected a live Go SDK backend")
        except BaseException:
            self.close()
            raise

    @property
    def available(self):
        return bool(self.process and self.process.poll() is None and self.token)

    def _call(self, method, path, body=None, content_type=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=240)
        try:
            headers = {"X-Testbench-Token": self.token}
            if content_type:
                headers["Content-Type"] = content_type
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            data = response.read(96 * 1024 * 1024 + 1)
            if len(data) > 96 * 1024 * 1024:
                raise RuntimeError("Go response is too large")
            return response.status, data
        finally:
            connection.close()

    def request(self, method, path, body=None, content_type=None):
        if not self.available:
            raise RuntimeError("Go backend stopped; restart the shared stand")
        if not API_PATH.fullmatch(path.split("?", 1)[0]):
            raise ValueError("Unsupported Go API route")
        return self._call(method, path, body, content_type)

    def close(self):
        if self.job:
            self.job.terminate()
            self.job.close()
        elif self.process and self.process.poll() is None:
            _fallback_terminate_process_tree(self.process)
        if self.process:
            self.process.wait(timeout=10)
        if self.reader:
            self.reader.join(timeout=2)
        if self.process and self.process.stdout:
            self.process.stdout.close()
        if self.log:
            self.log.close()
