"""Threshold calibration: hand-computed sweep arithmetic, the high-tie rule, the
self-refusing report, and the committed fixture's non-trivial bracket around 0.92.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from conftest import StubEmbedder
from evalgen.config import get_settings
from evalgen.contracts import (
    EmbedderFingerprint,
    LabeledPair,
    ThresholdCalibrationReport,
    ThresholdCandidate,
)
from evalgen.dedup import calibrate_threshold, load_labeled_pairs

FIXTURE = Path(__file__).resolve().parent.parent / "data" / "fixtures" / "neardup_pairs.jsonl"

FP = EmbedderFingerprint(name="stub", dim=8, analyzer="stub", ngram_min=1, ngram_max=1)


def pair(pair_id: str, text_a: str, text_b: str, label: str) -> LabeledPair:
    return LabeledPair(pair_id=pair_id, text_a=text_a, text_b=text_b, label=label)


def stub_for(sims: dict[str, float]) -> tuple[list[LabeledPair], StubEmbedder]:
    """Build one pair per (text, similarity): text_a = key, text_b = key + '-b'.

    Vectors: a = (1, 0); b = (sim, sqrt(1 - sim²)) → dot == sim exactly enough for
    hand-chosen values.
    """
    import math

    pairs: list[LabeledPair] = []
    mapping: dict[str, tuple[float, ...]] = {}
    for i, (name, sim) in enumerate(sims.items(), start=1):
        label = "duplicate" if name.startswith("dup") else "distinct"
        a_text, b_text = f"{name} a", f"{name} b"
        pairs.append(pair(f"pair-{i:03d}", a_text, b_text, label))
        mapping[a_text] = (1.0, 0.0)
        mapping[b_text] = (sim, math.sqrt(max(0.0, 1.0 - sim * sim)))
    return pairs, StubEmbedder(mapping, dim=2)


# ------------------------------------------------------- hand-computed sweep


def test_sweep_arithmetic_on_hand_computed_miniset() -> None:
    # sims: dup 1.0, dup 0.8, distinct 0.6, distinct 0.0
    # candidates = midpoints of adjacent distinct values: 0.3, 0.7, 0.9
    #   0.3 → TP2 FP1 FN0 → P=2/3      R=1   F1=0.8
    #   0.7 → TP2 FP0 FN0 → P=1        R=1   F1=1.0
    #   0.9 → TP1 FP0 FN1 → P=1        R=0.5 F1=2/3
    pairs, embedder = stub_for({"dup1": 1.0, "dup2": 0.8, "dist1": 0.6, "dist2": 0.0})
    report = calibrate_threshold(pairs, embedder=embedder)

    assert [c.threshold for c in report.candidates] == [0.3, 0.7, 0.9]
    by_threshold = {c.threshold: c for c in report.candidates}
    assert by_threshold[0.3].precision == 0.666667
    assert by_threshold[0.3].recall == 1.0
    assert by_threshold[0.3].f1 == 0.8
    assert by_threshold[0.7].f1 == 1.0
    assert by_threshold[0.9].precision == 1.0
    assert by_threshold[0.9].recall == 0.5
    assert by_threshold[0.9].f1 == 0.666667
    assert report.chosen_threshold == 0.7
    assert report.pairs_duplicate == 2
    assert report.pairs_distinct == 2


def test_f1_tie_breaks_to_the_highest_threshold() -> None:
    # sims: dup {1.0, 0.8}, distinct {0.9, 0.85, 0.2}
    # candidates 0.5, 0.825, 0.875, 0.95:
    #   0.5   → TP2 FP2 FN0 → P=1/2 R=1   → F1 = 2/3
    #   0.825 → TP1 FP2 FN1 → P=1/3 R=1/2 → F1 = 0.4
    #   0.875 → TP1 FP1 FN1 → P=1/2 R=1/2 → F1 = 0.5
    #   0.95  → TP1 FP0 FN1 → P=1   R=1/2 → F1 = 2/3   ← tie with 0.5; higher wins
    pairs, embedder = stub_for(
        {"dup1": 1.0, "dup2": 0.8, "dist1": 0.9, "dist2": 0.85, "dist3": 0.2}
    )
    report = calibrate_threshold(pairs, embedder=embedder)
    assert [c.threshold for c in report.candidates] == [0.5, 0.825, 0.875, 0.95]
    assert report.candidates[0].f1 == report.candidates[3].f1 == 0.666667
    assert report.chosen_threshold == 0.95


def test_calibration_is_deterministic() -> None:
    pairs, embedder = stub_for({"dup1": 1.0, "dup2": 0.8, "dist1": 0.6, "dist2": 0.0})
    a = calibrate_threshold(pairs, embedder=embedder)
    b = calibrate_threshold(pairs, embedder=embedder)
    assert a.model_dump_json() == b.model_dump_json()


# ------------------------------------------------- degenerate inputs (red-team MINOR-2)


def test_all_identical_similarities_raise_naming_the_actual_problem() -> None:
    # One distinct similarity value -> no midpoints. Before the fix this crashed as
    # `max() arg is an empty sequence`; the error must name the real precondition.
    pairs, embedder = stub_for({"dup1": 0.9, "dup2": 0.9, "dist1": 0.9})
    with pytest.raises(ValueError, match="two distinct similarity values"):
        calibrate_threshold(pairs, embedder=embedder)


def test_similarities_closer_than_report_precision_merge_their_candidates() -> None:
    # Adjacent midpoints 0.2000001 and 0.2000003 both round to 0.2 at 6 decimals —
    # the same decision boundary at report precision. Before the fix the report
    # refused with "candidates must be sorted ... unique" on perfectly valid pairs.
    pairs, embedder = stub_for({"dist1": 0.2, "dist2": 0.2000002, "dist3": 0.2000004, "dup1": 1.0})
    report = calibrate_threshold(pairs, embedder=embedder)
    assert [c.threshold for c in report.candidates] == [0.2, 0.6]
    by_threshold = {c.threshold: c for c in report.candidates}
    # Metrics are computed against the ROUNDED threshold: at 0.2 (inclusive >=) all
    # three distinct sims count as false positives alongside the one true positive.
    assert by_threshold[0.2].precision == 0.25
    assert by_threshold[0.2].recall == 1.0
    assert by_threshold[0.6].f1 == 1.0
    assert report.chosen_threshold == 0.6


# ------------------------------------------------------- self-refusing report


def candidate(threshold: float, f1: float) -> ThresholdCandidate:
    return ThresholdCandidate(threshold=threshold, precision=1.0, recall=1.0, f1=f1)


def test_report_claiming_non_argmax_choice_refuses() -> None:
    with pytest.raises(ValidationError, match="argmax"):
        ThresholdCalibrationReport(
            embedder=FP,
            pairs_duplicate=2,
            pairs_distinct=2,
            candidates=(candidate(0.3, 0.8), candidate(0.7, 1.0)),
            chosen_threshold=0.3,
        )


def test_report_ignoring_the_high_tie_rule_refuses() -> None:
    with pytest.raises(ValidationError, match="argmax"):
        ThresholdCalibrationReport(
            embedder=FP,
            pairs_duplicate=2,
            pairs_distinct=2,
            candidates=(candidate(0.3, 0.9), candidate(0.7, 0.9)),
            chosen_threshold=0.3,  # tie must go to 0.7
        )


def test_unsorted_candidates_refuse() -> None:
    with pytest.raises(ValidationError, match="sorted"):
        ThresholdCalibrationReport(
            embedder=FP,
            pairs_duplicate=1,
            pairs_distinct=1,
            candidates=(candidate(0.7, 1.0), candidate(0.3, 0.8)),
            chosen_threshold=0.7,
        )


def test_labeled_pair_rejects_unknown_label() -> None:
    with pytest.raises(ValidationError):
        LabeledPair(pair_id="pair-001", text_a="a", text_b="b", label="maybe")


# ------------------------------------------------------------ fixture + loader


def test_fixture_loads_with_enough_pairs_on_both_labels() -> None:
    pairs = load_labeled_pairs(FIXTURE)
    duplicates = [p for p in pairs if p.label == "duplicate"]
    distincts = [p for p in pairs if p.label == "distinct"]
    assert len(duplicates) >= 15
    assert len(distincts) >= 15
    assert [p.pair_id for p in pairs] == [f"pair-{i:03d}" for i in range(1, len(pairs) + 1)]


def test_fixture_sweep_brackets_the_default_threshold() -> None:
    # The point of the fixture: candidates exist on BOTH sides of 0.92, so the sweep
    # is non-trivial around the configured default (ADR-0002 rule 4).
    from evalgen.cluster import HashingEmbedder

    embedder = HashingEmbedder(dim=get_settings().hash_embedding_dim)
    report = calibrate_threshold(load_labeled_pairs(FIXTURE), embedder=embedder)
    assert any(c.threshold < 0.92 for c in report.candidates)
    assert any(c.threshold > 0.92 for c in report.candidates)
    assert report.embedder == embedder.fingerprint


def test_loader_raises_with_line_number_on_bad_line(tmp_path: Path) -> None:
    bad = tmp_path / "pairs.jsonl"
    bad.write_text(
        '{"pair_id": "pair-001", "text_a": "a", "text_b": "b", "label": "duplicate"}\n'
        '{"pair_id": "pair-002", "text_a": "", "text_b": "b", "label": "distinct"}\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="pairs.jsonl:2"):
        load_labeled_pairs(bad)
