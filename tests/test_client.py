from __future__ import annotations

import json
import threading
import time

import pytest

from antigravity_engine_sdk import AntigravityError, Media

SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "integer"}, "nested": {"$ref": "#/$defs/nested"}},
    "required": ["answer", "nested"],
    "additionalProperties": False,
    "$defs": {
        "nested": {
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
        }
    },
}


def test_isolated_runtime_schema_media_reasoning_and_usage(boundary, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "must-not-be-inherited")
    client = boundary.client()
    progress = []
    text = "long input with quotes and real\nline breaks " * 4000
    result = client.generate(
        text,
        model="test-low",
        schema=SCHEMA,
        media=[Media(b"image", "image/png")],
        thinking="high",
        on_progress=progress.append,
    )
    assert result.output == {"answer": 42, "nested": {"ok": True}}
    assert result.model == "test-low" and result.native_model == "MODEL_TEST"
    assert result.usage[0]["inputTokens"] == "17"
    assert result.usage_scope == "trajectory_model_steps"
    assert result.engine_version == "2.23.1"
    assert {event.phase for event in progress} == {
        "starting",
        "working",
        "trajectory",
        "completed",
    }
    start = boundary.calls("StartCascade")[0]["body"]
    sent = boundary.calls("SendUserCascadeMessage")[0]["body"]
    assert start["requestedModel"] == "MODEL_TEST"
    assert start["customAgentSpec"]["customAgent"]["toolNames"] == ["finish"]
    assert text in sent["items"][0]["text"]
    assert "images" not in sent and sent["media"] == [
        {"mimeType": "image/png", "inlineData": "aW1hZ2U="}
    ]
    planner = sent["cascadeConfig"]["plannerConfig"]
    assert planner["requestedModel"] == {"model": "MODEL_TEST"}
    assert planner["completionConfigOverride"] == {"thinkingLevel": 3}
    assert (
        json.loads(planner["toolConfig"]["finish"]["resultJsonSchemaString"]) == SCHEMA
    )
    command, kwargs = boundary.launches[0]
    assert "--standalone" in command and "--exit_on_stdin_close" in command
    assert "--headless" not in command
    assert "GEMINI_API_KEY" not in kwargs["env"]
    assert not any("agy.exe" in arg for arg in command)


def test_client_reuses_engine_but_not_workspace_or_conversation(boundary):
    client = boundary.client()
    first = client.chat("hello", model="test-low")
    second = client.chat("hello again", model="test-low")
    assert first.text == second.text == "Native SDK answer"
    assert len(boundary.processes) == 1
    starts = boundary.calls("StartCascade")
    assert first.cascade_id != second.cascade_id
    assert starts[0]["body"]["workspaceUris"] != starts[1]["body"]["workspaceUris"]
    client.close()
    assert boundary.processes[0].poll() is not None


def test_clients_own_different_engines_and_close_independently(boundary):
    first, second = boundary.client(), boundary.client()
    first.models()
    second.models()
    assert len(boundary.processes) == 2
    first.close()
    assert boundary.processes[0].poll() is not None
    assert boundary.processes[1].poll() is None
    assert second.chat("still alive", model="test-low").text == "Native SDK answer"


def test_catalog_keeps_native_ids_and_has_no_product_policy(boundary):
    client = boundary.client()
    assert {model.id for model in client.models()} == {
        "test-low",
        "text-only",
        "gemini-2.5-pro",
    }
    assert "internal" in {model.id for model in client.models(include_internal=True)}
    assert client.authentication() == {
        "available": True,
        "eligibility_warning": "native warning",
        "ineligible": False,
    }
    assert "accessToken" not in client.authentication()


def test_dead_engine_is_replaced(boundary):
    client = boundary.client()
    client.models()
    boundary.processes[0].kill()
    boundary.processes[0].wait(timeout=5)
    assert client.chat("new engine", model="test-low").text
    assert len(boundary.processes) == 2


def test_catalog_reflects_provider_additions_and_removals_on_the_same_client(boundary):
    client = boundary.client()
    assert "text-only" in {model.id for model in client.models()}
    boundary.set("catalog_changed")
    models = {model.id: model for model in client.models()}
    assert "text-only" not in models and "new-model" in models
    assert models["new-model"].raw["providerFlag"] is True
    assert len(boundary.calls("GetAvailableModels")) == 2
    assert len(boundary.processes) == 1


