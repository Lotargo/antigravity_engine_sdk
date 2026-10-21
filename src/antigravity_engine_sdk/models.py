"""Public value objects; native fields and usage remain available without normalization."""

from __future__ import annotations

import base64
import hashlib
import mimetypes
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import AntigravityError

THINKING_LEVELS = {"minimal": 4, "low": 1, "medium": 2, "high": 3}
MAX_MEDIA_BYTES = 20 * 1024 * 1024


@dataclass(frozen=True)
class Media:
    data: bytes
    mime_type: str

    @classmethod
    def from_file(cls, path: str | Path, *, mime_type: str | None = None) -> Media:
        source = Path(path).expanduser()
        if source.stat().st_size > MAX_MEDIA_BYTES:
            raise AntigravityError("Media exceeds 20 MiB.", code="invalid_input")
        return cls(
            source.read_bytes(),
            mime_type
            or mimetypes.guess_type(source.name)[0]
            or "application/octet-stream",
        )

    def native(self) -> dict[str, str]:
        if not self.data or len(self.data) > MAX_MEDIA_BYTES or not self.mime_type:
            raise AntigravityError(
                "Media must contain 1 byte to 20 MiB and a MIME type.",
                code="invalid_input",
            )
        return {
            "mimeType": self.mime_type,
            "inlineData": base64.b64encode(self.data).decode("ascii"),
        }


@dataclass(frozen=True)
class Model:
    id: str
    native_id: str
    name: str
    supports_images: bool
    raw: dict[str, Any] = field(repr=False)


@dataclass(frozen=True)
class ProgressEvent:
    phase: str
    cascade_id: str | None = None
    trajectory: dict[str, Any] | None = field(default=None, repr=False)


@dataclass(frozen=True)
class ImageArtifact:
    data: bytes = field(repr=False)
    mime_type: str
    model: str | None

    def save(self, path: str | Path) -> Path:
        """Save explicitly; never replace an existing file."""
        target = Path(path)
        with target.open("xb") as stream:
            stream.write(self.data)
        return target


@dataclass(frozen=True)
class Result:
    output: Any
    raw_output: str
    trajectory: dict[str, Any] = field(repr=False)
    cascade_id: str
    model: str
    native_model: str
    engine_version: str
    latency_ms: int
    usage: tuple[dict[str, Any], ...]
    images: tuple[ImageArtifact, ...] = ()
    usage_scope: str = "trajectory_model_steps"

    @property
    def raw_response_sha256(self) -> str:
        return hashlib.sha256(self.raw_output.encode("utf8")).hexdigest()

    @property
    def text(self) -> str:
        if not isinstance(self.output, dict) or not isinstance(
            self.output.get("text"), str
        ):
            raise TypeError(
                "This result does not have a text field; use result.output."
            )
        return self.output["text"]
