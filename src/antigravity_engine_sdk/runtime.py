"""Owned, windowless Antigravity 2 runtime. No agy CLI or exported Google tokens."""

from __future__ import annotations

import atexit
import json
import os
import re
import secrets
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ._process import (
    _fallback_terminate_process_tree,
    _subprocess_options,
    _WindowsJob,
)
from ._sanitize import sanitize_output
from .errors import AntigravityError

SERVICE = "/exa.language_server_pb.LanguageServerService/"
_active: set[Engine] = set()
_lifetime_lock = threading.RLock()
_closing = False


@dataclass(frozen=True)
class Installation:
    executable: str
    version: str


def _package_version(resources: Path) -> str:
    with (resources / "app.asar").open("rb") as stream:
        _, header_size, _, json_size = struct.unpack("<4I", stream.read(16))
        if json_size > 8 * 1024 * 1024:
            raise ValueError("Invalid Antigravity package header")
        entry = json.loads(stream.read(json_size))["files"]["package.json"]
        stream.seek(8 + header_size + int(entry["offset"]))
        package = json.loads(stream.read(min(int(entry["size"]), 100_000)))
    version = package["version"]
    if not isinstance(version, str) or not re.fullmatch(
        r"2\.\d+\.\d+(?:[-.][\w.]+)?", version
    ):
        raise ValueError("Antigravity 2.0 is required")
    return version


def find_installation(requested: str | None = None) -> Installation | None:
    """Discover only the 2.0 native engine; an installed IDE or agy is insufficient."""
    explicit = requested or os.environ.get("ANTIGRAVITY_ENGINE_PATH")
    if explicit:
        candidates = [Path(explicit).expanduser()]
    elif sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        candidates = (
            [Path(local) / "Programs/antigravity/resources/bin/language_server.exe"]
            if local
            else []
        )
    elif sys.platform == "darwin":
        candidates = [
            root / "Antigravity.app/Contents/Resources/bin/language_server"
            for root in [Path("/Applications"), Path.home() / "Applications"]
        ]
    else:
        candidates = [
            Path(root) / "resources/bin/language_server"
            for root in [
                "/opt/Antigravity",
                "/opt/antigravity",
                "/usr/share/antigravity",
            ]
        ]
    for binary in candidates:
        if binary.name not in {"language_server.exe", "language_server"}:
            continue
        try:
            if binary.is_file():
                return Installation(
                    str(binary.resolve()), _package_version(binary.parent.parent)
                )
        except (OSError, ValueError, KeyError, struct.error):
            continue
    return None


def begin_runtime() -> None:
    global _closing
    with _lifetime_lock:
        _closing = False


def shutdown() -> None:
    global _closing
    with _lifetime_lock:
        _closing = True
        engines = list(_active)
    for engine in engines:
        engine.close()


atexit.register(shutdown)


# Native readiness budgets, separate from request deadlines.
REQUEST_READY_SECONDS = 60.0
STARTUP_READY_SECONDS = 90.0


