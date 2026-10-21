"""Opt-in native Python editor acceptance, including real idle before generation."""

from __future__ import annotations

import argparse
import json
import math
import time
from dataclasses import asdict
from pathlib import Path

from antigravity_engine_sdk import (
    EditorDocument,
    EditorError,
    EditorSession,
    __version__,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument(
        "--case", choices=["supercomplete", "jump", "editor"], default="supercomplete"
    )
    parser.add_argument("--idle", type=float, default=0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not math.isfinite(args.idle) or args.idle < 0:
        parser.error("--idle must be finite and nonnegative")
    args.output.mkdir(parents=True, exist_ok=False)
    before = "def add(a, b):\n    return \n"
    after, cursor = before, len("def add(a, b):\n    return ".encode())
    if args.case != "supercomplete":
        before = "def calculate_total(prices):\n    tax_rate = 0.1\n    subtotal = sum(prices)\n"
        before += "".join(f"    # pricing calculation step {i}\n" for i in range(25))
        before += "    tax = subtotal * tax_rate\n    return subtotal + tax\n"
        after = before.replace("tax_rate", "vat_rate", 1)
        cursor = len(after[: after.index("vat_rate") + len("vat_rate")].encode())
    document = EditorDocument("sample.py", "python", before, cursor)
    report = {
        "sdk_language": "Python",
        "sdk_version": __version__,
        "before": before,
        "usage": "UNKNOWN",
        "model_selection": "requested catalog ID is not actual-model attribution",
    }
    code = 0
    with EditorSession() as editor:
        editor.sync(document)
        document = EditorDocument(document.name, document.language, after, cursor)
        editor.sync(document)
        report["document"] = asdict(document)
        if args.idle:
            print(
                f"Snapshots synced; idle for {args.idle:g}s without edits or model requests.",
                flush=True,
            )
            started = time.monotonic()
            time.sleep(args.idle)
            report["idle_ms"] = round((time.monotonic() - started) * 1000)
        kinds = (
            ["supercomplete", "tab_to_jump"]
            if args.case == "editor"
            else ["tab_to_jump" if args.case == "jump" else "supercomplete"]
        )
        for kind in kinds:
            try:
                result = getattr(editor, kind)()
            except EditorError as error:
                code = 1
                result = error.result
                report[kind + "_error"] = {"code": error.code, "message": str(error)}
            report[kind] = result.to_dict()
            try:
                report[kind + "_preview"] = result.preview(after)
            except Exception as error:
                report[kind + "_preview_error"] = str(error)
            print(
                f"{kind}: {result.status} ({result.latency_ms}ms), frames={len(result.frames)}",
                flush=True,
            )
    path = args.output / "editor-report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8")
    print("Report:", path)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
