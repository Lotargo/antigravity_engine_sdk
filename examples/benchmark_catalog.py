"""Opt-in native SDK catalog acceptance and warm-request timing.

Every catalog ID is retained, including entries the server may have retired.
Image models are exercised through generate_image. Credentials/auth payloads
are never recorded. Use --prompt-file/--schema-file for a consumer's real task.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from collections import Counter
from pathlib import Path

from antigravity_engine_sdk import (
    AntigravityError,
    Client,
    __version__,
    find_installation,
    runtime,
)


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf8")


def safe(value):
    if isinstance(value, dict):
        return {
            key: safe(item)
            for key, item in value.items()
            if key not in {"responseHeader", "messageId", "responseId"}
        }
    if isinstance(value, (list, tuple)):
        return [safe(item) for item in value]
    if isinstance(value, str):
        return value.replace(str(Path.home()), "%USERPROFILE%").replace(
            Path.home().as_posix(), "%USERPROFILE%"
        )
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prompt-file", type=Path)
    parser.add_argument("--schema-file", type=Path)
    parser.add_argument("--marker", default="SDK-MATRIX-391")
    parser.add_argument(
        "--only", action="append", default=[], help="Optional exact SDK catalog IDs"
    )
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--repeat-model", action="append", default=[])
    parser.add_argument("--repeat-count", type=int, default=3)
    parser.add_argument("--retry-contract", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    prompt = (
        args.prompt_file.read_text(encoding="utf8")
        if args.prompt_file
        else f"Calculate 17 * 23. Return the integer answer and marker {args.marker}."
    )
    schema = (
        json.loads(args.schema_file.read_text(encoding="utf8"))
        if args.schema_file
        else {
            "type": "object",
            "properties": {"answer": {"type": "integer"}, "marker": {"type": "string"}},
            "required": ["answer", "marker"],
            "additionalProperties": False,
        }
    )
    installation = find_installation()
    if installation is None:
        parser.error("Antigravity 2.x is not installed")
    report = {
        "sdk_version": __version__,
        "engine_version": installation.version,
        "engine_sha256": hashlib.sha256(
            Path(installation.executable).read_bytes()
        ).hexdigest(),
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "prompt_chars": len(prompt),
        "schema_sha256": hashlib.sha256(
            json.dumps(schema, sort_keys=True).encode()
        ).hexdigest(),
        "timeout_seconds": args.timeout,
        "results": [],
        "timing_scope": "warm synchronous SDK call through validated finish; startup/warmup separate",
        "usage_scope": "reported trajectory model steps; aborted/missing receipts UNKNOWN",
    }
    current = None
    real_engine = runtime.Engine
    launches = []

    class AuditedEngine(real_engine):
        def __enter__(self):
            result = super().__enter__()
            launches.append(self.process)
            return result

        def rpc(self, method, payload=None, **kwargs):
            if current is not None:
                if method == "StartCascade":
                    current["start_model"] = (payload or {}).get("requestedModel")
                elif method == "SendUserCascadeMessage":
                    current["send_model"] = (
                        (payload or {})
                        .get("cascadeConfig", {})
                        .get("plannerConfig", {})
                        .get("requestedModel", {})
                        .get("model")
                    )
            result = super().rpc(method, payload, **kwargs)
            if current is not None and method == "GetCascadeTrajectory":
                current["trajectory"] = result
            return result

    runtime.Engine = AuditedEngine
    client = Client(installation=installation, timeout=args.timeout)

    def persist():
        report["counts"] = dict(Counter(row["status"] for row in report["results"]))
        write_json(output / "matrix.json", safe(report))

    def attempt(model, index, *, image=False, planner=None):
        nonlocal current
        warmup_started = time.monotonic()
        try:
            client.rpc("HasAuthToken", timeout=90, concurrent=True)
        except AntigravityError as error:
            row = {
                "id": model.id,
                "attempt": index,
                "kind": "image" if image else "text",
                "status": "startup_error",
                "error": {"code": error.code, "message": str(error)},
                "warmup_ms": round((time.monotonic() - warmup_started) * 1000),
                "elapsed_ms": None,
            }
            report["results"].append(row)
            persist()
            return row
        current = {
            "id": model.id,
            "attempt": index,
            "kind": "image" if image else "text",
            "expected_native_model": model.native_id,
            "warmup_ms": round((time.monotonic() - warmup_started) * 1000),
        }
        started = time.monotonic()
        try:
            if image:
                result = client.generate_image(
                    "One square studio photograph of a cobalt blue ceramic cup with a yellow triangle on a white table.",
                    planner=planner.id,
                    expected_model=model.id,
                )
            else:
                result = client.generate(prompt, model=model.id, schema=schema)
            current["output"] = result.output
            current["usage"] = result.usage or "UNKNOWN"
            current["sdk_latency_ms"] = result.latency_ms
            current["raw_response_sha256"] = result.raw_response_sha256
            expected = planner.native_id if image else model.native_id
            current["selection_matches"] = (
                current.get("start_model") == current.get("send_model") == expected
            )
            used = sorted(
                {step.get("model") for step in result.usage if step.get("model")}
            )
            current["observed_native_models"] = used
            current["usage_model_matches"] = used == [expected] if used else None
            current["marker_retained"] = image or args.marker in result.raw_output
            current["status"] = (
                "pass"
                if current["selection_matches"]
                and current["usage_model_matches"]
                and current["marker_retained"]
                else "unverified"
            )
            if image:
                current["planner"] = planner.id
                current["artifacts"] = []
                for position, artifact in enumerate(result.images):
                    extension = {
                        "image/jpeg": ".jpg",
                        "image/png": ".png",
                        "image/webp": ".webp",
                    }.get(artifact.mime_type, ".bin")
                    path = artifact.save(
                        output / f"image-{index}-{position}{extension}"
                    )
                    current["artifacts"].append(
                        {
                            "file": path.name,
                            "bytes": len(artifact.data),
                            "mime_type": artifact.mime_type,
                            "model": artifact.model,
                            "sha256": hashlib.sha256(artifact.data).hexdigest(),
                        }
                    )
        except AntigravityError as error:
            current["status"] = "failed"
            current["error"] = {
                "code": error.code,
                "message": str(error),
                "details": error.details,
            }
            current["usage"] = "UNKNOWN"
        except Exception as error:
            current["status"] = "probe_error"
            current["error"] = {"type": type(error).__name__, "message": str(error)}
        finally:
            current["elapsed_ms"] = round((time.monotonic() - started) * 1000)
            trajectory = current.pop("trajectory", {})
            if current.get("usage") == "UNKNOWN":
                reported = [
                    step["metadata"]["modelUsage"]
                    for step in trajectory.get("trajectory", {}).get("steps", [])
                    if step.get("metadata", {}).get("modelUsage")
                ]
                if reported:
                    current["usage"] = reported
            filename = re.sub(r"[^a-zA-Z0-9._-]", "_", model.id) + f"-attempt-{index}"
            write_json(output / f"{filename}.trajectory.json", safe(trajectory))
            row = current
            current = None
            report["results"].append(row)
            persist()
            print(
                json.dumps(
                    {
                        key: row.get(key)
                        for key in ("id", "attempt", "status", "elapsed_ms")
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
        return row

    try:
        started = time.monotonic()
        models = client.models()
        report["cold_catalog_ms"] = round((time.monotonic() - started) * 1000)
        report["catalog"] = [
            {
                "id": model.id,
                "native_id": model.native_id,
                "name": model.name,
                "supports_images": model.supports_images,
            }
            for model in models
        ]
        persist()
        print(
            json.dumps(
                {
                    "event": "catalog",
                    "models": len(models),
                    "cold_catalog_ms": report["cold_catalog_ms"],
                }
            ),
            flush=True,
        )
        selected = [model for model in models if not args.only or model.id in args.only]
        if args.only and set(args.only) - {model.id for model in selected}:
            raise ValueError("Requested --only model is absent from the native catalog")
        report["tested_ids"] = [model.id for model in selected]
        images = [model for model in selected if "image" in model.id.casefold()]
        texts = [model for model in selected if model not in images]
        planner = next(
            (model for model in models if model.id == "gemini-3.8-flash-low"), models[0]
        )
        for model in texts:
            row = attempt(model, 1)
            if args.retry_contract and row.get("error", {}).get("code") in {
                "incompatible_format",
                "contract_violation",
            }:
                attempt(model, 2)
        for model in images:
            attempt(model, 1, image=True, planner=planner)
        for name in args.repeat_model:
            model = next(item for item in texts if item.id == name)
            existing = max(
                (row["attempt"] for row in report["results"] if row["id"] == name),
                default=0,
            )
            for index in range(existing + 1, args.repeat_count + 1):
                attempt(model, index)
    finally:
        client.close()
        runtime.Engine = real_engine
        report["engine_launches"] = len(launches)
        report["owned_processes_remaining"] = sum(
            process.poll() is None for process in launches
        )
        persist()
    return int(bool(report["owned_processes_remaining"]))


if __name__ == "__main__":
    raise SystemExit(main())
