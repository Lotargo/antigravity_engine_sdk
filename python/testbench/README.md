# Shared Python / Go testbench

Run from the repository root:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
go -C go build -o testbench/bin/antigravity-testbench-shared.exe ./testbench
.\.venv\Scripts\python.exe python/testbench/server.py --listen 127.0.0.1:8773
```

Open `/editor` for Supercomplete and Tab-to-Jump, or `/` for model selection,
chat, image attachments and cancellation. Select **Python SDK** or **Go SDK**
at the top of either page. Both use the same browser origin and UI.
Generation is real and uses the current Antigravity account. Loading a file or
initializing an editor does not generate a suggestion.

Python requests use `Client` and `EditorSession` directly. Go requests are
forwarded to an owned Go testbench child on a randomly allocated loopback port,
with its own native SDK clients and reports under `go/` in the run directory.
Opening the stand starts no model inference. The gateway replaces API credentials,
preserves JSON/multipart bodies and routes cancellation/report requests to the
selected backend; it never falls back to another SDK.

The code, cursor, filename, language, prompt, selected model (when present in both
catalogs), response mode and attached image survive an SDK switch within a page.
Editor initialization and last results are separate per SDK. Initialize the
baseline once in each SDK; switch back to compare its last result and latency.
Results are labelled with the SDK/version. Applying a suggestion changes the
shared current code, so compare before applying if both requests should use the
same input. Switching is disabled during setup, metadata refresh and generation;
cancel or finish the active request first. Selection survives navigation between
chat and editor in the same tab. Input buffers and editor result cache live in
the current page; reloading clears them.

`--go-testbench PATH` selects a different built Go backend. On Unix, omit `.exe`
from the build output. `--python-only` disables the child. If the binary is absent,
Python still works and the UI marks Go unavailable. The standalone Go launcher
remains available and serves Go only. The shared launcher closes its owned child
on exit; existing separately launched stands are unaffected.

The server serves the same static UI from
`go/testbench/web` to keep CodeMirror, file upload/download and result handling
identical. The shared stand requires this checkout and a built Go backend to use
both SDKs; the installed Python SDK itself
does not depend on those assets, Go, Flask or Node.js. Rebuilding CodeMirror is
documented in `go/testbench/frontend/README.md`.

The loopback server enforces Host/Origin checks and a per-process API token.
Editor pages issue fresh style nonces while preserving the strict script CSP.
Reports go to a new `reports/python-live-*` directory by default. `--output`
accepts a new directory and refuses an existing one. Reports contain input code,
prompts, media and native trace data; cancellation usage remains UNKNOWN.

CLI editor checks, explicitly opted in:

```powershell
.\.venv\Scripts\python.exe examples/editor.py --live --case supercomplete --idle 65 --output reports/python-idle-new
.\.venv\Scripts\python.exe examples/editor.py --live --case jump --output reports/python-jump-new
```

Independent editor commands are verified on native **2.24.0**. Both use the same
zero-inference bootstrap/snapshot replay as the Go SDK. Native can return an
empty, filtered, incorrect or unnecessary suggestion; review it before applying.
`Client.complete()` is the explicit tool-less planner text contract. `chat()` and
`generate()` keep their strict finish contract with no implicit fallback.
