"""Opt-in live SDK acceptance: catalog, structured output, synthetic vision and cancellation.

python examples/smoke.py --live --model <catalog-id> --output reports/live-01
Add --image to test the native generate_image tool as well.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import threading
import zlib
from pathlib import Path

from antigravity_engine_sdk import AntigravityError, Client, Media, __version__


def quadrant_png() -> bytes:
    def chunk(kind, data):
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    side = 128
    rows = []
    for y in range(side):
        row = bytearray(b"\x00")
        for x in range(side):
            row.extend(
                ((255, 0, 0) if x < side // 2 else (0, 255, 0))
                if y < side // 2
                else ((0, 0, 255) if x < side // 2 else (255, 255, 0))
            )
        rows.append(row)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", side, side, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"".join(rows)))
        + chunk(b"IEND", b"")
    )


def receipt(result):
    return {
        "model": result.model,
        "native_model": result.native_model,
        "engine_version": result.engine_version,
        "latency_ms": result.latency_ms,
        "usage": result.usage,
        "usage_scope": result.usage_scope,
        "output": result.output,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--image", action="store_true")
    parser.add_argument("--expected-image-model", default="gemini-3.1-flash-image")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    report = {"sdk_version": __version__, "checks": {}, "owned_process_stopped": False}
    client = Client(timeout=180)
    process = None
    try:
        with client:
            report["authentication"] = client.authentication()
            models = client.models()
            report["catalog"] = [
                {
                    "id": model.id,
                    "native_id": model.native_id,
                    "supports_images": model.supports_images,
                }
                for model in models
            ]
            process = client._engine.process
            result = client.generate(
                "Return the integer answer to 17 * 23 and a short explanation.",
                model=args.model,
                schema={
                    "type": "object",
                    "properties": {
                        "answer": {"type": "integer"},
                        "explanation": {"type": "string"},
                    },
                    "required": ["answer", "explanation"],
                    "additionalProperties": False,
                },
            )
            report["structured"] = receipt(result)
            report["checks"]["structured"] = result.output["answer"] == 391
            schema = {
                "type": "object",
                "properties": {
                    key: {"type": "string"}
                    for key in ("top_left", "top_right", "bottom_left", "bottom_right")
                },
                "required": ["top_left", "top_right", "bottom_left", "bottom_right"],
                "additionalProperties": False,
            }
            result = client.generate(
                "Identify the color of each quadrant of this image. Use one lowercase English color word per field.",
                model=args.model,
                schema=schema,
                media=[Media(quadrant_png(), "image/png")],
            )
            report["vision"] = receipt(result)
            report["checks"]["vision"] = result.output == {
                "top_left": "red",
                "top_right": "green",
                "bottom_left": "blue",
                "bottom_right": "yellow",
            }
            stop = threading.Event()

            def cancel_after_send(event):
                if event.phase == "working":
                    stop.set()

            try:
                client.chat(
                    "Write a detailed explanation of matrix multiplication.",
                    model=args.model,
                    cancelled=stop.is_set,
                    on_progress=cancel_after_send,
                )
                report["checks"]["cancellation"] = False
            except AntigravityError as error:
                report["cancellation"] = {"code": error.code, "usage": "UNKNOWN"}
                report["checks"]["cancellation"] = error.code == "cancelled"
            result = client.chat("Reply with exactly: sdk-ready", model=args.model)
            report["after_cancellation"] = receipt(result)
            report["checks"]["reuse_after_cancel"] = (
                result.text.strip() == "sdk-ready" and client._engine.process is process
            )
            if args.image:
                result = client.generate_image(
                    "Generate one square studio photograph of a cobalt blue ceramic cup with a small yellow triangle on it, on a white table.",
                    planner=args.model,
                    expected_model=args.expected_image_model,
                )
                report["image"] = receipt(result)
                report["image"]["artifacts"] = []
                for index, artifact in enumerate(result.images):
                    suffix = {
                        "image/jpeg": ".jpg",
                        "image/png": ".png",
                        "image/webp": ".webp",
                    }.get(artifact.mime_type, ".bin")
                    path = artifact.save(args.output / f"generated-{index}{suffix}")
                    report["image"]["artifacts"].append(
                        {
                            "file": path.name,
                            "mime_type": artifact.mime_type,
                            "model": artifact.model,
                            "bytes": len(artifact.data),
                            "sha256": hashlib.sha256(artifact.data).hexdigest(),
                        }
                    )
                report["checks"]["image"] = bool(result.images)
    except Exception as error:
        report["error"] = {
            "type": type(error).__name__,
            "message": str(error),
            "code": getattr(error, "code", None),
        }
    finally:
        client.close()
        report["owned_process_stopped"] = (
            process is not None and process.poll() is not None
        )
        report["passed"] = (
            not report.get("error")
            and report["owned_process_stopped"]
            and len(report["checks"]) >= 4
            and all(report["checks"].values())
        )
        text = json.dumps(report, ensure_ascii=False, indent=2)
        (args.output / "result.json").write_text(text, encoding="utf8")
        print(text)
    return int(not report["passed"])


if __name__ == "__main__":
    raise SystemExit(main())
