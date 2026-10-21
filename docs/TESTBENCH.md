# Shared Python / Go testbench

The testbench is an application built on the two SDKs. Python requests use the
Python SDK directly. Go requests go to an owned Go process on a random loopback
port. Both appear in the same browser interface, with no SDK fallback.

[Installation](GETTING_STARTED.md) · [Launcher options](../python/testbench/README.md)

## Chat and images

1. Select Python SDK or Go SDK at the top of `/`.
2. Choose an exact model ID from the fresh account catalog.
3. Select structured `finish` or plain text. Use plain text for `tab_*` previews.
4. Enter a prompt and optionally attach one PNG/JPEG/WebP/GIF, up to 20 MiB.
5. Submit, or press Ctrl+Enter. Cancel stops the selected SDK's active request.

The result identifies the SDK/version, requested model, duration and attachment.
Diagnostics show native usage and allow downloading the JSON report. Usage of
an interrupted request remains UNKNOWN when the engine does not report it.

![Chat and vision](assets/chat-vision.jpg)

## Supercomplete and Tab-to-Jump

1. Open `/editor`, select an SDK and upload a UTF-8 code file up to 4 MiB,
   or load one of the supplied examples.
2. Verify the filename and language, then keep the baseline with
   «Запомнить исходную версию».
3. Place the cursor in the current code. You may edit first, but neither manual
   command requires a just-made edit or a preceding completion.
4. Run Supercomplete for a code edit, or Tab-to-Jump for a cursor destination.
5. Review the native diff. Apply an edit explicitly, or move to the returned
   position; the destination is highlighted. Download saves current code, not an
   unapplied preview, using the original filename.

The source file on disk is never overwritten by loading, previewing or applying
inside the page. Browser download behavior determines where the result is saved.
Ctrl+F searches, Ctrl+Z/Ctrl+Y undo/redo, and Tab leaves the editor normally.

<details>
<summary>Supercomplete preview</summary>

![Shared editor with a native Supercomplete result](assets/supercomplete.jpg)

</details>

<details>
<summary>Tab-to-Jump destination</summary>

![A real native jump to line 29, column 22](assets/tab-to-jump.jpg)

</details>

## Compare SDKs

Input buffers and the cursor stay in place when you switch SDK. Keep a baseline
once in each SDK; sessions and last results are separate. Switching back restores
that SDK's last result, version and request latency. For comparable input, run
both requests before applying either suggestion.

Switching is disabled during setup, model refresh and generation. Finish or
cancel first. The selected SDK persists when navigating between chat and editor
in the same tab; editor buffers and comparison history are page memory and are
cleared by reload. Loading a new example/file clears editor comparison state.

Durations include the work performed by the SDK request and can include native
startup/context preparation. Two runs are not a controlled performance benchmark.
Native suggestions may be empty, filtered, unnecessary or incorrect. The UI
shows them literally; it does not repair the provider's answer.

## Reports and protocol details

Each run gets a new report directory. Python reports are in its root; Go reports
are under `go/`. Editor reports preserve actual snapshots, request kind, literal
Connect data/terminal frames, bootstrap evidence, requested model IDs and the
final status. Editor token usage remains UNKNOWN.

Input cursor offsets are UTF-8 bytes. Native rows/columns are zero-based IDE
positions; the UI displays one-based line/column numbers. Applying a preview
checks the source hash and complete old range to reject stale/mismatched edits.

Manual requests replay actual snapshots in a fresh owned context. The zero-data
bootstrap is verified only for native **2.24.0**. The automatic SDK methods with
`_native` / `Native` suffixes retain the IDE's recent-edit restrictions.
