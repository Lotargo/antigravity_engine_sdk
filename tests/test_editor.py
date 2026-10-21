import hashlib
import json
import threading
import time
from dataclasses import replace

import pytest

from antigravity_engine_sdk import (
    AntigravityError,
    EditorDocument,
    EditorError,
    EditorResponse,
    EditorResult,
    EditorSession,
    Installation,
    runtime,
)


@pytest.fixture
def editor(boundary, monkeypatch):
    boundary.set("editor_success")
    monkeypatch.setattr(
        runtime,
        "find_installation",
        lambda *_: Installation("language_server.exe", "2.24.0"),
    )
    with EditorSession(timeout=5, poll_interval=0.02) as session:
        yield session


@pytest.mark.parametrize(
    "kind,model,status",
    [("supercomplete", "MODEL_TAB", "edit"), ("tab_to_jump", "MODEL_JUMP", "jump")],
)
def test_independent_editor_replays_actual_old_snapshots(
    editor, boundary, kind, model, status
):
    for text in ("old", "new"):
        editor.sync(EditorDocument("test.py", "python", text, 1))
    observed = "2000-01-01T00:00:00Z"
    editor._history[-1] = replace(editor._history[-1], observed_at=observed)
    frames = []
    result = getattr(editor, kind)(on_frame=frames.append)
    assert result.status == status and len(frames) == 1
    assert result.requested_native_model == model
    assert result.preparation.bootstrap_frames == []
    assert result.preparation.bootstrap_terminal == b"{}"
    assert result.preparation.snapshots[-1].observed_at == observed
    assert result.preview("new") == "new!"
    assert not editor._primed
    calls = boundary.calls("HandleStreamingCommand")
    assert len(calls) == 2  # One zero-inference initialization, one real generation.
    assert calls[0]["body"]["document"]["text"] == ""
    assert calls[0]["body"]["document"]["isDirty"] is False
    assert calls[1]["body"]["requestedModelId"] == model
    docs = result.response.raw["requestInfo"]["testReplay"]
    assert [d["text"] for d in docs] == ["", "old", "new"]
    assert docs[0]["absoluteUri"] != docs[1]["absoluteUri"]
    editor.sync(EditorDocument("test.py", "python", "new", 2))
    assert len(editor._history) == 2 and editor._history[-1].observed_at == observed
    assert result.to_dict()["frames"] == [json.loads(result.frames[0])]


@pytest.mark.parametrize("kind", ["supercomplete", "tab_to_jump"])
@pytest.mark.parametrize(
    "scenario,status,code",
    [
        ("editor_success", "edit", None),
        ("editor_empty", "empty", None),
        ("editor_filtered", "filtered", None),
        ("editor_bootstrap_data", "incomplete", "incompatible_format"),
        ("editor_terminal_error", "incomplete", "provider_error"),
        ("editor_no_terminal", "incomplete", "incompatible_format"),
        ("editor_bad_flag", "incomplete", "incompatible_format"),
        ("editor_bad_position", "incomplete", "incompatible_format"),
    ],
)
def test_editor_single_snapshot_and_outcomes(
    editor, boundary, kind, scenario, status, code
):
    boundary.set(scenario)
    editor.sync(EditorDocument("test.py", "python", "current"))
    if code:
        with pytest.raises(EditorError) as error:
            getattr(editor, kind)()
        assert error.value.code == code
        result = error.value.result
        with pytest.raises(AntigravityError):
            result.preview("current")
        if scenario == "editor_bootstrap_data":
            assert len(result.preparation.bootstrap_frames) == 1 and not result.frames
            assert len(boundary.calls("HandleStreamingCommand")) == 1
    else:
        result = getattr(editor, kind)()
        if status == "edit" and kind == "tab_to_jump":
            status = "jump"
    assert result.status == status


