"""HashingEmbedder: bit-stable across instances/calls, unit rows, exact shapes.

The embedder contract (contracts/embeddings.py) is what makes cosine-as-dot and
euclidean-on-unit-sphere valid downstream — these tests pin it on the real
implementation.
"""

from __future__ import annotations

import numpy as np

from evalgen.cluster import HashingEmbedder

TEXTS = [
    "Quels sont les horaires du péage de Saint-Arnoult ?",
    "Le trafic est-il fluide sur l'A10 ce matin ?",
    "short",
    "unicode : péage — télépéage ␟ été",
]


def test_two_instances_are_bit_identical() -> None:
    a = HashingEmbedder(dim=512).embed(TEXTS)
    b = HashingEmbedder(dim=512).embed(TEXTS)
    assert np.array_equal(a, b)  # bit-equal, not merely allclose


def test_repeated_calls_are_bit_identical() -> None:
    embedder = HashingEmbedder(dim=512)
    assert np.array_equal(embedder.embed(TEXTS), embedder.embed(TEXTS))


def test_rows_are_l2_normalized() -> None:
    matrix = HashingEmbedder(dim=512).embed(TEXTS)
    norms = np.linalg.norm(matrix, axis=1)
    assert np.allclose(norms, 1.0, atol=1e-12)


def test_shape_and_dtype() -> None:
    matrix = HashingEmbedder(dim=128).embed(TEXTS)
    assert matrix.shape == (len(TEXTS), 128)
    assert matrix.dtype == np.float64


def test_empty_input_yields_empty_matrix_with_right_dim() -> None:
    matrix = HashingEmbedder(dim=64).embed([])
    assert matrix.shape == (0, 64)
    assert matrix.dtype == np.float64


def test_same_text_same_vector_across_batches() -> None:
    embedder = HashingEmbedder(dim=256)
    solo = embedder.embed([TEXTS[0]])
    batched = embedder.embed(TEXTS)
    assert np.array_equal(solo[0], batched[0])


def test_fingerprint_names_the_identity() -> None:
    fp = HashingEmbedder(dim=512).fingerprint
    assert fp.name == "hashing"
    assert fp.dim == 512
    assert fp.analyzer == "char_wb"
    assert (fp.ngram_min, fp.ngram_max) == (3, 5)
