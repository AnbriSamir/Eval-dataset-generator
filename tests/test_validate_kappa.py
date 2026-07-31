"""Confusion building + the sklearn oracle + Landis–Koch band edges (ADR-0004 §8).

The implementation is OURS (typed degeneracy, per-class one-vs-rest, exact integer
arithmetic — sklearn has none of those); ``sklearn.metrics.cohen_kappa_score`` is
the INDEPENDENT test oracle: it must agree with ``kappa_from_confusion`` on every
hand fixture and on seeded randomized label sets, global AND binarized per-class.
"""

from __future__ import annotations

import numpy as np
import pytest
from sklearn.metrics import cohen_kappa_score

from evalgen.contracts import binarize_confusion, kappa_from_confusion
from evalgen.validate import confusion_matrix, landis_koch_band

ORDER = ("correct", "partially_correct", "incorrect", "unjudgeable")

# Fixture A as label sequences (record-id order is irrelevant to the counts).
FIXTURE_A_HUMAN = (
    ["correct"] * 4
    + ["correct"]
    + ["partially_correct"] * 2
    + ["partially_correct"]
    + ["incorrect"]
    + ["incorrect"]
)
FIXTURE_A_JUDGE = (
    ["correct"] * 4
    + ["partially_correct"]
    + ["partially_correct"] * 2
    + ["incorrect"]
    + ["incorrect"]
    + ["correct"]
)


class TestConfusionMatrix:
    def test_fixture_a_hand_counts(self) -> None:
        """(C,C)x4, (C,P)x1, (P,P)x2, (P,I)x1, (I,I)x1, (I,C)x1 — counted by hand."""
        matrix = confusion_matrix(FIXTURE_A_HUMAN, FIXTURE_A_JUDGE, ORDER)
        assert matrix == ((4, 1, 0, 0), (0, 2, 1, 0), (1, 0, 1, 0), (0, 0, 0, 0))

    def test_labels_map_to_indices_in_declared_order(self) -> None:
        matrix = confusion_matrix(["incorrect"], ["correct"], ORDER)
        assert matrix[2][0] == 1  # row = human 'incorrect' (idx 2), col = judge 'correct' (idx 0)

    def test_absent_classes_keep_their_zero_rows(self) -> None:
        matrix = confusion_matrix(["correct"], ["correct"], ORDER)
        assert len(matrix) == 4 and all(len(row) == 4 for row in matrix)

    def test_unpaired_sequences_refuse(self) -> None:
        with pytest.raises(ValueError, match="unpaired"):
            confusion_matrix(["correct"], [], ORDER)

    def test_label_outside_class_order_refuses(self) -> None:
        with pytest.raises(ValueError, match="not in class_order"):
            confusion_matrix(["woof"], ["correct"], ORDER)


class TestSklearnOracle:
    """cohen_kappa_score is an independent implementation of the same statistic —
    every defined fixture value must match it to 1e-12."""

    @pytest.mark.parametrize(
        ("human", "judge", "expected"),
        [
            (FIXTURE_A_HUMAN, FIXTURE_A_JUDGE, 16 / 31),  # fixture A
            (["c"] * 3 + ["p"] * 2 + ["i"], ["c"] * 3 + ["p"] * 2 + ["i"], 1.0),  # fixture B
            (["c", "c", "p", "p"], ["c", "p", "c", "p"], 0.0),  # fixture D
            (["c", "p"], ["p", "c"], -1.0),  # fixture E
            (["c", "c", "c", "u"], ["c", "c", "c", "c"], 0.0),  # fixture F (monoclass judge)
        ],
    )
    def test_hand_fixtures_match_sklearn(self, human, judge, expected) -> None:
        order = sorted(set(human) | set(judge))
        result = kappa_from_confusion(confusion_matrix(human, judge, order))
        assert result is not None
        assert result[2] == pytest.approx(expected, abs=1e-12)
        sk = cohen_kappa_score(human, judge, labels=list(order))
        assert result[2] == pytest.approx(sk, abs=1e-12)

    @pytest.mark.filterwarnings("ignore::RuntimeWarning")
    def test_randomized_label_sets_match_sklearn_global_and_per_class(self) -> None:
        """~20 seeded skewed draws over both axes' class counts: ours vs sklearn,
        global AND per-class (sklearn fed the BINARIZED arrays — an independent
        path to the same 2x2).

        The RuntimeWarning filter is itself evidence: sklearn emits NaN + warning
        on degenerate (both-raters-single-class) draws — the exact failure mode our
        typed ``None``/status vocabulary replaces (ADR-0004 options §4). Those
        draws are asserted as NaN==None pairs, not skipped.
        """
        rng = np.random.default_rng(1750)
        for trial in range(20):
            k = 4 if trial % 2 == 0 else 5  # outcome-sized and task-sized axes
            order = [f"class_{i}" for i in range(k)]
            n = int(rng.integers(30, 60))
            # Skewed marginals — the regime where percent agreement flatters.
            weights = rng.dirichlet(np.full(k, 0.8))
            human = [order[i] for i in rng.choice(k, size=n, p=weights)]
            judge = [order[i] for i in rng.choice(k, size=n, p=weights)]
            matrix = confusion_matrix(human, judge, order)
            result = kappa_from_confusion(matrix)
            sk = cohen_kappa_score(human, judge, labels=order)
            if result is None:
                assert np.isnan(sk)
                continue
            assert result[2] == pytest.approx(sk, abs=1e-12)
            for i, name in enumerate(order):
                collapsed = kappa_from_confusion(binarize_confusion(matrix, i))
                bin_h = [label if label == name else "rest" for label in human]
                bin_j = [label if label == name else "rest" for label in judge]
                sk_bin = cohen_kappa_score(bin_h, bin_j, labels=[name, "rest"])
                if collapsed is None:
                    assert np.isnan(sk_bin)
                    continue
                assert collapsed[2] == pytest.approx(sk_bin, abs=1e-12)

    def test_fixture_a_per_class_matches_sklearn_on_binarized_arrays(self) -> None:
        matrix = confusion_matrix(FIXTURE_A_HUMAN, FIXTURE_A_JUDGE, ORDER)
        for i, name in enumerate(ORDER[:3]):  # C, P, I (U is degenerate: absent)
            collapsed = kappa_from_confusion(binarize_confusion(matrix, i))
            assert collapsed is not None
            bin_h = [x if x == name else "rest" for x in FIXTURE_A_HUMAN]
            bin_j = [x if x == name else "rest" for x in FIXTURE_A_JUDGE]
            sk = cohen_kappa_score(bin_h, bin_j, labels=[name, "rest"])
            assert collapsed[2] == pytest.approx(sk, abs=1e-12)


class TestLandisKochBand:
    @pytest.mark.parametrize(
        ("kappa", "band"),
        [
            (-1.0, "poor"),
            (-0.01, "poor"),
            (0.0, "slight"),
            (0.20, "slight"),
            (0.21, "fair"),
            (0.40, "fair"),
            (0.41, "moderate"),
            (0.60, "moderate"),
            (0.61, "substantial"),
            (0.80, "substantial"),
            (0.81, "almost perfect"),
            (1.0, "almost perfect"),
        ],
    )
    def test_band_edges(self, kappa: float, band: str) -> None:
        assert landis_koch_band(kappa) == band

    def test_out_of_range_refuses(self) -> None:
        with pytest.raises(ValueError, match=r"\[-1, 1\]"):
            landis_koch_band(1.5)
