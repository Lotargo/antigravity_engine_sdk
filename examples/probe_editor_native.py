"""Opt-in isolated native Supercomplete/Tab-to-Jump probe. Makes real model calls."""

from __future__ import annotations

import argparse
import json
import struct
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from antigravity_engine_sdk import Engine, find_installation


def stream_command(engine, payload, timeout=30):
    raw = json.dumps(payload).encode("utf8")
    request = urllib.request.Request(
        f"http://127.0.0.1:{engine.port}/exa.language_server_pb.LanguageServerService/HandleStreamingCommand",
        data=b"\0" + struct.pack(">I", len(raw)) + raw,
        headers={
            "Content-Type": "application/connect+json",
            "Connect-Protocol-Version": "1",
            "Connect-Timeout-Ms": str(timeout * 1000),
            "X-Codeium-Csrf-Token": engine.csrf,
        },
        method="POST",
    )
    frames = []
    with engine.opener.open(request, timeout=timeout + 2) as response:
        while True:
            header = response.read(5)
            if not header:
                raise ValueError("Stream ended without terminal frame")
            if len(header) != 5:
                raise ValueError("Truncated frame header")
            flags, size = header[0], struct.unpack(">I", header[1:])[0]
            if flags not in (0, 2) or size > 16 * 1024 * 1024:
                raise ValueError("Unsupported or oversized frame")
            body = response.read(size)
            if len(body) != size:
                raise ValueError("Truncated frame body")
            frame = {"flags": flags, "body": json.loads(body)}
            frames.append(frame)
            if flags == 2:
                return frames


def main():
    sys.stdout.reconfigure(encoding="utf8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--scenario", choices=["complete", "rename"], default="complete"
    )
    parser.add_argument(
        "--include-debug",
        action="store_true",
        help="Read native debug trajectories (2.24 can panic inside this RPC when granular state is absent)",
    )
    parser.add_argument(
        "--pause-before-jump",
        type=float,
        default=0,
        help="Idle seconds before the first Tab-to-Jump request",
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    installation = find_installation()
    if not installation:
        raise SystemExit("No Antigravity 2.x installation")
    report = {
        "date": datetime.now().date().isoformat(),
        "engine_version": installation.version,
        "model_selection": "native editor configuration; requestedModelId is not verified actual-model attribution",
        "events": [],
        "runs": [],
    }

    def save():
        (args.output / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8"
        )

    with Engine(installation, timeout=120) as engine:
        catalog = engine.rpc("GetAvailableModels")["response"]["models"]
        root = engine.root / "workspace"
        file = root / "sample.py"
        report["tracked_workspace"] = engine.rpc(
            "AddTrackedWorkspace", {"workspace": root.as_uri()}
        )
        report["workspace_infos"] = engine.rpc("GetWorkspaceInfos")
        text = "def add(a: int, b: int) -> int:\n    return a + b\n\n\ndef multiply(a: int, b: int) -> int:\n    return a * b\n"
        if args.scenario == "rename":
            text = (
                "def calculate_total(prices):\n    tax_rate = 0.1\n    subtotal = sum(prices)\n"
                + "".join(f"    # pricing calculation step {i}\n" for i in range(25))
                + "    tax = subtotal * tax_rate\n    return subtotal + tax\n"
            )

        def document(value):
            cursor = (
                value.index("vat_rate") + len("vat_rate")
                if "vat_rate" in value
                else value.index("return") + 7
                if "return" in value
                else 0
            )
            return {
                "absoluteUri": file.as_uri(),
                "workspaceUri": root.as_uri(),
                "text": value,
                "editorLanguage": "python",
                "cursorOffset": str(len(value[:cursor].encode("utf8"))),
                "lineEnding": "\n",
                "isDirty": True,
                "timestamp": datetime.now(timezone.utc)
                .isoformat()
                .replace("+00:00", "Z"),
            }

        def refresh(value, action):
            payload = {
                "activeDocument": document(value),
                "otherDocuments": [],
                "openDocumentUris": [file.as_uri()],
                "workspaceUris": [root.as_uri()],
                "blocking": True,
                "ideAction": "IDE_ACTION_" + action,
            }
            result = engine.rpc("RefreshContextForIdeAction", payload)
            debug = (
                engine.rpc("GetUserTrajectoryDebug", {"includeAllTrajectories": True})
                if args.include_debug
                else {}
            )
            report["events"].append(
                {"request": payload, "result": result, "debug": debug}
            )
            save()
            print("Refresh", action, "debug keys", list(debug), flush=True)

        file.write_text(text, encoding="utf8")
        refresh(text, "STARTUP")
        text = (
            text.replace("tax_rate", "vat_rate", 1)
            if args.scenario == "rename"
            else text.replace("return a + b", "return ")
        )
        file.write_text(text, encoding="utf8")
        refresh(text, "SELECTION_CHANGED")

        for index, (model, source) in enumerate(
            [("tab_flash_lite_preview", "SUPERCOMPLETE")]
            + [("tab_jump_flash_lite_preview", "TAB_JUMP")] * 2
        ):
            if index == 1 and args.pause_before_jump:
                print("Idle before jump:", args.pause_before_jump, flush=True)
                time.sleep(args.pause_before_jump)
            refresh(text, "SELECTION_CHANGED")
            payload = {
                "metadata": {"sessionId": "sdk-editor-probe"},
                "document": document(text),
                "editorOptions": {"tabSize": "4", "insertSpaces": True},
                "requestedModelId": catalog[model]["model"],
                "requestSource": "COMMAND_REQUEST_SOURCE_" + source,
                "diffType": "DIFF_TYPE_TMP_" + source,
                "supercompleteTriggerCondition": "SUPERCOMPLETE_TRIGGER_CONDITION_TYPING",
                "ignoreSupercompleteDebounce": True,
            }
            run = {"model": model, "request": payload, "usage": "UNKNOWN"}
            started = time.monotonic()
            try:
                run["frames"] = stream_command(engine, payload)
            except Exception as exc:
                run["error"] = str(exc)
            run["duration_ms"] = round((time.monotonic() - started) * 1000)
            report["runs"].append(run)
            time.sleep(2)
            try:
                if args.include_debug:
                    run["debug_after"] = engine.rpc(
                        "GetUserTrajectoryDebug", {"includeAllTrajectories": True}
                    )
            except Exception as exc:
                run["debug_error"] = str(exc)
                report["logs"] = list(engine.logs)
                save()
                print("Native debug failed; see report logs", flush=True)
            save()
            responses = [f["body"] for f in run.get("frames", []) if f["flags"] == 0]
            print(
                json.dumps(
                    {
                        "model": model,
                        "duration_ms": run["duration_ms"],
                        "responses": responses,
                    },
                    ensure_ascii=False,
                )[:6000],
                flush=True,
            )
        report["logs"] = [
            line
            for line in engine.logs
            if any(
                s in line.lower()
                for s in ("granular", "rpcs_command", "context", "error id")
            )
        ]
        save()
    print("Report:", args.output / "report.json")


if __name__ == "__main__":
    main()
