"""Build Python artifacts and both Windows testbench ZIPs into a new directory."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]


def validate_source_archive(path):
    """MANIFEST rules must never publish local run data or installed dependencies."""
    forbidden = {"reports", "node_modules", ".venv", ".git", "__pycache__", "bin"}
    with tarfile.open(path) as source:
        for member in source.getmembers():
            parts = Path(member.name).parts
            if (
                member.name.startswith("/")
                or ".." in parts
                or forbidden.intersection(parts)
                or member.name.endswith((".exe", ".pyc", "/.env"))
            ):
                raise RuntimeError("Forbidden release source member: " + member.name)


def run(*args, cwd=ROOT, env=None):
    subprocess.run(args, cwd=cwd, env=env, check=True)


def archive(source, target):
    with ZipFile(target, "x", compression=ZIP_DEFLATED) as output:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                output.write(path, path.relative_to(source).as_posix())


def copy(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def go_notices(target, env):
    modules = subprocess.check_output(
        [
            "go",
            "list",
            "-deps",
            "-f",
            "{{if .Module}}{{if not .Module.Main}}{{.Module.Path}}|{{.Module.Version}}|{{.Module.Dir}}{{end}}{{end}}",
            "./testbench",
        ],
        cwd=ROOT / "go",
        text=True,
        env=env,
    )
    for line in sorted(set(modules.splitlines()) - {""}):
        module, version, directory = line.split("|")
        found = False
        for name in ("LICENSE", "PATENTS", "NOTICE"):
            source = Path(directory) / name
            if source.is_file():
                copy(
                    source,
                    target / "go" / (module + "@" + version) / name,
                )
                found |= name == "LICENSE"
        if not found:
            raise RuntimeError("Missing dependency LICENSE: " + module)
    goroot = Path(subprocess.check_output(["go", "env", "GOROOT"], text=True).strip())
    for name in ("LICENSE", "PATENTS"):
        copy(goroot / name, target / "go-runtime" / name)
    copy(ROOT / "go/testbench/web/editor-licenses.txt", target / "editor-licenses.txt")


def checksums(directory):
    lines = []
    for path in sorted(directory.iterdir()):
        if path.is_file():
            lines.append(
                hashlib.sha256(path.read_bytes()).hexdigest() + "  " + path.name
            )
    (directory / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="ascii")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, required=True, help="new output directory"
    )
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    py_version = re.search(
        r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(), re.M
    )[1]
    go_version = re.search(
        r'const Version = "([^"]+)"', (ROOT / "go/types.go").read_text()
    )[1]
    pyout, goout, work = output / "python", output / "go", output / "work"
    for path in (pyout, goout, work):
        path.mkdir()
    run(sys.executable, "-m", "build", "--no-isolation", "--outdir", str(pyout))
    for path in pyout.glob("*.tar.gz"):
        validate_source_archive(path)
    binary = work / "antigravity-testbench.exe"
    env = {**os.environ, "CGO_ENABLED": "0", "GOOS": "windows", "GOARCH": "amd64"}
    run(
        "go",
        "build",
        "-trimpath",
        "-ldflags=-s -w",
        "-o",
        str(binary),
        "./testbench",
        cwd=ROOT / "go",
        env=env,
    )
    notices = work / "third-party"
    go_notices(notices, env)

    standalone = work / "standalone"
    standalone.mkdir()
    copy(binary, standalone / "antigravity-testbench.exe")
    copy(ROOT / "THIRD_PARTY.md", standalone / "THIRD_PARTY.md")
    shutil.copytree(notices, standalone / "third-party")
    (standalone / "README.txt").write_text(
        f"Antigravity Go SDK {go_version} - Windows amd64 testbench\n\n"
        "No Go or Python needed for this standalone executable.\n"
        "Mock checks: antigravity-testbench.exe --case all\n"
        "Live UI: antigravity-testbench.exe --ui --live --listen 127.0.0.1:8765\n"
        "Live requires installed Antigravity and an existing sign-in.\n"
        "Native editor preparation is verified only on native 2.24.0.\n"
        "Reports are created in a fresh reports/ directory.\n"
        "No SDK license is selected. Third-party notices are retained.\n",
        encoding="utf8",
    )
    archive(
        standalone, goout / f"antigravity-testbench-go-{go_version}-windows-amd64.zip"
    )

    shared = work / "shared"
    shared.mkdir()
    for name in ("server.py", "go_backend.py", "README.md"):
        copy(ROOT / "python/testbench" / name, shared / "python/testbench" / name)
    for path in (ROOT / "go/testbench/web").iterdir():
        if path.is_file():
            copy(path, shared / "go/testbench/web" / path.name)
    copy(binary, shared / "go/testbench/bin/antigravity-testbench-shared.exe")
    for path in pyout.glob("*.whl"):
        copy(path, shared / "wheels" / path.name)
    copy(ROOT / "THIRD_PARTY.md", shared / "THIRD_PARTY.md")
    shutil.copytree(notices, shared / "third-party")
    shutil.copytree(ROOT / "docs", shared / "docs")
    for name in ("README.md", "README.ru.md"):
        copy(ROOT / name, shared / name)
    for path in (ROOT / "go").glob("*.md"):
        copy(path, shared / "go" / path.name)
    copy(ROOT / "go/testbench/README.md", shared / "go/testbench/README.md")
    for name in ("README.md", "package.json", "package-lock.json"):
        copy(
            ROOT / "go/testbench/frontend" / name,
            shared / "go/testbench/frontend" / name,
        )
    shutil.copytree(
        ROOT / "examples",
        shared / "examples",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    (shared / "README.txt").write_text(
        f"Antigravity shared testbench - Python {py_version} / Go {go_version}\n\n"
        "From this directory in PowerShell:\n"
        "python -m venv .venv\n"
        f".\\.venv\\Scripts\\python.exe -m pip install .\\wheels\\antigravity_engine_sdk-{py_version}-py3-none-any.whl\n"
        ".\\.venv\\Scripts\\python.exe python/testbench/server.py\n\n"
        "Open http://127.0.0.1:8773/editor or http://127.0.0.1:8773/\n"
        "Python 3.10+ and installed Antigravity with an existing sign-in are required.\n"
        "Python dependencies may be downloaded during pip install.\n"
        "Go and Node.js are not needed; Go backend and browser assets are included.\n"
        "Native editor preparation is verified only on native 2.24.0.\n"
        "Reports contain input data and are stored locally under reports/.\n"
        "No SDK license is selected. Third-party notices are retained.\n",
        encoding="utf8",
    )
    archive(
        shared,
        pyout
        / f"antigravity-testbench-python-{py_version}-go-{go_version}-windows-amd64.zip",
    )
    for directory in (pyout, goout):
        checksums(directory)
        print("Release files:", directory)
        for path in sorted(directory.iterdir()):
            print(" ", path.name, path.stat().st_size)


if __name__ == "__main__":
    main()
