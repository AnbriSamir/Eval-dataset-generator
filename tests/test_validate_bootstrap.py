"""The paired bootstrap, decomposed and hand-checked (ADR-0004 rule 5, spec §4 fixture G).

The RNG half is pinned as a golden (shape, dtype, range, determinism, and the FIRST
ROW for the config seed — regenerating that row is a reviewed event, guarding
against numpy Generator drift). The arithmetic half is checked with a HAND-BUILT
index matrix whose interval was computed on paper — no RNG in the arithmetic check.
"""

from __future__ import annotations

import pathlib

import numpy as np
import pytest

import evalgen.validate.bootstrap as bootstrap_module
from evalgen.validate import bootstrap_kappa, draw_index_matrix, percentile_ci

# Fixture G pairs in record-id order: 0=(C,C), 1=(C,C), 2=(P,P), 3=(C,P);
# encoded C=0, P=1 (class_order declaration indices).
G_HUMAN = np.array([0, 0, 1, 0], dtype=np.int64)
G_JUDGE = np.array([0, 0, 1, 1], dtype=np.int64)

#: The hand matrix (B=5, n=4) and its per-row kappas via the SAME kappa_from_confusion:
#:   [0,1,2,3] full set          -> D=3, S=8      -> (12-8)/(16-8)  = 0.5
#:   [0,0,1,1] (C,C)x4           -> S=16=n^2      -> DEGENERATE (excluded, counted)
#:   [2,2,3,3] (P,P)x2,(C,P)x2   -> D=2, S=0+8=8  -> (8-8)/(16-8)   = 0.0
#:   [0,1,2,2] (C,C)x2,(P,P)x2   -> D=4, S=4+4=8  -> (16-8)/(16-8)  = 1.0
#:   [3,3,3,3] (C,P)x4           -> D=0, S=0      -> (0-0)/(16-0)   = 0.0
#: valid kappas sorted: [0.0, 0.0, 0.5, 1.0]; linear percentile h=(4-1)q:
#:   q=0.025 -> h=0.075 -> 0.0;  q=0.975 -> h=2.925 -> 0.5 + 0.925*0.5 = 0.9625.
G_MATRIX = np.array(
    [[0, 1, 2, 3], [0, 0, 1, 1], [2, 2, 3, 3], [0, 1, 2, 2], [3, 3, 3, 3]], dtype=np.int64
)


class TestDrawIndexMatrix:
    def test_shape_dtype_and_range(self) -> None:
        matrix = draw_index_matrix(seed=1750, resamples=10, n=5)
        assert matrix.shape == (10, 5)
        assert matrix.dtype == np.int64
        assert matrix.min() >= 0 and matrix.max() < 5

    def test_two_calls_are_byte_identical(self) -> None:
        a = draw_index_matrix(seed=1750, resamples=10, n=5)
        b = draw_index_matrix(seed=1750, resamples=10, n=5)
        assert a.tobytes() == b.tobytes()

    def test_pinned_first_row_for_the_config_seed(self) -> None:
        # Golden-style regression against numpy Generator drift: PCG64(1750),
        # integers(0, 5, size=(10, 5)). Regenerating this row is a REVIEWED event —
        # a silent change here would silently move every published interval.
        matrix = draw_index_matrix(seed=1750, resamples=10, n=5)
        assert matrix[0].tolist() == [1, 3, 2, 4, 0]

    def test_different_seeds_differ(self) -> None:
        a = draw_index_matrix(seed=1750, resamples=10, n=5)
        b = draw_index_matrix(seed=1751, resamples=10, n=5)
        assert a.tobytes() != b.tobytes()

    def test_invalid_dimensions_refuse(self) -> None:
        with pytest.raises(ValueError, match="resamples"):
            draw_index_matrix(seed=1, resamples=0, n=5)
        with pytest.raises(ValueError, match="n must be"):
            draw_index_matrix(seed=1, resamples=5, n=0)


