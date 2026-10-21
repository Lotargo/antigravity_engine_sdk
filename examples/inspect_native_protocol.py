"""Read selected embedded protobuf descriptors from the installed native binary.

Read-only inspection; no engine startup or model requests. No protobuf dependency.
"""

from __future__ import annotations

import argparse
import json
import mmap
import sys
from pathlib import Path

from antigravity_engine_sdk import find_installation


def varint(data, pos):
    value, shift = 0, 0
    while shift <= 63:
        byte = data[pos]
        pos += 1
        value |= (byte & 127) << shift
        if not byte & 128:
            return value, pos
        shift += 7
    raise ValueError("Invalid protobuf varint")


def encoded(value):
    result = bytearray()
    while value > 127:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def fields(data, *, stop_syntax=False):
    result, pos = [], 0
    while pos < len(data):
        tag, pos = varint(data, pos)
        number, wire = tag >> 3, tag & 7
        if not number:
            raise ValueError("Invalid protobuf field")
        if wire == 0:
            value, pos = varint(data, pos)
        elif wire == 2:
            size, pos = varint(data, pos)
            value = data[pos : pos + size]
            pos += size
        elif wire in (1, 5):
            size = 8 if wire == 1 else 4
            value = data[pos : pos + size]
            pos += size
        else:
            raise ValueError("Unsupported protobuf wire type")
        if pos > len(data):
            raise ValueError("Truncated protobuf descriptor")
        result.append((number, value))
        if stop_syntax and number == 12 and value == b"proto3":
            return result
    if stop_syntax:
        raise ValueError("Descriptor syntax terminator not found")
    return result


def first(items, key, default=""):
    value = next((value for number, value in items if number == key), default)
    return value.decode("utf8") if isinstance(value, bytes) else value


def inspect(binary, wanted):
    output = {}
    with (
        Path(binary).open("rb") as stream,
        mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as data,
    ):
        for package in ("language_server", "codeium_common", "diff_action"):
            filename = f"third_party/jetski/{package}_pb/{package}.proto".encode()
            prefix = b"\n" + encoded(len(filename)) + filename + b"\x12"
            offset = data.find(prefix)
            if offset < 0:
                raise ValueError(f"No descriptor for {package}")
            descriptor = fields(data[offset : offset + 4_000_000], stop_syntax=True)
            for kind, raw in descriptor:
                if kind not in (4, 5):
                    continue
                definition = fields(raw)
                name = first(definition, 1)
                if name not in wanted:
                    continue
                if kind == 5:
                    output[name] = {
                        first(fields(v), 1): first(fields(v), 2)
                        for k, v in definition
                        if k == 2
                    }
                else:
                    output[name] = [
                        {
                            label: first(fields(v), key)
                            for label, key in (
                                ("name", 1),
                                ("number", 3),
                                ("label", 4),
                                ("type", 5),
                                ("type_name", 6),
                                ("json_name", 10),
                            )
                        }
                        for k, v in definition
                        if k == 2
                    ]
    return output


def main():
    sys.stdout.reconfigure(encoding="utf8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--types",
        nargs="+",
        default=[
            "HandleStreamingCommandRequest",
            "HandleStreamingCommandResponse",
            "Document",
            "DocumentPosition",
            "EditorOptions",
            "CommandRequestSource",
            "SupercompleteTriggerCondition",
            "DiffType",
            "UnifiedDiff",
            "CharacterDiff",
            "ComboDiff",
            "SuperCompleteFilterReason",
            "CompletionItem",
            "RequestInfo",
        ],
    )
    args = parser.parse_args()
    installation = find_installation()
    if installation is None:
        raise SystemExit("No installed Antigravity 2.x")
    print(
        json.dumps(
            {
                "engine_version": installation.version,
                "types": inspect(installation.executable, set(args.types)),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