@pytest.mark.parametrize("kind", ["supercomplete", "tab_to_jump"])
def test_editor_stream_cancel_timeout_close_recovery(editor, boundary, kind):
    editor.sync(EditorDocument("test.py", "python", "current"))
    boundary.set("editor_running")
    stop = threading.Event()
    with pytest.raises(EditorError) as error:
        getattr(editor, kind)(cancelled=stop.is_set, on_frame=lambda _: stop.set())
    assert error.value.code == "cancelled" and len(error.value.result.frames) == 1
    boundary.set("editor_success")
    assert getattr(editor, kind)().status in {"edit", "jump"}
    boundary.set("editor_running")
    with pytest.raises(EditorError) as error:
        getattr(editor, kind)(on_frame=lambda _: editor.close())
    assert error.value.code == "cancelled"
    with pytest.raises(AntigravityError) as error:
        getattr(editor, kind)()
    assert error.value.code == "client_closed"


def test_editor_unsupported_version_and_not_ready(boundary):
    with EditorSession(timeout=5) as session:
        with pytest.raises(AntigravityError) as error:
            session.supercomplete()
        assert error.value.code == "editor_not_ready"
        boundary.set("editor_success")
        session.sync(EditorDocument("test.py", "python", "current"))
        with pytest.raises(EditorError) as error:
            session.supercomplete()
        assert error.value.code == "unsupported_editor_version"
        assert not boundary.calls("HandleStreamingCommand")


def test_editor_cancellation_interrupts_blocked_socket(editor, boundary):
    editor.sync(EditorDocument("test.py", "python", "current"))
    boundary.set("editor_running")
    stop, frame = threading.Event(), threading.Event()
    errors = []

    def run():
        try:
            editor.supercomplete(cancelled=stop.is_set, on_frame=lambda _: frame.set())
        except EditorError as error:
            errors.append(error)

    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert frame.wait(4)
        time.sleep(0.1)  # Reader is waiting on the next native frame.
        stop.set()
        worker.join(timeout=2)
        assert not worker.is_alive()
        assert errors[0].code == "cancelled" and len(errors[0].result.frames) == 1
    finally:
        stop.set()
        editor.close()
        worker.join(timeout=5)


def test_editor_deadline_interrupts_blocked_socket(editor, boundary):
    editor.sync(EditorDocument("test.py", "python", "current"))
    boundary.set("editor_running")
    with pytest.raises(EditorError) as error:
        editor.supercomplete(timeout=2)
    assert error.value.code == "timeout"
    assert len(error.value.result.frames) == 1


@pytest.mark.parametrize(
    "doc",
    [
        EditorDocument("../bad.py", "python", "x"),
        EditorDocument("CON.py", "python", "x"),
        EditorDocument("x.py", "python", "я", 1),
        EditorDocument("x.py", "python", "x", True),
        EditorDocument("x.py", "", "x"),
        EditorDocument("x.py", "python", "\ud800"),
    ],
)
def test_editor_rejects_paths_and_invalid_byte_positions(doc):
    with pytest.raises(AntigravityError) as error:
        doc.validate()
    assert error.value.code == "invalid_input"


def test_preview_is_exact_and_preserves_crlf_unicode():
    text = "# Привет 🛰️\r\nold\r\nlast\r\n"
    raw = {
        "selectionStartLine": "1",
        "selectionEndLine": "2",
        "characterDiff": {
            "changes": [
                {"text": "old", "type": "DIFF_CHANGE_TYPE_DELETE"},
                {"text": "новый 🛰️", "type": "DIFF_CHANGE_TYPE_INSERT"},
            ]
        },
    }
    result = EditorResult(
        "SUPERCOMPLETE",
        hashlib.sha256(text.encode()).hexdigest(),
        status="edit",
        response=EditorResponse.decode(json.dumps(raw).encode()),
    )
    assert result.preview(text) == text.replace("old", "новый 🛰️")
    with pytest.raises(AntigravityError) as error:
        result.preview(text + " ")
    assert error.value.code == "stale_edit"
    result.response.raw["characterDiff"]["changes"][0]["text"] = "other"
    with pytest.raises(AntigravityError) as error:
        result.preview(text)
    assert error.value.code == "stale_edit"
