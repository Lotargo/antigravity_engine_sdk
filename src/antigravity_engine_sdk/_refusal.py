"""Recognize a model or provider declining a request on content grounds.

Every host reports it differently: an HTTP error, a ``content_filter`` finish, a refusal
written as prose in place of the JSON answer. Executors check here only after the answer
has already failed its contract, so a valid prompt that mentions a policy is never affected.
They then raise ``CONTENT_REFUSED`` so callers can tell censorship from a broken answer.
"""

from __future__ import annotations

import re

CONTENT_REFUSED = "content_rejected"

# A reply in which the model declines, in the words models use for it.
_PROSE = re.compile(
    r"\bi(?:'m| am)? (?:not able|unable) to (?:help|assist|create|generate|write|produce|provide|fulfil)"
    r"|\bi (?:can't|cannot|can not|won't|will not) (?:help|assist|create|generate|write|produce|provide|"
    r"fulfil|comply|depict|describe|make|do that|do this|continue)"
    r"|\bunable to (?:fulfil|comply)"
    r"|\bi (?:must|have to|need to|'m going to|am going to) decline"
    r"|\b(?:against|violates?|outside) (?:my|the|our) (?:guidelines|usage polic|content polic|polic)"
    r"|\b(?:content|usage|safety) polic(?:y|ies)\b"
    r"|не могу (?:с этим )?(?:помочь|выполнить|создать|написать|сгенерировать|описать|продолжить)"
    r"|вынужден[а]? отказаться"
    r"|(?:нарушает|противоречит) (?:мои|нашим|моим|правила|политик)",
    re.IGNORECASE,
)

# What providers put in an error or a finish when their filter blocked the request.
_PROVIDER = re.compile(
    r"content[_ ]filter|content[_ ]policy|prohibited[_ ]content|blocklist|block_?reason"
    r"|finish_?reason\W+safety|\bsafety (?:filter|system|setting|block)"
    r"|usage polic|flagged as potentially|invalid_prompt|moderation|responsible ai|unsafe content"
    r"|\brecitation\b|\bspii\b",
    re.IGNORECASE,
)


def _normalized(text: str) -> str:
    return text.replace("’", "'").replace("‘", "'")


def is_refusal(reply: object) -> bool:
    """True when a model's own answer, which failed its contract, declines the request."""
    if not isinstance(reply, str) or not reply.strip():
        return False
    return bool(_PROSE.search(_normalized(reply)) or _PROVIDER.search(reply))


def is_provider_block(detail: object) -> bool:
    """True when a provider's error or stop reason says its content filter blocked the request.

    Pass the provider's own error text, never a log that echoes the task: the instructions
    sent to the model may themselves talk about policies.
    """
    return isinstance(detail, str) and bool(_PROVIDER.search(detail))


__all__ = ["CONTENT_REFUSED", "is_provider_block", "is_refusal"]