@pytest.mark.parametrize(
    ("scenario", "code"),
    [
        ("send_error", "provider_error"),
        ("retired", "model_unavailable"),
    ],
)
def test_request_errors_do_not_poison_catalog_or_block_the_next_attempt(
    boundary, scenario, code
):
    client = boundary.client()
    boundary.set(scenario)
    with pytest.raises(AntigravityError) as caught:
        client.chat("test", model="gemini-2.5-pro")
    assert caught.value.code == code
    assert "gemini-2.5-pro" in {model.id for model in client.models()}
    boundary.set("success")
    assert client.chat("retry", model="gemini-2.5-pro").text == "Native SDK answer"
    assert len(boundary.calls("StartCascade")) == 2
    assert len(boundary.processes) == 1


@pytest.mark.parametrize(
    ("scenario", "code"),
    [
        ("invalid", "contract_violation"),
        ("extra", "contract_violation"),
        ("missing", "contract_violation"),
        ("invalid_json", "contract_violation"),
        ("no_finish", "incompatible_format"),
        ("pending_finish", "incompatible_format"),
        ("failed", "provider_error"),
        ("blocked", "content_rejected"),
        ("retired", "model_unavailable"),
        ("refused", "content_rejected"),
        ("no_id", "incompatible_format"),
        ("signed_out", "authentication_required"),
        ("crash", "engine_exited"),
    ],
)
def test_failed_results_never_become_success(boundary, scenario, code):
    boundary.set(scenario)
    with pytest.raises(AntigravityError) as caught:
        boundary.client().generate("test", model="test-low", schema=SCHEMA)
    assert caught.value.code == code


def test_send_error_also_cancels_the_created_cascade(boundary):
    boundary.set("send_error")
    with pytest.raises(AntigravityError) as caught:
        boundary.client().chat("test", model="test-low")
    assert caught.value.code == "provider_error"
    assert len(boundary.calls("CancelCascadeInvocation")) == 1


def test_missing_media_receipt_is_an_error(boundary):
    boundary.set("dropped_media")
    with pytest.raises(AntigravityError) as caught:
        boundary.client().chat(
            "test", model="test-low", media=[Media(b"image", "image/png")]
        )
    assert caught.value.code == "incompatible_image_input"


@pytest.mark.parametrize(
    "model, code",
    [("missing", "model_unavailable"), ("text-only", "incompatible_image_input")],
)
def test_model_and_vision_preflight(boundary, model, code):
    with pytest.raises(AntigravityError) as caught:
        boundary.client().chat(
            "test", model=model, media=[Media(b"image", "image/png")]
        )
    assert caught.value.code == code
    assert not boundary.calls("StartCascade")


def wait_for_call(boundary, method):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if boundary.calls(method):
            return
        time.sleep(0.02)
    pytest.fail(f"Did not reach {method}")


def test_concurrent_catalog_rpc_does_not_wait_for_generation(boundary):
    boundary.set("running")
    client = boundary.client()
    stop, refreshed = threading.Event(), threading.Event()
    errors, catalogs = [], []

    def generate():
        try:
            client.chat("wait", model="test-low", cancelled=stop.is_set)
        except AntigravityError as error:
            errors.append(error.code)

    def read_catalog():
        catalogs.append(client.rpc("GetAvailableModels", concurrent=True))
        refreshed.set()

    worker = threading.Thread(target=generate)
    reader = threading.Thread(target=read_catalog)
    worker.start()
    try:
        wait_for_call(boundary, "GetCascadeTrajectory")
        reader.start()
        assert refreshed.wait(0.5)
        assert worker.is_alive()
    finally:
        stop.set()
        worker.join(timeout=5)
        if reader.ident:
            reader.join(timeout=5)
    assert errors == ["cancelled"]
    assert "test-low" in catalogs[0]["response"]["models"]
    assert len(boundary.processes) == 1


def test_cancel_interrupts_request_and_keeps_successfully_stopped_engine(boundary):
    boundary.set("running")
    client = boundary.client()
    stop = threading.Event()
    errors = []

    def run():
        try:
            client.chat("wait", model="test-low", cancelled=stop.is_set)
        except AntigravityError as exc:
            errors.append(exc.code)

    worker = threading.Thread(target=run)
    worker.start()
    wait_for_call(boundary, "GetCascadeTrajectory")
    stop.set()
    worker.join(timeout=5)
    assert not worker.is_alive() and errors == ["cancelled"]
    assert (
        boundary.calls("CancelCascadeInvocation")[0]["body"]["killBackgroundTasks"]
        is True
    )
    boundary.set("success")
    assert client.chat("after stop", model="test-low").text
    assert len(boundary.processes) == 1


def test_timeout_cancels_and_close_is_terminal(boundary):
    client = boundary.client()
    client.models()
    boundary.set("running")
    with pytest.raises(AntigravityError) as caught:
        client.chat("wait", model="test-low", timeout=0.2)
    assert caught.value.code == "timeout"
    assert boundary.calls("CancelCascadeInvocation")
    client.close()
    with pytest.raises(AntigravityError) as caught:
        client.models()
    assert caught.value.code == "client_closed"


