# Getting started

## Requirements

Install Antigravity and sign in through its normal native flow at least once.
The SDK uses that existing authorization, starts its own engine and does not
distribute the native executable or require an API key.

- Python SDK: Python 3.10+; `jsonschema` is installed as a dependency.
- Go SDK: Go 1.23+ for building applications; Python is not required.
- Shared Windows testbench ZIP: Python 3.10+; its Go backend and local browser
  assets are included. Go and Node.js are not required to run this ZIP.
- Native editor commands: verified Antigravity **2.24.0**. Windows has live
  acceptance; Linux/macOS operation is not yet verified.

## Download a release

Get files from [GitHub Releases](https://github.com/Lotargo/antigravity_engine_sdk/releases).

| Asset | Use |
| --- | --- |
| `antigravity_engine_sdk-0.2.0-py3-none-any.whl` | Install only the Python SDK |
| `antigravity_engine_sdk-0.2.0.tar.gz` | Python source distribution |
| `antigravity-testbench-python-0.2.0-go-0.5.0-windows-amd64.zip` | Shared UI with both backends |
| `antigravity-testbench-go-0.5.0-windows-amd64.zip` | Standalone Go UI and CLI |
| `SHA256SUMS.txt` | Verify downloaded assets |

Extract the shared ZIP into a new directory. From its root:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install .\wheels\antigravity_engine_sdk-0.2.0-py3-none-any.whl
.\.venv\Scripts\python.exe python/testbench/server.py
```

The first pip command may download Python dependencies. The browser UI itself
uses local assets and does not need a CDN. Open `http://127.0.0.1:8773/editor`
or `/` for chat. The native engine calls the provider only when you submit a
generation request; catalog requests retrieve account model metadata.

To install the SDK alone, use the same `pip install` command with your downloaded
wheel path. No public PyPI package is assumed by these instructions.

## Install from source

```powershell
git clone https://github.com/Lotargo/antigravity_engine_sdk.git
cd antigravity_engine_sdk
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install ".[dev]"
go -C go build -o testbench/bin/antigravity-testbench-shared.exe ./testbench
.\.venv\Scripts\python.exe python/testbench/server.py
```

For Python only, omit the Go build and pass `--python-only`. To select another
compiled Go worker, pass `--go-testbench PATH`. An occupied port can be changed
with `--listen 127.0.0.1:8774`. Reports go into a new directory under `reports/`.

On Unix, use `.venv/bin/python` and omit `.exe` from the Go build output. These
commands describe source setup, not verified native Linux/macOS acceptance.

## Go applications and standalone testbench

```shell
go get github.com/Lotargo/antigravity_engine_sdk/go@v0.5.0
```

The module is in the `go/` subdirectory, so its repository tag is `go/v0.5.0`.
See [Go API examples](../go/README.md) for client use.

The standalone Windows ZIP contains `antigravity-testbench.exe`:

```powershell
# Local protocol checks; no provider or account required.
.\antigravity-testbench.exe --case all

# Real engine and browser UI.
.\antigravity-testbench.exe --ui --live --listen 127.0.0.1:8765
```

Image generation is an explicit SDK/CLI operation; the browser's image
attachment control exercises vision input, not the image output tool.

## Private repository access

While this repository is private, releases, cloning and Go module installation
require an account with repository access. Keep credentials in your normal Git
credential manager rather than in commands, screenshots or configuration files.

For Go, set `GOPRIVATE=github.com/Lotargo/*` in your shell or Go environment and
configure authenticated Git access. For a local checkout, a `replace` entry
avoids a remote module fetch; see the [Go guide](../go/README.md).

## Troubleshooting

| Symptom | Check |
| --- | --- |
| `engine_unavailable` | Confirm Antigravity is installed or set `ANTIGRAVITY_ENGINE_PATH` |
| `authentication_required` | Sign in through Antigravity and retry |
| Go unavailable in the selector | Build the shared worker or use `--go-testbench PATH` |
| `unsupported_editor_version` | The manual editor preparation is guarded for native 2.24.0 |
| Empty/filtered suggestion | This is a valid native outcome; try a different code context |
| `incompatible_format` with `tab_*` in chat | Select plain text / `complete()` instead of finish |
| Reload clears code/results | Editor buffers and comparison cache live in the current page |

Reports include prompts, media and native trajectories. They are excluded from
Git by default and are not included in release bundles.
