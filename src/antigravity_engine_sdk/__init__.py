"""Native Antigravity engine SDK. Importing the package never starts a process."""

from .client import Client
from .editor import (
    EditorDocument,
    EditorError,
    EditorPosition,
    EditorPreparation,
    EditorResponse,
    EditorResult,
    EditorSession,
    EditorSnapshot,
)
from .errors import AntigravityError
from .models import ImageArtifact, Media, Model, ProgressEvent, Result
from .runtime import Engine, Installation, Session, find_installation

__version__ = "0.2.0"
__all__ = [
    "AntigravityError",
    "Client",
    "Engine",
    "EditorDocument",
    "EditorError",
    "EditorPosition",
    "EditorPreparation",
    "EditorResponse",
    "EditorResult",
    "EditorSession",
    "EditorSnapshot",
    "ImageArtifact",
    "Installation",
    "Media",
    "Model",
    "ProgressEvent",
    "Result",
    "Session",
    "find_installation",
]
