"""Best-effort redaction for native diagnostics, never for result payloads."""

import re

ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*(?:bearer\s+)?)(\S+)"),
    re.compile(r"(?i)((?:api[_-]?key|access[_-]?token|password)\s*[:=]\s*)(\S+)"),
    re.compile(r"\b(sk-[A-Za-z0-9_-]{8,})\b"),
)


def sanitize_output(value: str, *, maximum: int = 16_000) -> str:
    text = ANSI_RE.sub("", value).replace("\x00", "")
    for pattern in SECRET_PATTERNS:
        if pattern.groups >= 2:
            text = pattern.sub(r"\1[redacted]", text)
        else:
            text = pattern.sub("[redacted]", text)
    return text.strip()[:maximum]
