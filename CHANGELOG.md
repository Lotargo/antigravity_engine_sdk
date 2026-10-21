# Changelog

## Python 0.2.0 — 2026-10-21

- Direct native text, object JSON Schema, vision and image tool access.
- Explicit `complete()` for tool-less planner text; strict finish contracts stay
  in `chat()` and `generate()`.
- Native `EditorSession` with independent Supercomplete and Tab-to-Jump,
  snapshot replay, literal frames, partial errors and source-checked preview.
- Interruptible streaming reads, cancellation/recovery, request deadlines and
  concurrent model metadata after engine authorization.
- Shared Python / Go browser testbench, local CodeMirror editor, file upload,
  download, cursor highlighting and per-SDK result comparison.
- GitHub wheel, source distribution and Windows shared testbench bundle.

## Go 0.5.0 — 2026-10-21

- Independent Go client with model catalog, text, schemas, vision, image tool,
  cancellation, progress, usage, lifecycle ownership and raw RPC.
- Explicit `Complete()` alongside strict finish-based generation.
- Independent native Supercomplete/Tab-to-Jump and automatic IDE variants,
  exact-source previews and raw Connect stream evidence.
- Standalone mock/live CLI and browser UI; shared launcher support.
- Windows/amd64 executable bundle with retained dependency notices.

These are the first published GitHub releases. Both versions were validated on
Windows with native Antigravity 2.24.0. The engine itself is not distributed.
No SDK license is selected; third-party notices are retained.
