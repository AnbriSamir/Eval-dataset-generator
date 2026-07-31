"""Typed judge failures (ADR-0003 rule 6 / options §3).

The engine catches exactly these three leaves and converts them to counted, id-traceable
report entries. Anything else propagates and crashes the run — our own bugs (an
``AttributeError`` in our code) are never laundered into labeling statistics.
"""

from __future__ import annotations


class JudgeError(Exception):
    """Base of the typed judge-failure hierarchy; carries a bounded ``detail``."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class JudgeRefusalError(JudgeError):
    """The model declined to judge (``stop_reason == "refusal"``)."""


class JudgeParseError(JudgeError):
    """Schema-invalid or truncated output: missing ``parsed_output``, client-side
    constraint validation failure, or ``stop_reason == "max_tokens"``."""


class JudgeAPIError(JudgeError):
    """SDK ``APIStatusError`` / ``APIConnectionError`` after the SDK's own retries."""
