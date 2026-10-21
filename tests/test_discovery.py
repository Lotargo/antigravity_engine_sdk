import json
import struct

from antigravity_engine_sdk import find_installation
from antigravity_engine_sdk._sanitize import sanitize_output


def test_only_native_v2_installation_is_accepted(tmp_path, monkeypatch):
    resources = tmp_path / "resources"
    (resources / "bin").mkdir(parents=True)
    engine = resources / "bin/language_server.exe"
    engine.touch()
    package = json.dumps({"version": "2.23.1"}).encode()
    header = json.dumps(
        {"files": {"package.json": {"offset": "0", "size": len(package)}}}
    ).encode()
    (resources / "app.asar").write_bytes(
        struct.pack("<4I", 4, len(header) + 8, len(header) + 4, len(header))
        + header
        + package
    )
    monkeypatch.setenv("ANTIGRAVITY_ENGINE_PATH", str(engine))
    assert find_installation().version == "2.23.1"
    assert find_installation(str(engine)).executable == str(engine.resolve())
    legacy = tmp_path / "agy.exe"
    legacy.touch()
    assert find_installation(str(legacy)) is None
    (resources / "app.asar").write_bytes(b"broken")
    assert find_installation(str(engine)) is None


def test_native_diagnostics_are_redacted():
    text = sanitize_output(
        "\x1b[31mAuthorization: Bearer secret-value\x1b[0m\napi_key=private-value"
    )
    assert "secret-value" not in text and "private-value" not in text
    assert "[redacted]" in text
