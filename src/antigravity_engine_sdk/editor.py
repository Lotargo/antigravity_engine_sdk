"""Native editor requests, isolated snapshot replay and source-checked previews."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import secrets
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path

from .client import Client, _check, _positive
from .errors import AntigravityError


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf8")).hexdigest()


def _uint(value) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, (str, int))
        or not re.fullmatch(r"[0-9]+", str(value))
    ):
        raise AntigravityError(
            "Invalid native editor position.", code="incompatible_format"
        )
    number = int(value)
    if number >= 2**64:
        raise AntigravityError(
            "Native editor position exceeds uint64.", code="incompatible_format"
        )
    return number


@dataclass(frozen=True)
class EditorDocument:
    name: str
    language: str
    text: str
    cursor_offset: int = 0  # UTF-8 bytes, not Python characters or UTF-16 units.

    def validate(self) -> None:
        try:
            data = self.text.encode("utf8")
            stem = self.name.split(".", 1)[0].upper()
            valid = (
                isinstance(self.name, str)
                and bool(self.name)
                and self.name not in {".", ".."}
                and self.name.rstrip(" .") == self.name
                and not re.search(r'[/\\:<>"|?*\x00]', self.name)
                and not re.fullmatch(r"CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9]", stem)
                and isinstance(self.language, str)
                and bool(self.language)
                and len(data) <= 4 * 1024 * 1024
                and type(self.cursor_offset) is int
                and 0 <= self.cursor_offset <= len(data)
            )
            if valid:
                data[: self.cursor_offset].decode("utf8")
                return
        except (AttributeError, TypeError, UnicodeError):
            pass
        raise AntigravityError(
            "Editor requires a basename, language, UTF-8 text up to 4 MiB and a cursor on a UTF-8 boundary.",
            code="invalid_input",
        )


@dataclass(frozen=True)
class EditorPosition:
    row: int
    col: int


@dataclass(frozen=True)
class EditorResponse:
    raw: dict = field(repr=False)

    @classmethod
    def decode(cls, raw: bytes) -> EditorResponse:
        try:
            value = json.loads(raw.decode("utf8"))
            if not isinstance(value, dict):
                raise ValueError("Expected an object")
        except (ValueError, UnicodeError) as error:
            raise AntigravityError(
                "Invalid native editor response.", code="incompatible_format"
            ) from error
        for key in ("selectionStartLine", "selectionEndLine"):
            _uint(value.get(key, "0"))
        if value.get("jumpPosition") is not None:
            pos = value["jumpPosition"]
            if not isinstance(pos, dict):
                raise AntigravityError(
                    "Invalid native jump position.", code="incompatible_format"
                )
            _uint(pos.get("row", "0"))
            _uint(pos.get("col", "0"))
        if value.get("characterDiff") is not None:
            diff = value["characterDiff"]
            if not isinstance(diff, dict) or not isinstance(
                diff.get("changes", []), list
            ):
                raise AntigravityError(
                    "Invalid native character diff.", code="incompatible_format"
                )
            for change in diff.get("changes", []):
                if (
                    not isinstance(change, dict)
                    or not isinstance(change.get("text", ""), str)
                    or not isinstance(change.get("type", ""), str)
                ):
                    raise AntigravityError(
                        "Invalid native character change.", code="incompatible_format"
                    )
        return cls(value)

    @property
    def jump_position(self) -> EditorPosition | None:
        value = self.raw.get("jumpPosition")
        return (
            None
            if value is None
            else EditorPosition(
                _uint(value.get("row", "0")), _uint(value.get("col", "0"))
            )
        )

    @property
    def character_diff(self) -> dict | None:
        return self.raw.get("characterDiff")


@dataclass(frozen=True)
class EditorSnapshot:
    document: EditorDocument
    observed_at: str


@dataclass
class EditorPreparation:
    snapshots: tuple[EditorSnapshot, ...]
    mode: str = "snapshot_replay"
    replayed_at: str = ""
    bootstrap_frames: list[bytes] = field(default_factory=list, repr=False)
    bootstrap_terminal: bytes | None = field(default=None, repr=False)
    bootstrap_ms: int = 0
    bootstrap_document: dict | None = None
    bootstrap_native_model: str = ""


@dataclass
class EditorResult:
    kind: str
    source_sha256: str
    status: str = "incomplete"
    response: EditorResponse | None = None
    frames: list[bytes] = field(default_factory=list, repr=False)
    terminal: bytes | None = field(default=None, repr=False)
    engine_version: str = ""
    latency_ms: int = 0
    usage: str = "UNKNOWN"
    requested_model: str = ""
    requested_native_model: str = ""
    preparation: EditorPreparation | None = None

    def to_dict(self) -> dict:
        value = asdict(self)
        value["response"] = copy.deepcopy(self.response.raw) if self.response else None
        _json_frames(value, "frames", "terminal")
        if value["preparation"] is not None:
            _json_frames(value["preparation"], "bootstrap_frames", "bootstrap_terminal")
        return value

    def preview(self, text: str) -> str:
        """Apply only a final native diff matching this exact source. No disk writes."""
        if (
            self.status not in {"edit", "jump"}
            or not self.response
            or self.response.character_diff is None
            or self.source_sha256 != _hash(text)
        ):
            raise AntigravityError(
                "No final applicable edit for this exact snapshot.", code="stale_edit"
            )
        raw = self.response.raw
        starts = [0] + [i + 1 for i, char in enumerate(text) if char == "\n"]
        start, end = (
            _uint(raw.get("selectionStartLine", "0")),
            _uint(raw.get("selectionEndLine", "0")),
        )
        if end == start:
            end += 1
        if start >= len(starts) or end <= start or end > len(starts):
            raise AntigravityError(
                "Native edit range is outside the document.", code="incompatible_format"
            )
        a, b = starts[start], len(text)
        if end < len(starts):
            b = starts[end] - 1
            if b > 0 and text[b - 1] == "\r":
                b -= 1
        old, new = [], []
        for change in self.response.character_diff.get("changes", []):
            kind, value = change.get("type"), change.get("text", "")
            if kind == "DIFF_CHANGE_TYPE_UNCHANGED":
                old.append(value)
                new.append(value)
            elif kind == "DIFF_CHANGE_TYPE_DELETE":
                old.append(value)
            elif kind == "DIFF_CHANGE_TYPE_INSERT":
                new.append(value)
            else:
                raise AntigravityError(
                    "Unknown native character change type.", code="incompatible_format"
                )
        if "".join(old) != text[a:b]:
            raise AntigravityError(
                "Native diff source does not match selected text.", code="stale_edit"
            )
        return text[:a] + "".join(new) + text[b:]


def _json_frames(value, frames, terminal):
    value["raw_" + frames] = [raw.decode("utf8") for raw in value[frames]]
    value[frames] = [json.loads(raw) for raw in value[frames]]
    raw = value[terminal]
    value["raw_" + terminal] = raw.decode("utf8") if raw is not None else None
    value[terminal] = json.loads(raw) if raw is not None else None


class EditorError(AntigravityError):
    """An interrupted/failed request retains its partial evidence in result."""

    def __init__(self, error: AntigravityError, result: EditorResult):
        super().__init__(str(error), code=error.code, details=error.details)
        self.result = result


class EditorSession:
    """Dedicated editor client. Manual commands replay actual snapshots in a child.

    sync() does not generate. supercomplete()/tab_to_jump() need no recent edit.
    *_native() retains the automatic IDE path and its native age restriction.
    Callbacks are synchronous; do not call sync or generate from the callback.
    """

    def __init__(self, **client_options):
        self._options = client_options
        self._client = Client(**client_options)
        self._lock = threading.Lock()
        self._state = threading.Lock()
        self._closed = threading.Event()
        self._children: set[Client] = set()
        self._document: EditorDocument | None = None
        self._history: list[EditorSnapshot] = []
        self._workspace = ""
        self._uri = ""
        self._primed = False
        self._session_id = secrets.token_hex(24)

    def __enter__(self) -> EditorSession:
        if self._closed.is_set():
            raise AntigravityError("Editor is closed.", code="client_closed")
        return self

    def __exit__(self, *_exc):
        self.close()

    def close(self) -> None:
        # Never wait on the request lock: close must interrupt callbacks and reads.
        with self._state:
            self._closed.set()
            children = tuple(self._children)
        self._client.close()
        for child in children:
            child.close()

    @contextmanager
    def _request(self, timeout, cancelled):
        if self._closed.is_set():
            raise AntigravityError("Editor is closed.", code="client_closed")
        deadline = time.monotonic() + (
            self._client.timeout if timeout is None else _positive(timeout, "timeout")
        )

        def stopped():
            return self._closed.is_set() or bool(cancelled and cancelled())

        while True:
            _check(deadline, stopped)
            if self._lock.acquire(
                timeout=min(0.05, max(0, deadline - time.monotonic()))
            ):
                break
        try:
            _check(deadline, stopped)
            yield deadline, stopped
        finally:
            self._lock.release()

    def _doc(self, document, uri=None, workspace=None):
        return {
            "absoluteUri": uri or self._uri,
            "workspaceUri": workspace or self._workspace,
            "text": document.text,
            "editorLanguage": document.language,
            "cursorOffset": str(document.cursor_offset),
            "lineEnding": "\r\n" if "\r\n" in document.text else "\n",
            "isDirty": True,
            "timestamp": _now(),
        }

    def _refresh(self, session, doc, action, workspace):
        return session.rpc(
            "RefreshContextForIdeAction",
            {
                "metadata": {"sessionId": self._session_id},
                "activeDocument": doc,
                "openDocumentUris": [doc["absoluteUri"]],
                "workspaceUris": [workspace],
                "blocking": True,
                "ideAction": "IDE_ACTION_" + action,
            },
        )

    def sync(self, document: EditorDocument, *, timeout=None, cancelled=None) -> None:
        if not isinstance(document, EditorDocument):
            raise AntigravityError("Expected EditorDocument.", code="invalid_input")
        document.validate()
        with self._request(timeout, cancelled) as (deadline, stopped):
            if self._document and document.name != self._document.name:
                raise AntigravityError(
                    "Use a new editor session for another file.", code="invalid_input"
                )
            with self._client._session(
                max(0.001, deadline - time.monotonic()), stopped
            ) as session:
                root = session.root / "workspace"
                workspace = root.as_uri()
                if self._document and workspace != self._workspace:
                    raise AntigravityError(
                        "Native editor history was lost; create a new session.",
                        code="editor_not_ready",
                    )
                action = "SELECTION_CHANGED"
                if self._document is None:
                    (root / document.name).write_bytes(document.text.encode("utf8"))
                    self._workspace, self._uri = (
                        workspace,
                        (root / document.name).as_uri(),
                    )
                    session.rpc("AddTrackedWorkspace", {"workspace": workspace})
                    action = "STARTUP"
                self._refresh(session, self._doc(document), action, workspace)
                if (
                    not self._history
                    or self._history[-1].document.text != document.text
                ):
                    self._history.append(EditorSnapshot(document, _now()))
                    self._history = self._history[-2:]
                else:
                    self._history[-1] = replace(self._history[-1], document=document)
                self._document = document

    def supercomplete(
        self, *, timeout=None, cancelled=None, on_frame=None
    ) -> EditorResult:
        return self._complete("SUPERCOMPLETE", True, timeout, cancelled, on_frame)

    def tab_to_jump(
        self, *, timeout=None, cancelled=None, on_frame=None
    ) -> EditorResult:
        return self._complete("TAB_JUMP", True, timeout, cancelled, on_frame)

    def supercomplete_native(
        self, *, timeout=None, cancelled=None, on_frame=None
    ) -> EditorResult:
        return self._complete("SUPERCOMPLETE", False, timeout, cancelled, on_frame)

    def tab_to_jump_native(
        self, *, timeout=None, cancelled=None, on_frame=None
    ) -> EditorResult:
        return self._complete("TAB_JUMP", False, timeout, cancelled, on_frame)

    def _payload(self, doc, kind, model):
        return {
            "metadata": {"sessionId": self._session_id},
            "document": doc,
            "requestedModelId": model,
            "editorOptions": {"tabSize": "4", "insertSpaces": True},
            "requestSource": "COMMAND_REQUEST_SOURCE_" + kind,
            "diffType": "DIFF_TYPE_TMP_" + kind,
            "supercompleteTriggerCondition": "SUPERCOMPLETE_TRIGGER_CONDITION_TYPING",
            "ignoreSupercompleteDebounce": True,
        }

    def _complete(self, kind, replay, timeout, cancelled, on_frame):
        with self._request(timeout, cancelled) as (deadline, stopped):
            if self._document is None or (
                not replay and kind == "TAB_JUMP" and not self._primed
            ):
                raise AntigravityError(
                    "Sync first; automatic TabToJump also needs SupercompleteNative.",
                    code="editor_not_ready",
                )
            result = EditorResult(
                kind,
                _hash(self._document.text),
                requested_model="tab_flash_lite_preview"
                if kind == "SUPERCOMPLETE"
                else "tab_jump_flash_lite_preview",
            )
            started = time.monotonic()
            child = None
            try:
                if replay:
                    result.preparation = EditorPreparation(tuple(self._history))
                    child = Client(**self._options)
                    with self._state:
                        _check(deadline, stopped)
                        self._children.add(child)
                with (child or self._client)._session(
                    max(0.001, deadline - time.monotonic()), stopped
                ) as session:
                    result.engine_version = session.engine.installation.version
                    if replay and result.engine_version != "2.24.0":
                        raise AntigravityError(
                            "Independent editor requests are verified on native 2.24.0; no bootstrap was sent.",
                            code="unsupported_editor_version",
                        )
                    catalog = (
                        session.rpc("GetAvailableModels")
                        .get("response", {})
                        .get("models", {})
                    )
                    result.requested_native_model = catalog.get(
                        result.requested_model, {}
                    ).get("model", "")
                    bootstrap_model = catalog.get("tab_flash_lite_preview", {}).get(
                        "model", ""
                    )
                    if not result.requested_native_model or (
                        replay and not bootstrap_model
                    ):
                        raise AntigravityError(
                            "Native editor preview models are absent from catalog.",
                            code="model_unavailable",
                        )
                    if replay:
                        uri, workspace = self._prepare(
                            session, result.preparation, bootstrap_model
                        )
                    else:
                        workspace, uri = self._workspace, self._uri
                        if workspace != (session.root / "workspace").as_uri():
                            raise AntigravityError(
                                "Native history was lost.", code="editor_not_ready"
                            )

                    def frame(raw):
                        result.frames.append(raw)
                        result.response = EditorResponse.decode(raw)
                        if on_frame:
                            on_frame(result.response)

                    session.stream_rpc(
                        "HandleStreamingCommand",
                        self._payload(
                            self._doc(self._document, uri, workspace),
                            kind,
                            result.requested_native_model,
                        ),
                        on_frame=frame,
                        on_terminal=lambda raw: setattr(result, "terminal", raw),
                    )
                    raw = result.response.raw if result.response else None
                    result.status = "no_suggestion"
                    if raw is None:
                        result.status = "empty"
                    elif raw.get("filterReason") is not None:
                        result.status = "filtered"
                    elif result.response.jump_position is not None:
                        result.status = "jump"
                    elif any(
                        c["type"]
                        in {"DIFF_CHANGE_TYPE_INSERT", "DIFF_CHANGE_TYPE_DELETE"}
                        for c in (result.response.character_diff or {}).get(
                            "changes", []
                        )
                    ):
                        result.status = "edit"
                    if not replay and kind == "SUPERCOMPLETE" and result.frames:
                        self._primed = True
                    return result
            except AntigravityError as error:
                raise EditorError(error, result) from error
            finally:
                if child:
                    child.close()
                    with self._state:
                        self._children.discard(child)
                result.latency_ms = round((time.monotonic() - started) * 1000)

    def _prepare(self, session, prep, bootstrap_model):
        root = session.root / "workspace"
        workspace = root.as_uri()
        handle, name = tempfile.mkstemp(prefix=".sdk-context-", suffix=".py", dir=root)
        os.close(handle)
        os.utime(name, (0, 0))
        prime = self._doc(
            EditorDocument(Path(name).name, "python", ""),
            Path(name).as_uri(),
            workspace,
        )
        prime.update(timestamp="1970-01-01T00:00:00Z", isDirty=False)
        prep.bootstrap_document, prep.bootstrap_native_model = prime, bootstrap_model
        session.rpc("AddTrackedWorkspace", {"workspace": workspace})
        self._refresh(session, prime, "STARTUP", workspace)

        def unexpected(raw):
            prep.bootstrap_frames.append(raw)
            raise AntigravityError(
                "Bootstrap returned a data frame; no editor generation was sent.",
                code="incompatible_format",
            )

        started = time.monotonic()
        try:
            session.stream_rpc(
                "HandleStreamingCommand",
                self._payload(prime, "SUPERCOMPLETE", bootstrap_model),
                on_frame=unexpected,
                on_terminal=lambda raw: setattr(prep, "bootstrap_terminal", raw),
            )
        finally:
            prep.bootstrap_ms = round((time.monotonic() - started) * 1000)
        path = root / self._document.name
        path.write_bytes(self._history[0].document.text.encode("utf8"))
        prep.replayed_at = _now()
        for snapshot in self._history:
            self._refresh(
                session,
                self._doc(snapshot.document, path.as_uri(), workspace),
                "ACTIVE_EDITOR_CHANGED",
                workspace,
            )
        return path.as_uri(), workspace
