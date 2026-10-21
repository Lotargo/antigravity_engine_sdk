"""Synchronous client with an owned, reusable engine and isolated requests."""

from __future__ import annotations

import base64
import binascii
import json
import math
import mimetypes
import tempfile
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from jsonschema.exceptions import SchemaError, ValidationError
from jsonschema.validators import validator_for

from . import runtime
from ._refusal import is_provider_block, is_refusal
from .errors import AntigravityError
from .models import THINKING_LEVELS, ImageArtifact, Media, Model, ProgressEvent, Result

_TEXT_SCHEMA = {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
    "additionalProperties": False,
}
_IMAGE_SCHEMA = {
    "type": "object",
    "properties": {"status": {"type": "string"}},
    "required": ["status"],
    "additionalProperties": False,
}
_ENGINE_FAULTS = {"engine_connection", "engine_exited", "authentication_required"}
_IMAGE_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def _positive(value: float, name: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        raise AntigravityError(
            f"{name} must be a positive finite number.", code="invalid_input"
        )
    return float(value)


def _check(deadline: float, cancelled: Callable[[], bool] | None) -> None:
    if cancelled and cancelled():
        raise AntigravityError("Antigravity request was cancelled.", code="cancelled")
    if time.monotonic() >= deadline:
        raise AntigravityError("Antigravity request timed out.", code="timeout")


def _trajectory_error(trajectory: dict) -> None:
    steps = trajectory.get("steps", [])
    for step in steps:
        if "ERROR" in step.get("type", "") or step.get("status") in {
            "CORTEX_STEP_STATUS_ERROR",
            "CORTEX_STEP_STATUS_FAILED",
        }:
            detail = step.get("errorMessage", {})
            blocked = is_provider_block(json.dumps(detail, ensure_ascii=False))
            raise AntigravityError(
                "Antigravity could not complete the request.",
                code="content_rejected" if blocked else "provider_error",
                details=detail,
            )


def _planner_text(trajectory: dict) -> str:
    _trajectory_error(trajectory)
    parts = []
    for step in trajectory.get("steps", []):
        message = step.get("plannerResponse", {}).get("response", "")
        if isinstance(message, str) and any(
            marker in message.casefold()
            for marker in (
                "is no longer available",
                "is no longer supported",
                "has been retired",
            )
        ):
            raise AntigravityError(
                "Selected model has been retired.", code="model_unavailable"
            )
        if (
            step.get("type") == "CORTEX_STEP_TYPE_PLANNER_RESPONSE"
            and step.get("status")
            not in {"CORTEX_STEP_STATUS_RUNNING", "CORTEX_STEP_STATUS_PENDING"}
            and isinstance(message, str)
            and message.strip()
        ):
            parts.append(message)
    if not parts:
        raise AntigravityError(
            "Engine returned no completed planner text.", code="incompatible_format"
        )
    return "\n".join(parts)


def _finish(trajectory: dict) -> str:
    _trajectory_error(trajectory)
    steps = trajectory.get("steps", [])
    for step in reversed(steps):
        if step.get("type") == "CORTEX_STEP_TYPE_FINISH" and step.get("status") not in {
            "CORTEX_STEP_STATUS_RUNNING",
            "CORTEX_STEP_STATUS_PENDING",
        }:
            finish = step.get("finish", {})
            output = finish.get("outputString") or finish.get("output", {}).get(
                "arguments_json"
            )
            if isinstance(output, str) and output.strip():
                return output
    for step in steps:
        message = step.get("plannerResponse", {}).get("response", "")
        if isinstance(message, str):
            if any(
                marker in message.casefold()
                for marker in (
                    "is no longer available",
                    "is no longer supported",
                    "has been retired",
                )
            ):
                raise AntigravityError(message[:1000], code="model_unavailable")
            if is_refusal(message):
                raise AntigravityError(
                    "Antigravity declined the request.",
                    code="content_rejected",
                    details=message[:8000],
                )
    raise AntigravityError(
        "Antigravity returned no native finish result.",
        code="incompatible_format",
        details=trajectory,
    )


class Client:
    """One engine per client, started lazily and closed by the context manager.

    Calls on one client are serialized. Separate clients own separate processes.
    Deadlines include queueing, startup and RPC. No inference runs on import/open.
    """

    def __init__(
        self,
        executable: str | None = None,
        *,
        installation: runtime.Installation | None = None,
        timeout: float = 240,
        ready_seconds: float = runtime.REQUEST_READY_SECONDS,
        poll_interval: float = 0.2,
    ):
        if installation is not None and executable is not None:
            raise AntigravityError(
                "Choose installation or executable, not both.", code="invalid_input"
            )
        self.installation = installation
        self.executable = executable
        self.timeout = _positive(timeout, "timeout")
        self.ready_seconds = _positive(ready_seconds, "ready_seconds")
        self.poll_interval = _positive(poll_interval, "poll_interval")
        self._engine: runtime.Engine | None = None
        self._closed = False
        self._requests = threading.Lock()
        self._state = threading.Lock()

    def __enter__(self) -> Client:
        if self._closed:
            raise AntigravityError("Client is closed.", code="client_closed")
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def close(self) -> None:
        """Stop only this client's engine; also interrupts a running request."""
        with self._state:
            self._closed = True
            engine, self._engine = self._engine, None
        if engine is not None:
            engine.close()

    def _retire(self, engine: runtime.Engine) -> None:
        with self._state:
            if self._engine is engine:
                self._engine = None
        engine.close()

    @contextmanager
    def _session(
        self, timeout: float | None, cancelled: Callable[[], bool] | None
    ) -> Iterator[runtime.Session]:
        deadline = time.monotonic() + (
            self.timeout if timeout is None else _positive(timeout, "timeout")
        )

        def stopped() -> bool:
            return self._closed or bool(cancelled and cancelled())

        if self._closed:
            raise AntigravityError("Client is closed.", code="client_closed")
        while True:
            _check(deadline, stopped)
            if self._requests.acquire(
                timeout=min(0.05, max(0, deadline - time.monotonic()))
            ):
                break
        engine = None
        try:
            _check(deadline, stopped)
            engine = self._engine
            if engine is None or not engine.usable():
                if engine is not None:
                    self._retire(engine)
                installation = self.installation or runtime.find_installation(
                    self.executable
                )
                if installation is None:
                    raise AntigravityError(
                        "Install Antigravity 2.x and sign in once.",
                        code="engine_unavailable",
                    )
                self.installation = installation
                engine = runtime.Engine(
                    installation,
                    timeout=max(0.001, deadline - time.monotonic()),
                    cancelled=stopped,
                    ready_seconds=self.ready_seconds,
                )
                with self._state:
                    _check(deadline, stopped)
                    self._engine = engine
                engine.__enter__()
                # The process is reusable; each request owns its deadline and cancellation.
                engine.deadline, engine.cancelled = math.inf, None
            _check(deadline, stopped)
            yield runtime.Session(
                engine,
                timeout=max(0.001, deadline - time.monotonic()),
                cancelled=stopped,
            )
        except AntigravityError as exc:
            if engine is not None and (
                exc.code in _ENGINE_FAULTS or not engine.usable()
            ):
                self._retire(engine)
            raise
        finally:
            self._requests.release()

    def rpc(
        self,
        method: str,
        payload: dict | None = None,
        *,
        timeout: float | None = None,
        cancelled: Callable[[], bool] | None = None,
        concurrent: bool = False,
    ) -> dict:
        """Escape hatch to native RPC; methods/fields depend on the installed build.

        High-level tool restrictions do not apply to arbitrary RPC calls.
        ``concurrent=True`` lets independent metadata reads use an already
        running engine during generation. The caller owns native RPC ordering.
        """
        if (
            not isinstance(method, str)
            or not method
            or not method.replace("_", "").isalnum()
            or not method.isascii()
        ):
            raise AntigravityError("Invalid RPC method name.", code="invalid_input")
        with self._rpc_session(timeout, cancelled, concurrent) as session:
            return session.rpc(
                method, payload, timeout=self.timeout if timeout is None else timeout
            )

    @contextmanager
    def _rpc_session(
        self,
        timeout: float | None,
        cancelled: Callable[[], bool] | None,
        concurrent: bool,
    ) -> Iterator[runtime.Session]:
        # Reuse an already running process without acquiring the inference lock.
        # Cold start still goes through the normal serialized ownership path.
        budget = self.timeout if timeout is None else _positive(timeout, "timeout")
        with self._state:
            if self._closed:
                raise AntigravityError("Client is closed.", code="client_closed")
            engine = self._engine
        if not concurrent or engine is None or not engine.usable():
            with self._session(budget, cancelled) as session:
                yield session
            return
        try:
            session = runtime.Session(
                engine,
                timeout=budget,
                cancelled=lambda: self._closed or bool(cancelled and cancelled()),
            )
            session.check()
            yield session
        except AntigravityError as exc:
            if exc.code in _ENGINE_FAULTS:
                self._retire(engine)
            raise

    def authentication(self, *, timeout: float | None = None) -> dict:
        """Read sign-in status; never return native token fields."""
        auth = self.rpc("GetAuthStatus", timeout=timeout).get("authResult", {})
        return {
            "available": bool(auth.get("hasValidAuth")),
            "eligibility_warning": auth.get("uiMessage"),
            "ineligible": "ineligible" in auth,
        }

    def models(
        self,
        *,
        include_internal: bool = False,
        timeout: float | None = None,
        concurrent: bool = False,
    ) -> tuple[Model, ...]:
        """Return native catalog IDs unchanged, without VEIL-specific exclusions."""
        catalog = (
            self.rpc("GetAvailableModels", timeout=timeout, concurrent=concurrent)
            .get("response", {})
            .get("models", {})
        )
        return tuple(
            Model(
                id=key,
                native_id=card["model"],
                name=card.get("displayName") or key,
                supports_images=bool(card.get("supportsImages")),
                raw=card,
            )
            for key, card in catalog.items()
            if card.get("model") and (include_internal or not card.get("isInternal"))
        )

    def chat(self, prompt: str, *, model: str, **options: Any) -> Result:
        """One independent text turn. See generate() for media/reasoning/cancellation."""
        return self.generate(prompt, model=model, schema=_TEXT_SCHEMA, **options)

    def generate(
        self,
        prompt: str,
        *,
        model: str,
        schema: Mapping[str, Any],
        instructions: str = "",
        media: Sequence[Media] = (),
        thinking: str | None = None,
        timeout: float | None = None,
        cancelled: Callable[[], bool] | None = None,
        on_progress: Callable[[ProgressEvent], None] | None = None,
        max_invocations: int = 3,
    ) -> Result:
        """Generate and locally validate a JSON object returned through native finish.

        Native catalog suffixes select catalog reasoning variants. ``thinking`` sets
        the per-request native override; support depends on the chosen model.
        Only finish is enabled. Progress contains polling snapshots, not token deltas.
        """
        return self._execute(
            prompt,
            model=model,
            schema=schema,
            instructions=instructions,
            media=media,
            thinking=thinking,
            timeout=timeout,
            cancelled=cancelled,
            on_progress=on_progress,
            max_invocations=max_invocations,
        )

    def complete(
        self,
        prompt: str,
        *,
        model: str,
        instructions: str = "",
        media: Sequence[Media] = (),
        thinking: str | None = None,
        timeout: float | None = None,
        cancelled: Callable[[], bool] | None = None,
        on_progress: Callable[[ProgressEvent], None] | None = None,
        max_invocations: int = 1,
        schema: Mapping[str, Any] | None = None,
    ) -> Result:
        """Literal planner text with no tools or finish; never an implicit fallback."""
        if schema is not None:
            raise AntigravityError(
                "complete() does not accept a schema; use generate().",
                code="invalid_input",
            )
        return self._execute(
            prompt,
            model=model,
            schema=None,
            instructions=instructions,
            media=media,
            thinking=thinking,
            timeout=timeout,
            cancelled=cancelled,
            on_progress=on_progress,
            max_invocations=max_invocations,
            plain_text=True,
        )

    def generate_image(
        self,
        prompt: str,
        *,
        planner: str,
        expected_model: str | None = None,
        media: Sequence[Media] = (),
        thinking: str | None = None,
        timeout: float | None = None,
        cancelled: Callable[[], bool] | None = None,
        on_progress: Callable[[ProgressEvent], None] | None = None,
    ) -> Result:
        """Run the native generate_image tool and capture artifacts before cleanup.

        ``planner`` is a text model. ``expected_model`` asserts the returned image
        model; native 2.23.1 exposes no reliable image-model selection parameter.
        Reference media is passed through; editing fidelity is not guaranteed.
        """
        instructions = "Generate an actual image using generate_image, then call finish with a status string. Do not substitute a description or code for an image."
        if expected_model:
            instructions += f" The expected image model is {expected_model}."
        return self._execute(
            prompt,
            model=planner,
            schema=_IMAGE_SCHEMA,
            instructions=instructions,
            media=media,
            thinking=thinking,
            timeout=timeout,
            cancelled=cancelled,
            on_progress=on_progress,
            max_invocations=5,
            image_generation=True,
            expected_model=expected_model,
        )

    def _execute(
        self,
        prompt: str,
        *,
        model: str,
        schema: Mapping[str, Any] | None,
        instructions: str,
        media: Sequence[Media],
        thinking: str | None,
        timeout: float | None,
        cancelled: Callable[[], bool] | None,
        on_progress: Callable[[ProgressEvent], None] | None,
        max_invocations: int,
        image_generation: bool = False,
        plain_text: bool = False,
        expected_model: str | None = None,
    ) -> Result:
        started = time.monotonic()
        if (
            not isinstance(prompt, str)
            or not prompt.strip()
            or not isinstance(model, str)
            or not model.strip()
        ):
            raise AntigravityError(
                "Prompt and model must be nonempty strings.", code="invalid_input"
            )
        if thinking is not None and thinking not in THINKING_LEVELS:
            raise AntigravityError(
                "thinking must be minimal, low, medium or high; omit for native default.",
                code="invalid_input",
            )
        if type(max_invocations) is not int or max_invocations < 1:
            raise AntigravityError(
                "max_invocations must be a positive integer.", code="invalid_input"
            )
        validator = None
        if not plain_text:
            try:
                schema = json.loads(json.dumps(dict(schema)))
                if schema.get("type") != "object":
                    raise ValueError("finish requires an object schema")
                validator_type = validator_for(schema)
                validator_type.check_schema(schema)
                # Do not fetch remote $refs during local validation.
                _local_references(schema)
                validator = validator_type(schema)
            except (SchemaError, ValueError, TypeError) as exc:
                raise AntigravityError(
                    "Invalid object JSON Schema.",
                    code="invalid_schema",
                    details=str(exc),
                ) from exc
        attachments = [item.native() for item in media]

        def emit(
            phase: str, cascade_id: str | None = None, trajectory: dict | None = None
        ) -> None:
            if on_progress:
                on_progress(ProgressEvent(phase, cascade_id, trajectory))

        emit("starting")
        with self._session(timeout, cancelled) as session:
            cards = (
                session.rpc("GetAvailableModels").get("response", {}).get("models", {})
            )
            card = cards.get(model)
            if not card or not card.get("model") or card.get("isInternal"):
                raise AntigravityError(
                    "The selected model is unavailable in the native catalog.",
                    code="model_unavailable",
                )
            if attachments and not card.get("supportsImages"):
                raise AntigravityError(
                    "The selected model does not support images.",
                    code="incompatible_image_input",
                )
            with tempfile.TemporaryDirectory(
                prefix="request-", dir=session.root
            ) as directory:
                workspace = Path(directory)
                generated = workspace / "images"
                generated.mkdir()
                tools = (
                    ["generate_image", "finish"]
                    if image_generation
                    else ([] if plain_text else ["finish"])
                )
                cascade = session.rpc(
                    "StartCascade",
                    {
                        "source": "CORTEX_TRAJECTORY_SOURCE_SDK",
                        "requestedModel": card["model"],
                        "workspaceUris": [workspace.as_uri()],
                        "backend": "BACKEND_TYPE_LOCAL",
                        "customAgentSpec": {"customAgent": {"toolNames": tools}},
                    },
                ).get("cascadeId")
                if not isinstance(cascade, str) or not cascade:
                    raise AntigravityError(
                        "Engine returned no conversation ID.",
                        code="incompatible_format",
                    )
                complete = False
                try:
                    tool_config = {
                        "autoInteractionBehavior": "AUTO_INTERACTION_BEHAVIOR_DENY_ALL"
                    }
                    if not plain_text:
                        tool_config["finish"] = {
                            "resultJsonSchemaString": json.dumps(schema)
                        }
                    if image_generation:
                        tool_config["generateImage"] = {
                            "forceDisable": False,
                            "outputDirectory": str(generated),
                            "saveToParentArtifactDirectory": False,
                        }
                    planner = {
                        "requestedModel": {"model": card["model"]},
                        "toolConfig": tool_config,
                    }
                    if thinking is not None:
                        planner["completionConfigOverride"] = {
                            "thinkingLevel": THINKING_LEVELS[thinking]
                        }
                    package = f"{instructions}\n\n{prompt}\n\nTRANSPORT OUTPUT\nCall finish with the required result object. Use plain text and real line breaks inside field values. Do not return the object as planner prose. Use only the supplied tools."
                    if plain_text:
                        package = (
                            instructions + "\n\n" + prompt if instructions else prompt
                        )
                    request = {
                        "cascadeId": cascade,
                        "items": [{"text": package}],
                        "cascadeConfig": {
                            "applyModelDefaultOverride": True,
                            "plannerConfig": planner,
                            "executorConfig": {
                                "maxGeneratorInvocations": max_invocations,
                                "requireFinishTool": not plain_text,
                            },
                        },
                        "blocking": False,
                    }
                    if attachments:
                        request["media"] = attachments
                    session.rpc("SendUserCascadeMessage", request)
                    emit("working", cascade)
                    while True:
                        session.check()
                        response = session.rpc(
                            "GetCascadeTrajectory",
                            {
                                "cascadeId": cascade,
                                "verbosity": "CLIENT_TRAJECTORY_VERBOSITY_PROD_UI",
                            },
                            timeout=2,
                        )
                        trajectory = response.get("trajectory", {})
                        steps = trajectory.get("steps", [])
                        emit("trajectory", cascade, trajectory)
                        if (
                            response.get("status") == "CASCADE_RUN_STATUS_IDLE"
                            and len(steps) > 1
                        ):
                            complete = True
                            break
                        time.sleep(
                            min(
                                self.poll_interval,
                                max(0, session.deadline - time.monotonic()),
                            )
                        )
                finally:
                    if not complete:
                        # Cancellation bypasses this request's expired deadline, never
                        # changes a shared process's cancellation state.
                        try:
                            session.engine.rpc(
                                "CancelCascadeInvocation",
                                {"cascadeId": cascade, "killBackgroundTasks": True},
                                timeout=1,
                            )
                        except Exception:
                            # An uncertain cancellation cannot leave work in the background.
                            self._retire(session.engine)
                if plain_text:
                    raw_output = _planner_text(trajectory)
                    output = {"text": raw_output}
                else:
                    raw_output = _finish(trajectory)
                    try:
                        output = json.loads(raw_output)
                        assert validator is not None
                        validator.validate(output)
                    except (ValueError, ValidationError) as exc:
                        raise AntigravityError(
                            "Native finish violates the requested schema.",
                            code="contract_violation",
                            details=str(exc),
                        ) from exc
                    except Exception as exc:
                        # jsonschema's local-reference resolver uses its own exception
                        # hierarchy; retain it as a schema error, not a raw library error.
                        raise AntigravityError(
                            "A local schema reference could not be resolved.",
                            code="invalid_schema",
                            details=str(exc),
                        ) from exc
                if attachments and not any(
                    step.get("userInput", {}).get("media") for step in steps
                ):
                    raise AntigravityError(
                        "Engine did not retain the attached media.",
                        code="incompatible_image_input",
                    )
                images = (
                    _collect_images(steps, generated, expected_model)
                    if image_generation
                    else ()
                )
                usage = tuple(
                    step["metadata"]["modelUsage"]
                    for step in steps
                    if step.get("metadata", {}).get("modelUsage")
                )
                emit("completed", cascade)
                return Result(
                    output,
                    raw_output,
                    trajectory,
                    cascade,
                    model,
                    card["model"],
                    session.engine.installation.version,
                    round((time.monotonic() - started) * 1000),
                    usage,
                    images,
                    output_mode="planner_text" if plain_text else "native_finish",
                )


def _local_references(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if (
                key in {"$ref", "$dynamicRef", "$recursiveRef"}
                and isinstance(item, str)
                and not item.startswith("#")
            ):
                raise ValueError("Only local fragment schema references are supported")
            _local_references(item)
    elif isinstance(value, list):
        for item in value:
            _local_references(item)


def _collect_images(
    steps: list, directory: Path, expected_model: str | None
) -> tuple[ImageArtifact, ...]:
    image_steps = [step["generateImage"] for step in steps if step.get("generateImage")]
    if not image_steps:
        raise AntigravityError(
            "No native image generation step was returned.", code="image_missing"
        )
    if expected_model and any(
        step.get("modelName") != expected_model for step in image_steps
    ):
        raise AntigravityError(
            "Returned image model differs from expected_model.",
            code="image_model_mismatch",
            details=[step.get("modelName") for step in image_steps],
        )
    artifacts = []
    for step in image_steps:
        media = step.get("generatedMedia") or {}
        data = media.get("inlineData") or step.get("generatedImage", {}).get(
            "base64Data"
        )
        if data:
            try:
                artifacts.append(
                    ImageArtifact(
                        base64.b64decode(data, validate=True),
                        media.get("mimeType") or "application/octet-stream",
                        step.get("modelName"),
                    )
                )
            except (ValueError, binascii.Error) as exc:
                raise AntigravityError(
                    "Invalid generated image encoding.", code="incompatible_format"
                ) from exc
    # Native 2.23.1 often supplies a file URI with empty inlineData. Read only the
    # owned output directory, resolving symlinks before access; never arbitrary URIs.
    if not artifacts:
        for path in sorted(directory.rglob("*")):
            if (
                path.suffix.lower() in _IMAGE_TYPES
                and path.resolve().is_relative_to(directory.resolve())
                and path.is_file()
            ):
                artifacts.append(
                    ImageArtifact(
                        path.read_bytes(),
                        mimetypes.guess_type(path.name)[0]
                        or _IMAGE_TYPES[path.suffix.lower()],
                        image_steps[0].get("modelName")
                        if len(image_steps) == 1
                        else None,
                    )
                )
    if not artifacts or any(not item.data for item in artifacts):
        raise AntigravityError(
            "Native image step returned no image bytes.", code="image_missing"
        )
    return tuple(artifacts)
