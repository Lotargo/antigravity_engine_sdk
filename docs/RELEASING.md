# Release packaging

The repository has independent version tags:

- Python **0.2.0**: `v0.2.0`, matching `pyproject.toml` and `__version__`.
- Go **0.5.0**: `go/v0.5.0`, matching `go.Version`. The prefix is required for
  the nested `go/` module; consumer versions still use `@v0.5.0`.

Both initial release tags point to the same reviewed repository snapshot.
SDK versions are not bumped for documentation or testbench-only changes.

## Build

From the repository root with development/build dependencies installed:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src tests python scripts
go -C go test ./...
go -C go vet ./...
.\.venv\Scripts\python.exe scripts/build_release.py --output dist/release-0.2.0-0.5.0
```

The output directory must be new. The builder produces:

```text
python/
  antigravity_engine_sdk-0.2.0-py3-none-any.whl
  antigravity_engine_sdk-0.2.0.tar.gz
  antigravity-testbench-python-0.2.0-go-0.5.0-windows-amd64.zip
  SHA256SUMS.txt
go/
  antigravity-testbench-go-0.5.0-windows-amd64.zip
  SHA256SUMS.txt
work/  (staging files; do not upload this directory)
```

Python artifacts use the local build environment without build isolation.
Go is built with CGO disabled, Windows/amd64, trimpath and stripped debug symbols.
The SDKs do not embed Antigravity or user credentials. Dependency notices are
copied into both ZIPs; account data, reports, virtualenvs and build caches are
not packaged. Existing running testbench executables are not overwritten.

Before publishing, verify archive contents/checksums, install the wheel and run
the standalone mock checks. Start the shared ZIP from a fresh extraction using
the installed wheel, not the source tree. Catalog checks do not invoke a model.

## Publish

Use a normal fast-forward push of the reviewed commit to `main`, then create
the two version tags at that commit. Publish GitHub Releases with the files from
the corresponding `python/` and `go/` directories and explicit release notes.
Do not replace an existing tag or published asset silently.

Repository description/topics are maintained separately from release notes.
Publication of a GitHub release does not publish to PyPI or change repository
visibility. A private repository requires authorized access to source and assets.
