# Go SDK 0.5.0

Independent Go client for the installed native Antigravity engine, with a
standalone mock/live testbench and a shared Python / Go UI.

- Model catalog, strict finish text/JSON Schema, vision and native image tool.
- Tool-less `Complete()` for literal planner output.
- Native Supercomplete, Tab-to-Jump, snapshots and exact-source previews.
- Cancellation, request deadlines, progress, usage, raw RPC and owned process cleanup.
- Browser editor with code upload/download, syntax colors, line numbers and
  highlighted jump targets. No Python is required for the standalone Go binary.

## Install and run

The module lives in `go/`; the repository tag is **`go/v0.5.0`**:

```shell
go get github.com/Lotargo/antigravity_engine_sdk/go@v0.5.0
```

Go 1.23+ is required to build applications. For private access, configure normal
Git authentication and `GOPRIVATE=github.com/Lotargo/*`.

The `antigravity-testbench-go-0.5.0-windows-amd64.zip` asset contains the executable,
instructions and dependency notices. Check its SHA-256 using `SHA256SUMS.txt`.

```powershell
# Offline protocol tests; no model calls.
.\antigravity-testbench.exe --case all

# Native browser UI.
.\antigravity-testbench.exe --ui --live --listen 127.0.0.1:8765
```

To switch Python / Go in one interface, download the shared testbench ZIP from
the [Python release](https://github.com/Lotargo/antigravity_engine_sdk/releases/tag/v0.2.0).

## Validation and compatibility

Go tests and vet passed; the release executable was checked with its mock suite.
Native calls and UI switching were verified on Windows / Antigravity **2.24.0**.
Independent editor preparation is guarded for that version. Other native
platforms have not been accepted. A successful transport does not guarantee
suggestion quality, model availability or a nonempty result.

Antigravity itself and account data are not bundled. No SDK license is selected;
third-party notices remain in the ZIP. Repository visibility is unchanged.

[Overview and screenshots](https://github.com/Lotargo/antigravity_engine_sdk/blob/main/README.md) · [Go API](https://github.com/Lotargo/antigravity_engine_sdk/blob/main/go/README.md) · [Shared UI](https://github.com/Lotargo/antigravity_engine_sdk/blob/main/docs/TESTBENCH.md)