class TestFixtureG:
    def test_global_ci_verbatim(self) -> None:
        """Point estimate: n=4, D=3, rows C=3,P=1, cols C=2,P=2 -> S=8 -> kappa=0.5.
        Injected hand matrix -> valid kappas [0.5, 0.0, 1.0, 0.0], b_degenerate=1,
        CI95 = [0.0, 0.9625] under linear interpolation (paper-checked above)."""
        ci = bootstrap_kappa(G_HUMAN, G_JUDGE, 2, G_MATRIX)
        assert ci.b_total == 5
        assert ci.b_degenerate == 1
        assert ci.lower == 0.0
        assert ci.upper == 0.9625

    def test_per_class_p_ci_verbatim(self) -> None:
        """Class P (index 1), each resample binarized by hand:
        [0,1,2,3] a=1,b=0,c=1,d=2 -> S=(1)(2)+(3)(2)=8  -> (12-8)/(16-8) = 0.5
        [0,0,1,1] a=0,b=0,c=0,d=4 -> S=16=n^2           -> DEGENERATE
        [2,2,3,3] a=2,b=0,c=2,d=0 -> S=(2)(4)+(2)(0)=8  -> (8-8)/(16-8)  = 0.0
        [0,1,2,2] a=2,b=0,c=0,d=2 -> S=4+4=8            -> (16-8)/(16-8) = 1.0
        [3,3,3,3] a=0,b=0,c=4,d=0 -> S=(0)(4)+(4)(0)=0  -> (0-0)/(16-0)  = 0.0
        Same valid set [0.5, 0.0, 1.0, 0.0] -> same CI [0.0, 0.9625], degenerate=1."""
        ci = bootstrap_kappa(G_HUMAN, G_JUDGE, 2, G_MATRIX, class_index=1)
        assert ci.b_total == 5
        assert ci.b_degenerate == 1
        assert (ci.lower, ci.upper) == (0.0, 0.9625)

    def test_all_degenerate_matrix_yields_no_bounds(self) -> None:
        # Every resample lands on the two (C,C) pairs -> p_e = 1 in every world.
        matrix = np.array([[0, 0, 1, 1], [1, 1, 0, 0]], dtype=np.int64)
        ci = bootstrap_kappa(G_HUMAN, G_JUDGE, 2, matrix)
        assert ci.lower is None and ci.upper is None
        assert ci.b_total == 2
        assert ci.b_degenerate == 2

    def test_percentile_arithmetic_verbatim(self) -> None:
        # The percentile step alone, on the paper values.
        lower, upper = percentile_ci([0.5, 0.0, 1.0, 0.0])
        assert lower == 0.0
        assert upper == pytest.approx(0.9625, abs=1e-12)

    def test_percentile_method_is_pinned_by_name_in_source(self) -> None:
        # Fixture G's 0.9625 only holds under linear interpolation — the value
        # asserts above ARE the behavioral pin; this grep pins the NAME so a numpy
        # default change can never silently reintroduce it.
        source = pathlib.Path(bootstrap_module.__file__).read_text(encoding="utf-8")
        assert 'method="linear"' in source


class TestBootstrapDeterminism:
    def test_same_inputs_twice_identical_ci(self) -> None:
        matrix = draw_index_matrix(seed=1750, resamples=200, n=4)
        a = bootstrap_kappa(G_HUMAN, G_JUDGE, 2, matrix)
        b = bootstrap_kappa(G_HUMAN, G_JUDGE, 2, matrix)
        assert a == b
        assert a.model_dump_json() == b.model_dump_json()

    def test_bounds_stay_inside_kappa_range(self) -> None:
        matrix = draw_index_matrix(seed=1750, resamples=500, n=4)
        ci = bootstrap_kappa(G_HUMAN, G_JUDGE, 2, matrix)
        assert ci.lower is not None and ci.upper is not None
        assert -1.0 <= ci.lower <= ci.upper <= 1.0

    def test_never_touches_global_numpy_state(self) -> None:
        before = np.random.get_state()[1][:10].tolist()  # type: ignore[index]
        draw_index_matrix(seed=1750, resamples=50, n=4)
        bootstrap_kappa(G_HUMAN, G_JUDGE, 2, G_MATRIX)
        after = np.random.get_state()[1][:10].tolist()  # type: ignore[index]
        assert before == after


class TestBootstrapValidation:
    def test_unpaired_index_arrays_refuse(self) -> None:
        with pytest.raises(ValueError, match="equal-length"):
            bootstrap_kappa(G_HUMAN, G_JUDGE[:3], 2, G_MATRIX)

    def test_empty_pair_arrays_refuse_naming_the_real_problem(self) -> None:
        # Red-team N-1 regression: pre-fix, n=0 skipped the range check and the
        # loop died with kappa_from_confusion's "sums to 0" — a misleading error
        # for a caller who passed empty pair arrays. Unreachable through
        # compute_agreement (NoMatchedPairsError fires first); defensive polish.
        empty = np.array([], dtype=np.int64)
        with pytest.raises(ValueError, match="no matched pairs to resample"):
            bootstrap_kappa(empty, empty, 2, np.zeros((5, 0), dtype=np.int64))

    def test_index_matrix_wrong_width_refuses(self) -> None:
        with pytest.raises(ValueError, match="one\\s+resampled index per matched pair"):
            bootstrap_kappa(G_HUMAN, G_JUDGE, 2, np.zeros((5, 3), dtype=np.int64))

    def test_out_of_range_indices_refuse(self) -> None:
        bad = np.full((2, 4), 7, dtype=np.int64)
        with pytest.raises(ValueError, match="index the pairs"):
            bootstrap_kappa(G_HUMAN, G_JUDGE, 2, bad)

    def test_out_of_range_class_index_refuses(self) -> None:
        with pytest.raises(ValueError, match="class_index"):
            bootstrap_kappa(G_HUMAN, G_JUDGE, 2, G_MATRIX, class_index=2)
