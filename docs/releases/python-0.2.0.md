# Python SDK 0.2.0

Python client for the installed native Antigravity engine, plus a shared browser
testbench that can switch between Python and Go SDKs.

- Text, object JSON Schema, vision, native image generation and live model catalog.
- `complete()` for literal planner text without tools; strict native finish in
  `chat()` and `generate()`.
- Independent Supercomplete and Tab-to-Jump, source-checked previews and literal
  native frames; no recent edit or preceding completion required for manual use.
- Cancellation, deadlines, progress and recovery after cancellation.
- Shared CodeMirror UI with SDK selection, code upload/download, syntax colors,
  line numbers and visible cursor destinations.

## Downloads

| File | Purpose |
| --- | --- |
| `antigravity_engine_sdk-0.2.0-py3-none-any.whl` | Python package |
| `antigravity_engine_sdk-0.2.0.tar.gz` | Source distribution with guides and examples |
| `antigravity-testbench-python-0.2.0-go-0.5.0-windows-amd64.zip` | Shared UI and ready-built Go worker |
| `SHA256SUMS.txt` | SHA-256 checksums for these three files |

Install the wheel with `python -m pip install <downloaded-wheel-path>`.
For the shared ZIP, follow its `README.txt`: create a virtualenv, install the
included wheel and run `python/testbench/server.py`. The ZIP needs Python 3.10+
and installed Antigravity; it does not need Go or Node.js.

## Validation and compatibility

Validated on Windows / Antigravity **2.24.0**. Local checks: **100 SDK/stand tests
and 4 release packaging tests**, plus the Go suite. Native text/vision/editor,
cancellation and SDK switching were exercised through the actual UI.
Independent editor preparation is guarded for native 2.24.0. Linux/macOS native
operation remains unverified. Raw suggestions may be empty, filtered or incorrect.

The native executable, sign-in data and run reports are not included.
No SDK license is selected; dependency notices are preserved. The release is
distributed through GitHub, not PyPI. Private repository access is unchanged.

[Overview and screenshots](https://github.com/Lotargo/antigravity_engine_sdk/blob/main/README.md) · [Setup](https://github.com/Lotargo/antigravity_engine_sdk/blob/main/docs/GETTING_STARTED.md) · [Python API](https://github.com/Lotargo/antigravity_engine_sdk/blob/main/docs/PYTHON.md)
