"""The embedding seam: a Protocol in contracts so dedup never imports cluster (ADR-0002).

Near-dup detection (``dedup/``) needs embeddings, but the embedder implementation lives
in ``cluster/`` (CLAUDE.md §3 layout) and dedup must not import against the pipeline
flow. The :class:`Embedder` Protocol lives here — imported by both sides — and the
composition layer (demo, later export) instantiates the concrete embedder and injects
it. The Protocol also buys offline testability: near-dup tests inject stub embedders
returning hand-built unit vectors, so threshold-boundary and chain fixtures are exact,
not hash-approximate.

:class:`EmbedderFingerprint` travels in every dedup / clustering / calibration report:
a threshold is only valid for the embedder it was calibrated against (ADR-0002 rule 5),
so every number names the embedder that measured it.

contracts may import numpy — the module-boundary rule bans sibling *evalgen* imports,
not third-party ones.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator


class EmbedderFingerprint(BaseModel):
    """Identity of the embedder a number was measured with.

    The analyzer/ngram choice is part of the embedder's *identity*, not a config knob —
    changing it is a code change that shows up in provenance, not an env var that
    silently moves every threshold (ADR-0002 rule 5).
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    dim: int = Field(ge=2)
    analyzer: str = Field(min_length=1)
    ngram_min: int = Field(ge=1)
    ngram_max: int = Field(ge=1)

    @model_validator(mode="after")
    def _ngram_range_must_be_ordered(self) -> EmbedderFingerprint:
        if self.ngram_max < self.ngram_min:
            raise ValueError(
                f"ngram_max ({self.ngram_max}) < ngram_min ({self.ngram_min}) — "
                "an inverted n-gram range is not an embedder identity"
            )
        return self


class Embedder(Protocol):
    """Contract every embedding backend implements (hashing default, real ones later).

    ``embed(texts)`` returns a ``(len(texts), dim)`` float64 array with L2-normalized
    rows — that contract is what makes cosine-as-dot AND euclidean-on-unit-sphere both
    valid (d² = 2 − 2·cos) — deterministic across calls/instances/runs, zero network.

    (An all-empty text may embed to a zero row — HashingVectorizer with no features
    yields zeros; unreachable in production because ``LogRecord`` texts are
    min_length=1 post-sanitization.)
    """

    @property
    def fingerprint(self) -> EmbedderFingerprint: ...

    def embed(self, texts: Sequence[str]) -> np.ndarray: ...
