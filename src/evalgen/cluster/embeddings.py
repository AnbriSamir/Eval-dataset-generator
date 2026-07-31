"""The deterministic hashing embedder — offline, fit-free, bit-stable (ADR-0002 rule 5).

``HashingVectorizer`` uses a fixed MurmurHash3 seed: same text → same vector, across
instances, runs, and platforms (verified by tests). Character 3-5-grams (``char_wb``)
catch the near-verbatim duplication that actually plagues log corpora — punctuation
variants, one-word edits — honestly stated as *lexical*, not semantic, similarity.
Real embedding backends plug in behind the same ``Embedder`` Protocol later; their
fingerprint travels through every report the same way.

The analyzer/ngram choices are the embedder's IDENTITY (fingerprint), not config knobs:
changing them is a code change that shows up in provenance, never an env var that
silently moves every calibrated threshold. Only ``dim`` comes from config
(``Settings.hash_embedding_dim``).
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
from sklearn.feature_extraction.text import HashingVectorizer

from evalgen.contracts.embeddings import EmbedderFingerprint

_ANALYZER = "char_wb"
_NGRAM_RANGE = (3, 5)


class HashingEmbedder:
    """Deterministic char-n-gram hashing embedder satisfying the ``Embedder`` Protocol."""

    def __init__(self, *, dim: int) -> None:
        self._dim = dim
        self._vectorizer = HashingVectorizer(
            n_features=dim,
            analyzer=_ANALYZER,
            ngram_range=_NGRAM_RANGE,
            norm="l2",
            alternate_sign=True,
            dtype=np.float64,
        )

    @property
    def fingerprint(self) -> EmbedderFingerprint:
        return EmbedderFingerprint(
            name="hashing",
            dim=self._dim,
            analyzer=_ANALYZER,
            ngram_min=_NGRAM_RANGE[0],
            ngram_max=_NGRAM_RANGE[1],
        )

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        """Dense ``(len(texts), dim)`` float64, rows L2-normalized (Protocol contract)."""
        if not texts:
            return np.empty((0, self._dim), dtype=np.float64)
        return np.asarray(self._vectorizer.transform(list(texts)).toarray(), dtype=np.float64)