class Engine:
    """One process/profile; stdin and a Job Object bound its lifetime.

    A client reuses its own engine; ``with Engine(...)`` gives direct RPC access.
    """

    def __init__(
        self,
        installation: Installation,
        *,
        timeout: float = 240,
        cancelled: Callable[[], bool] | None = None,
        ready_seconds: float = REQUEST_READY_SECONDS,
    ):
        self.installation = installation
        self.deadline = time.monotonic() + timeout
        self.ready_seconds = ready_seconds
        self.cancelled = cancelled
        self.port = 0
        self.csrf = secrets.token_urlsafe(32)
        self.logs: deque[str] = deque(maxlen=60)
        self.process = None
        self.job = None
        self.temp = None
        self.root: Path | None = None
        self._lock = threading.RLock()
        self._closed = False
        self._ready = False
        self._auth_prompt = False
        self._readers: list[threading.Thread] = []
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def usable(self) -> bool:
        return (
            not self._closed
            and self._ready
            and not self._auth_prompt
            and self.process is not None
            and self.process.poll() is None
        )

    def check(self) -> None:
        if self.cancelled and self.cancelled():
            raise AntigravityError(
                "Antigravity request was cancelled.", code="cancelled"
            )
        if self._closed:
            raise AntigravityError("Antigravity runtime stopped.", code="cancelled")
        if time.monotonic() >= self.deadline:
            raise AntigravityError("Antigravity request timed out.", code="timeout")
        if self.process is not None and self.process.poll() is not None:
            raise AntigravityError(
                "Antigravity engine exited unexpectedly.", code="engine_exited"
            )
        if self._auth_prompt:
            raise AntigravityError(
                "Sign in once in Antigravity 2.0, then press Re-check.",
                code="authentication_required",
            )

    def _drain(self, stream) -> None:
        try:
            for line in iter(stream.readline, ""):
                # Never retain an OAuth URL (including state/code) or forward native logs to UI.
                if (
                    "accounts.google.com/" in line
                    or "authorization code:" in line.lower()
                ):
                    self._auth_prompt = True
                    continue
                match = re.search(
                    r"listening on .*?port at (\d+) for HTTP(?:$|\s)", line
                )
                if match:
                    self.port = int(match[1])
                self.logs.append(sanitize_output(line, maximum=2000))
        except (OSError, ValueError):
            pass

    def __enter__(self) -> Engine:
        try:
            with _lifetime_lock, self._lock:
                if _closing:
                    raise AntigravityError(
                        "Application is shutting down.", code="cancelled"
                    )
                self.check()
                self.temp = tempfile.TemporaryDirectory(
                    prefix="antigravity-sdk-", ignore_cleanup_errors=True
                )
                self.root = Path(self.temp.name)
                config = self.root / "gemini/config"
                config.mkdir(parents=True)
                (config / "config.json").write_text("{}", encoding="utf8")
                (config / "mcp_config.json").write_text(
                    '{"mcpServers":{}}', encoding="utf8"
                )
                (self.root / "workspace").mkdir()
                env = dict(os.environ, ANTIGRAVITY_VSCODE_HOST="1")
                for key in [
                    "GEMINI_API_KEY",
                    "GOOGLE_API_KEY",
                    "AGY_ADC_AUTH",
                    "GOOGLE_GENAI_USE_VERTEXAI",
                ]:
                    env.pop(key, None)
                command = [
                    self.installation.executable,
                    "--standalone",
                    "--exit_on_stdin_close",
                    "--disable_telemetry",
                    "--gemini_dir",
                    str(self.root / "gemini"),
                    "--config_dir",
                    "config",
                    "--app_data_dir",
                    "antigravity-engine-sdk",
                    "--csrf_token",
                    self.csrf,
                    "--override_ide_name",
                    "antigravity",
                    "--override_ide_version",
                    self.installation.version,
                    "--override_user_agent_name",
                    "antigravity",
                    "--subclient_type",
                    "hub",
                    "--api_server_url",
                    "https://generativelanguage.googleapis.com",
                    "--cloud_code_endpoint",
                    "https://daily-cloudcode-pa.googleapis.com",
                ]
                self.process = subprocess.Popen(
                    command,
                    cwd=self.root / "workspace",
                    env=env,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf8",
                    errors="replace",
                    **_subprocess_options(),
                )
                self.job = _WindowsJob.attach(self.process)
                _active.add(self)
                for stream in [self.process.stdout, self.process.stderr]:
                    reader = threading.Thread(
                        target=self._drain, args=(stream,), daemon=True
                    )
                    reader.start()
                    self._readers.append(reader)
            ready_by = min(self.deadline, time.monotonic() + self.ready_seconds)
            while time.monotonic() < ready_by:
                self.check()
                if self.port:
                    try:
                        auth = self.rpc("HasAuthToken", timeout=1)
                        if not auth.get("hasToken"):
                            raise AntigravityError(
                                "Sign in once in Antigravity 2.0, then press Re-check.",
                                code="authentication_required",
                            )
                        self._ready = True
                        return self
                    except AntigravityError as exc:
                        if exc.code not in {"engine_connection", "engine_busy"}:
                            raise
                time.sleep(0.1)
            raise AntigravityError(
                f"Antigravity engine did not become ready within {self.ready_seconds:g} seconds.",
                code="timeout",
            )
        except BaseException as exc:
            self.close()
            if isinstance(exc, OSError):
                raise AntigravityError(
                    "Antigravity runtime could not be started or read.",
                    code="engine_unavailable",
                ) from exc
            raise

    def rpc(
        self, method: str, payload: dict | None = None, *, timeout: float = 10
    ) -> dict:
        self.check()
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{SERVICE}{method}",
            data=json.dumps(payload or {}, ensure_ascii=False).encode("utf8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Connect-Protocol-Version": "1",
                "X-Codeium-Csrf-Token": self.csrf,
            },
        )
        try:
            with self.opener.open(
                request,
                timeout=max(0.05, min(timeout, self.deadline - time.monotonic())),
            ) as response:
                raw = response.read(16 * 1024 * 1024 + 1)
            if len(raw) > 16 * 1024 * 1024:
                raise AntigravityError(
                    "Antigravity response is too large.", code="incompatible_format"
                )
            data = json.loads(raw)
            self.check()
            if not isinstance(data, dict):
                raise ValueError("Expected a JSON object")
            return data
        except urllib.error.HTTPError as exc:
            detail = sanitize_output(exc.read(8000).decode("utf8", errors="replace"))
            raise AntigravityError(
                f"Antigravity {method} failed ({exc.code}): {detail}",
                code="authentication_required"
                if exc.code in {401, 403}
                else "provider_error",
            ) from exc
        except (OSError, urllib.error.URLError) as exc:
            self.check()
            raise AntigravityError(
                "Antigravity engine is not responding.", code="engine_connection"
            ) from exc
        except (ValueError, UnicodeError) as exc:
            raise AntigravityError(
                "Antigravity returned invalid JSON.", code="incompatible_format"
            ) from exc

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self.process:
                if self.process.stdin:
                    try:
                        self.process.stdin.close()
                    except OSError:
                        pass
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    if self.job:
                        self.job.terminate()
                    _fallback_terminate_process_tree(self.process)
                    try:
                        self.process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        pass
                if self.job:
                    self.job.close()
                for reader in self._readers:
                    reader.join(timeout=1)
                for stream in [self.process.stdout, self.process.stderr]:
                    if stream:
                        stream.close()
            if self.temp:
                self.temp.cleanup()
        with _lifetime_lock:
            _active.discard(self)

    def __exit__(self, *_exc) -> None:
        self.close()


