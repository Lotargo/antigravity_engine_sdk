# Documentation

[English overview](../README.md) · [Русский обзор](../README.ru.md)

| Guide | Contents |
| --- | --- |
| [Getting started](GETTING_STARTED.md) | Requirements, GitHub release downloads, installation and private access |
| [Python API](PYTHON.md) | Text, schemas, vision, image generation, editor, cancellation and raw RPC (Russian) |
| [Go API](../go/README.md) | Independent Go module, examples and native editor API (Russian) |
| [Shared testbench](TESTBENCH.md) | SDK switching, code files, model selection, cancellation and result comparison |
| [Architecture](ARCHITECTURE.md) | Origin, process ownership, protocol layers and cleanup (Russian) |
| [Release packaging](RELEASING.md) | Version tags, release files, checksums and build commands |
| [Third-party notices](../THIRD_PARTY.md) | Bundled UI and compiled dependency notices |

## Validation records

These are dated observations. Native generation and local protocol tests are
recorded separately; a passing mock does not establish provider availability.

| Record | Scope |
| --- | --- |
| [Release artifacts](VALIDATION-RELEASE-2026-10-21.md) | Clean archives, installed wheel, extracted bundles, checksums and metadata smoke |
| [Shared SDK switch](VALIDATION-SDK-SWITCH-2026-10-21.md) | 100 Python tests, Go UI tests, real calls through both backends |
| [Python 0.2.0](VALIDATION-0.2.0-2026-10-21.md) | Native editor, complete(), cancellation, wheel and a real 65-second idle |
| [Python metadata concurrency](VALIDATION-0.1.1-2026-10-21.md) | Catalog access during inference |
| [Initial Python extraction](VALIDATION-2026-10-21.md) | Text, vision, image tool, cancellation and packaging |
| [Go SDK](../go/VALIDATION-2026-10-21.md) | Native SDK and mock protocol acceptance |
| [Go editor](../go/EDITOR-VALIDATION-2026-10-21.md) | Literal diffs, cursor positions and preview |
| [Independent jump](../go/INDEPENDENT-JUMP-2026-10-21.md) | Snapshot replay without preceding Supercomplete |
| [Independent completion](../go/INDEPENDENT-SUPERCOMPLETE-2026-10-21.md) | Manual invocation without a recent edit |

## Screenshots

Captured from native testbench checks on 2026-10-21 using synthetic code and an
owned red image. They contain no account tokens or private project code.

- [Chat, vision and SDK selection](assets/chat-vision.jpg)
- [Shared Supercomplete editor](assets/supercomplete.jpg)
- [Python Tab-to-Jump target](assets/tab-to-jump.jpg)

The jump screenshot is from the Python editor before the shared selector was
added; the other two show the common Python / Go interface.
