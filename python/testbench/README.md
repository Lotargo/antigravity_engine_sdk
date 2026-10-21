# Python backend for the shared testbench

Run from the repository root:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe python/testbench/server.py --listen 127.0.0.1:8773
```

Open `/editor` for Supercomplete and Tab-to-Jump, or `/` for model selection,
chat, image attachments and cancellation. The header identifies **Python SDK**.
Generation is real and uses the current Antigravity account. Loading a file or
initializing an editor does not generate a suggestion.

This server uses Python `Client` and `EditorSession` directly, with separate
owned native processes. Go is not called. It serves the same static UI from
`go/testbench/web` to keep CodeMirror, file upload/download and result handling
identical. The stand requires this checkout; the installed Python SDK itself
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
