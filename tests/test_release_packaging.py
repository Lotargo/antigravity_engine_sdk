import io
import tarfile

import pytest

from scripts.build_release import validate_source_archive


@pytest.mark.parametrize(
    "member",
    [
        "go/reports/run.json",
        "go/testbench/frontend/node_modules/a.js",
        "go/testbench/bin/native.exe",
    ],
)
def test_source_release_rejects_local_artifacts(tmp_path, member):
    path = tmp_path / "source.tar.gz"
    with tarfile.open(path, "w:gz") as output:
        entry = tarfile.TarInfo("sdk-0.2.0/" + member)
        entry.size = 2
        output.addfile(entry, io.BytesIO(b"{}"))
    with pytest.raises(RuntimeError, match="Forbidden release source member"):
        validate_source_archive(path)


def test_source_release_accepts_source_docs_and_notices(tmp_path):
    path = tmp_path / "source.tar.gz"
    with tarfile.open(path, "w:gz") as output:
        for name in (
            "src/antigravity_engine_sdk/client.py",
            "docs/assets/chat-vision.jpg",
            "go/testbench/web/editor-licenses.txt",
        ):
            entry = tarfile.TarInfo("sdk-0.2.0/" + name)
            entry.size = 2
            output.addfile(entry, io.BytesIO(b"ok"))
    validate_source_archive(path)
