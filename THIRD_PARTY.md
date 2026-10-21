# Third-party notices

No license has been selected for the SDK's own code in this release. This file
records dependencies and retained notices; it does not assign them a new license.

## Browser editor

The generated local CodeMirror/Lezer bundle is accompanied by the complete
[editor dependency notices](go/testbench/web/editor-licenses.txt). These notices
are also embedded in the Go testbench and served at `/editor-licenses.txt`.
They are copied into both Windows release ZIPs. Pinned versions are recorded in
[package-lock.json](go/testbench/frontend/package-lock.json).

## Compiled Go dependencies

The Windows binary includes packages from:

- `github.com/santhosh-tekuri/jsonschema/v6` (MIT)
- `golang.org/x/sys` (BSD-3-Clause)
- `golang.org/x/text` (BSD-3-Clause)

The release builder copies their original LICENSE files from the module cache
into `third-party/go/` in each ZIP. The Go standard library's LICENSE and PATENTS
are copied under `third-party/go-runtime/`.

## Python dependencies and native engine

`jsonschema` and its dependencies are installed as separate Python distributions;
their own metadata and notices apply. Their code is not copied into our wheel.

The native Antigravity engine is discovered on the user's machine and is not
bundled. Its distribution and account authorization remain separate from the SDK.
