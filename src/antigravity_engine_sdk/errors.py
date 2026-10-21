"""Stable error codes independent of any consuming application's exceptions."""

from __future__ import annotations

from typing import Any


class AntigravityError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "engine_error",
        details: Any = None,
    ):
        super().__init__(message)
        self.code = code
        self.details = details
