"""Bounded uncompressed Connect JSON framing, with interruptible socket reads."""

from __future__ import annotations

import http.client
import json
import socket
import struct
import threading
import time
from collections.abc import Callable

from .errors import AntigravityError


def stream_rpc(
    session,
    method: str,
    payload: dict,
    on_frame: Callable[[bytes], None],
    on_terminal: Callable[[bytes], None],
) -> None:
    session.check()
    raw = json.dumps(payload, ensure_ascii=False).encode("utf8")
    if len(raw) > 16 * 1024 * 1024:
        raise AntigravityError("Streaming request is too large.", code="invalid_input")
    connection = http.client.HTTPConnection(
        "127.0.0.1",
        session.engine.port,
        timeout=max(0.05, session.deadline - time.monotonic()),
    )
    done = threading.Event()
    transport_socket = None

    def interrupt():
        while not done.wait(0.02):
            try:
                session.check()
            except AntigravityError:
                if transport_socket is not None:
                    try:
                        transport_socket.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                return

    watcher = threading.Thread(target=interrupt, daemon=True)
    watcher.start()
    try:
        connection.connect()
        transport_socket = connection.sock
        session.check()
        from .runtime import SERVICE

        connection.request(
            "POST",
            SERVICE + method,
            body=b"\0" + struct.pack(">I", len(raw)) + raw,
            headers={
                "Content-Type": "application/connect+json",
                "Connect-Protocol-Version": "1",
                "X-Codeium-Csrf-Token": session.engine.csrf,
            },
        )
        with connection.getresponse() as response:
            if response.status != 200:
                raise AntigravityError(
                    "Native stream failed.",
                    code="authentication_required"
                    if response.status in {401, 403}
                    else "provider_error",
                    details={"method": method, "http_status": response.status},
                )
            if (
                response.getheader("Content-Type", "").split(";", 1)[0].strip()
                != "application/connect+json"
            ):
                raise AntigravityError(
                    "Unexpected native stream content type.", code="incompatible_format"
                )

            def read_exact(count):
                chunks, remaining = [], count
                while remaining:
                    session.check()
                    chunk = response.read(remaining)
                    if not chunk:
                        session.check()
                        raise AntigravityError(
                            "Truncated Connect stream or missing terminal frame.",
                            code="incompatible_format",
                        )
                    chunks.append(chunk)
                    remaining -= len(chunk)
                return b"".join(chunks)

            total = 0
            while True:
                header = read_exact(5)
                flags, size = header[0], struct.unpack(">I", header[1:])[0]
                total += size + 5
                if (
                    flags not in {0, 2}
                    or size > 16 * 1024 * 1024
                    or total > 64 * 1024 * 1024
                ):
                    raise AntigravityError(
                        "Unsupported or oversized Connect frame.",
                        code="incompatible_format",
                    )
                frame = read_exact(size)
                try:
                    value = json.loads(frame.decode("utf8"))
                    if not isinstance(value, dict):
                        raise ValueError("Expected an object")
                except (ValueError, UnicodeError) as exc:
                    raise AntigravityError(
                        "Connect frame is not a JSON object.",
                        code="incompatible_format",
                    ) from exc
                if flags == 2:
                    on_terminal(frame)
                    if value.get("error") is not None:
                        raise AntigravityError(
                            "Native stream returned a terminal error.",
                            code="provider_error",
                        )
                    session.check()
                    return
                on_frame(frame)
    except (OSError, http.client.HTTPException) as exc:
        session.check()
        if isinstance(exc, TimeoutError):
            raise AntigravityError(
                "Antigravity request timed out.", code="timeout"
            ) from exc
        raise AntigravityError(
            "Native stream connection failed.", code="engine_connection"
        ) from exc
    finally:
        done.set()
        connection.close()
        watcher.join(timeout=1)