class Session:
    """One request on a shared engine: its own deadline and cancellation, not the engine's."""

    def __init__(
        self,
        engine: Engine,
        *,
        timeout: float,
        cancelled: Callable[[], bool] | None = None,
    ):
        self.engine = engine
        self.deadline = time.monotonic() + timeout
        self.cancelled = cancelled

    @property
    def root(self) -> Path | None:
        return self.engine.root

    def check(self) -> None:
        if self.cancelled and self.cancelled():
            raise AntigravityError(
                "Antigravity request was cancelled.", code="cancelled"
            )
        if time.monotonic() >= self.deadline:
            raise AntigravityError("Antigravity request timed out.", code="timeout")
        self.engine.check()

    def rpc(
        self, method: str, payload: dict | None = None, *, timeout: float = 10
    ) -> dict:
        self.check()
        remaining = self.deadline - time.monotonic()
        try:
            data = self.engine.rpc(
                method,
                payload,
                timeout=max(0.05, min(timeout, remaining)),
            )
        except AntigravityError as exc:
            # A read cut short by this request's own stop or deadline reports that, not the engine.
            if exc.code == "engine_connection":
                self.check()
                cause = exc.__cause__
                if isinstance(cause, urllib.error.URLError):
                    cause = cause.reason
                # Socket timers may fire just before the monotonic deadline.
                # Only a read bounded by this request's remaining budget is a
                # request timeout; shorter per-RPC timers retain their error.
                if remaining <= timeout and isinstance(cause, TimeoutError):
                    raise AntigravityError(
                        "Antigravity request timed out.", code="timeout"
                    ) from exc
            raise
        self.check()
        return data

    def stream_rpc(self, method, payload, *, on_frame, on_terminal) -> None:
        """Native Connect stream. Callbacks receive literal JSON frame bytes."""
        from ._stream import stream_rpc

        stream_rpc(self, method, payload, on_frame, on_terminal)