def test_startup_timeout_closes_owned_process(boundary):
    boundary.set("not_ready")
    with pytest.raises(AntigravityError) as caught:
        boundary.client(ready_seconds=0.2).models()
    assert caught.value.code == "timeout"
    assert boundary.processes[0].poll() is not None


def test_progress_callback_error_stops_invocation(boundary):
    def callback(event):
        if event.phase == "working":
            raise RuntimeError("consumer error")

    with pytest.raises(RuntimeError, match="consumer error"):
        boundary.client().chat("test", model="test-low", on_progress=callback)
    assert boundary.calls("CancelCascadeInvocation")


def test_failed_stop_rpc_retires_engine(boundary):
    client = boundary.client()

    def callback(event):
        if event.phase == "working":
            boundary.set("cancel_error")
            raise RuntimeError("consumer error")

    with pytest.raises(RuntimeError, match="consumer error"):
        client.chat("test", model="test-low", on_progress=callback)
    assert boundary.calls("CancelCascadeInvocation")
    assert boundary.processes[0].poll() is not None
    boundary.set("success")
    assert client.chat("recovered", model="test-low").text
    assert len(boundary.processes) == 2


def test_close_interrupts_active_request(boundary):
    boundary.set("running")
    client = boundary.client()
    errors = []

    def run():
        try:
            client.chat("wait", model="test-low")
        except AntigravityError as error:
            errors.append(error.code)

    worker = threading.Thread(target=run)
    worker.start()
    wait_for_call(boundary, "GetCascadeTrajectory")
    client.close()
    worker.join(timeout=5)
    assert not worker.is_alive() and errors == ["cancelled"]
    assert boundary.processes[0].poll() is not None


def test_close_during_process_launch_does_not_leak(boundary, monkeypatch):
    from antigravity_engine_sdk import runtime

    client = boundary.client()
    launching, release = threading.Event(), threading.Event()
    launch = runtime.subprocess.Popen
    errors = []

    def delayed_launch(*args, **kwargs):
        launching.set()
        assert release.wait(timeout=5)
        return launch(*args, **kwargs)

    def run():
        try:
            client.models()
        except AntigravityError as error:
            errors.append(error.code)

    monkeypatch.setattr(runtime.subprocess, "Popen", delayed_launch)
    worker = threading.Thread(target=run)
    worker.start()
    assert launching.wait(timeout=5)
    closer = threading.Thread(target=client.close)
    closer.start()
    deadline = time.monotonic() + 5
    while not client._closed and time.monotonic() < deadline:
        time.sleep(0.01)
    release.set()
    worker.join(timeout=5)
    closer.join(timeout=5)
    assert not worker.is_alive() and not closer.is_alive()
    assert errors == ["cancelled"]
    assert boundary.processes[0].poll() is not None


def test_cancelled_queued_request_does_not_send_another_invocation(boundary):
    boundary.set("running")
    client = boundary.client()
    stop = threading.Event()
    errors = []

    def run():
        try:
            client.chat("first", model="test-low", cancelled=stop.is_set)
        except AntigravityError as exc:
            errors.append(exc.code)

    worker = threading.Thread(target=run)
    worker.start()
    try:
        wait_for_call(boundary, "GetCascadeTrajectory")
        with pytest.raises(AntigravityError) as caught:
            client.chat("queued", model="test-low", timeout=0.1)
        assert caught.value.code == "timeout"
        assert len(boundary.calls("SendUserCascadeMessage")) == 1
    finally:
        stop.set()
        worker.join(timeout=5)
    assert errors == ["cancelled"]


@pytest.mark.parametrize(
    "options",
    [{"thinking": "extra-high"}, {"max_invocations": 0}, {"timeout": float("nan")}],
)
def test_invalid_request_options(boundary, options):
    with pytest.raises(AntigravityError) as caught:
        boundary.client().chat("test", model="test-low", **options)
    assert caught.value.code == "invalid_input"
    assert not boundary.launches


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "array"},
        {
            "type": "object",
            "properties": {"answer": {"$ref": "https://example.test/schema"}},
        },
    ],
)
def test_invalid_or_remote_schema_never_starts_engine(boundary, schema):
    with pytest.raises(AntigravityError) as caught:
        boundary.client().generate("test", model="test-low", schema=schema)
    assert caught.value.code == "invalid_schema"
    assert not boundary.launches


def test_unresolved_local_schema_reference_has_an_sdk_error(boundary):
    schema = {"type": "object", "properties": {"answer": {"$ref": "#/$defs/missing"}}}
    with pytest.raises(AntigravityError) as caught:
        boundary.client().generate("test", model="test-low", schema=schema)
    assert caught.value.code == "invalid_schema"
