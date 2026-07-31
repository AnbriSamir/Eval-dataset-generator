"""The paired percentile bootstrap, decomposed so each piece is hand-checkable (ADR-0004 rule 5).

Three deliberate separations:

1. **The randomness is isolated in ``draw_index_matrix``** — one ``numpy.random
   .Generator(PCG64(seed))``, ONE ``integers`` call, a function of (seed, resamples,
   n) ONLY. Adding a metric never moves the draws, the same resampled worlds serve
   every metric (both axes, global + per-class), and NOTHING here touches
   ``np.random`` global state.
2. **The resample loop (``bootstrap_kappa``) is pure arithmetic over a given
   matrix** — tests inject a hand-built index matrix and check the interval computed
   on paper (fixture G), no RNG in the arithmetic check. Every resample's κ flows
   through the SAME ``kappa_from_confusion`` as every published number.
3. **The percentile method is pinned BY NAME** (``method="linear"``) — a numpy
   default change can never silently move a published interval.

Degenerate resamples (p_e = 1 inside a resample) are EXCLUDED from the percentiles
and COUNTED — mapping them to 0 or 1 would inject invented mass; dropping them
uncounted would hide that the data sits near a degenerate boundary, exactly when the
reader must be told. All B degenerate → bounds ``None`` with the count, never
[NaN, NaN].
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from evalgen.contracts import (
    AGREEMENT_DECIMALS,
    BootstrapCI,
    binarize_confusion,
    kappa_from_confusion,
)


def draw_index_matrix(*, seed: int, resamples: int, n: int) -> np.ndarray:
    """The (resamples, n) matrix of pair indices — the run's single source of randomness.

    ``numpy.random.Generator(numpy.random.PCG64(seed))`` with the config seed; one
    ``rng.integers(0, n, size=(resamples, n))`` call; dtype int64. The unit of
    resampling is the (human, judge) PAIR — resampling the raters independently
    would destroy the pairing and fabricate variance (ADR-0004 context, failure
    mode 4). A pinned-first-row test guards against numpy Generator drift.
    """
    if resamples < 1:
        raise ValueError(f"resamples must be >= 1, got {resamples}")
    if n < 1:
        raise ValueError(f"n must be >= 1, got {n}")
    rng = np.random.Generator(np.random.PCG64(seed))
    return rng.integers(0, n, size=(resamples, n), dtype=np.int64)


def percentile_ci(values: Sequence[float]) -> tuple[float, float]:
    """[2.5th, 97.5th] percentiles of ``values`` — linear interpolation, pinned by name.

    ``numpy.percentile(..., method="linear")``: fixture G's hand-computed 0.9625
    only holds under linear interpolation, so the fixture test IS the method pin.
    """
    if len(values) == 0:
        raise ValueError("cannot take percentiles of zero values")
    lower, upper = np.percentile(np.asarray(values, dtype=np.float64), [2.5, 97.5], method="linear")
    return (float(lower), float(upper))


def bootstrap_kappa(
    human_idx: np.ndarray,
    judge_idx: np.ndarray,
    k: int,
    index_matrix: np.ndarray,
    class_index: int | None = None,
) -> BootstrapCI:
    """CI95 for global (``class_index=None``) or one-vs-rest per-class κ.

    Per resample row: gather the resampled PAIRS, rebuild the k×k confusion via a
    ``bincount`` over joint codes (deterministic), optionally binarize for the
    class, and apply the same ``kappa_from_confusion`` as every published number.
    ``None`` results (p_e = 1 in the resample) are counted as degenerate and
    excluded from the percentiles. Bounds are rounded to ``AGREEMENT_DECIMALS`` at
    this model boundary; the percentile arithmetic runs in full float64.
    """
    if human_idx.shape != judge_idx.shape or human_idx.ndim != 1:
        raise ValueError(
            f"human_idx {human_idx.shape} and judge_idx {judge_idx.shape} must be "
            "equal-length 1-d arrays of paired class indices"
        )
    n = human_idx.shape[0]
    if n == 0:
        # Red-team N-1: pre-fix, n = 0 skipped the range check and the loop died
        # with kappa_from_confusion's "sums to 0" — misleading. Name the real problem.
        raise ValueError(
            "human_idx and judge_idx are empty — there are no matched pairs to "
            "resample (nothing to measure)"
        )
    if index_matrix.ndim != 2 or index_matrix.shape[1] != n:
        raise ValueError(
            f"index_matrix has shape {index_matrix.shape}, expected (B, {n}) — one "
            "resampled index per matched pair"
        )
    if index_matrix.min() < 0 or index_matrix.max() >= n:
        raise ValueError(f"index_matrix values must lie in [0, {n}) — they index the pairs")
    if class_index is not None and not 0 <= class_index < k:
        raise ValueError(f"class_index {class_index} out of range for k={k}")

    joint = human_idx * k + judge_idx  # one code per pair; bincount rebuilds the matrix
    b_total = index_matrix.shape[0]
    valid: list[float] = []
    for row in range(b_total):
        resampled = joint[index_matrix[row]]
        confusion = np.bincount(resampled, minlength=k * k).reshape(k, k).tolist()
        matrix: Sequence[Sequence[int]] = confusion
        if class_index is not None:
            matrix = binarize_confusion(confusion, class_index)
        result = kappa_from_confusion(matrix)
        if result is None:
            continue  # degenerate resample: excluded from percentiles, counted below
        valid.append(result[2])

    b_degenerate = b_total - len(valid)
    if not valid:
        return BootstrapCI(lower=None, upper=None, b_total=b_total, b_degenerate=b_degenerate)
    lower, upper = percentile_ci(valid)
    return BootstrapCI(
        lower=round(lower, AGREEMENT_DECIMALS),
        upper=round(upper, AGREEMENT_DECIMALS),
        b_total=b_total,
        b_degenerate=b_degenerate,
    )
