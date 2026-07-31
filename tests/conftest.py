"""Shared Phase 2 test plumbing: the stub embedder + a record factory.

``StubEmbedder`` returns pre-baked vectors keyed by EXACT text — near-dup boundary and
chain fixtures are thereby exact (injected dots), not hash-approximate. It satisfies the
``Embedder`` Protocol; tests inject it wherever production code takes an embedder.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

import numpy as np

from evalgen.contracts import EmbedderFingerprint, LogRecord, SourceKind
from evalgen.ingest import build_record


class StubEmbedder:
    """Test-only Embedder: returns pre-baked unit vectors keyed by exact text."""

    def __init__(self, mapping: dict[str, tuple[float, ...]], dim: int) -> None:
        self._mapping = mapping
        self._dim = dim

    @property
    def fingerprint(self) -> EmbedderFingerprint:
        return EmbedderFingerprint(
            name="stub", dim=self._dim, analyzer="stub", ngram_min=1, ngram_max=1
        )

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        if not texts:
            return np.empty((0, self._dim), dtype=np.float64)
        return np.array([self._mapping[text] for text in texts], dtype=np.float64)


def make_record(
    *,
    source_name: str = "stub.jsonl",
    line_no: int = 1,
    input_text: str,
    output_text: str = "ok",
    timestamp: datetime | None = None,
) -> LogRecord:
    """Build a LogRecord through the production constructor (tests never hand-forge)."""
    return build_record(
        source_kind=SourceKind.GENERIC_JSONL,
        source_name=source_name,
        line_no=line_no,
        input_text=input_text,
        output_text=output_text,
        timestamp=timestamp,
    )
