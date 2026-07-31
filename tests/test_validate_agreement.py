"""``compute_agreement`` end-to-end: the join ledger, gates, and determinism
(ADR-0004 rule 3; spec §4 fixture H)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from conftest import make_stub_fingerprint
from evalgen.contracts import (
    TAXONOMY_V1,
    HumanLabel,
    JudgeConfidence,
    JudgeVerdict,
    KappaStatus,
    LabeledExample,
    LabelFailureEntry,
    LabelFailureReason,
    LabelingOutcome,
    LabelingReport,
    UnmatchedHumanCause,
)
from evalgen.validate import compute_agreement, load_human_labels, render_agreement_report
from evalgen.validate.errors import (
    DuplicateHumanLabelError,
    NoMatchedPairsError,
    TaxonomyMismatchError,
)

TAX = TAXONOMY_V1.taxonomy_id


def _verdict(
    task: str = "other",
    outcome: str = "correct",
    confidence: JudgeConfidence = JudgeConfidence.MEDIUM,
    rationale: str = "stub rationale for the drill-down",
) -> JudgeVerdict:
    return JudgeVerdict(task_type=task, outcome=outcome, confidence=confidence, rationale=rationale)


def _outcome(
    judged: dict[str, JudgeVerdict],
    refused: tuple[str, ...] = (),
    collisions: tuple[str, ...] = (),
) -> LabelingOutcome:
    examples = tuple(
        LabeledExample(record_id=rid, taxonomy_id=TAX, model_id="stub-model", verdict=verdict)
        for rid, verdict in sorted(judged.items())
    )
    report = LabelingReport(
        judge=make_stub_fingerprint(),
        max_labels=500,
        records_in=len(judged) + len(refused) + len(collisions),
        labeled=len(judged),
        refused=len(refused),
        failed=0,
        skipped_budget=0,
        skipped_fewshot_collision=len(collisions),
        refusal_entries=tuple(
            LabelFailureEntry(record_id=rid, reason=LabelFailureReason.REFUSAL, detail="declined")
            for rid in sorted(refused)
        ),
        fewshot_collision_record_ids=tuple(sorted(collisions)),
    )
    return LabelingOutcome(labeled_examples=examples, report=report)


def _human(rid: str, task: str = "other", outcome: str = "correct") -> HumanLabel:
    return HumanLabel(
        record_id=rid, taxonomy_id=TAX, task_type=task, outcome=outcome, annotator="synthetic"
    )


def _compute(labeling, humans, **overrides):
    kwargs = {
        "human_labels_source": "annotations_test.jsonl",
        "min_human_labels": 30,
        "min_class_support": 5,
        "bootstrap_resamples": 20,
        "seed": 1750,
    }
    kwargs.update(overrides)
    return compute_agreement(labeling, humans, **kwargs)


class TestFixtureH:
    """Judged {r1..r6}; humans {r4..r9}; r7 refused, r8 few-shot collision, r9 nowhere
    -> matched {r4,r5,r6} (n=3), judge_only {r1,r2,r3}, human_only classified;
    sums 6 = 3+3 on both sides; n=3 < 30 -> headline gated, diagnostics alive."""

    def _report(self):
        labeling = _outcome(
            {f"r{i}": _verdict() for i in range(1, 7)}, refused=("r7",), collisions=("r8",)
        )
        humans = [_human(f"r{i}") for i in range(4, 10)]
        return _compute(labeling, humans)

    def test_join_accounting_verbatim(self) -> None:
        report = self._report()
        accounting = report.accounting
        assert accounting.judged_in == 6
        assert accounting.human_in == 6
        assert accounting.n_matched == 3
        assert accounting.judge_only_ids == ("r1", "r2", "r3")
        assert [(u.record_id, u.cause) for u in accounting.human_only] == [
            ("r7", UnmatchedHumanCause.REFUSED),
            ("r8", UnmatchedHumanCause.FEWSHOT_COLLISION),
            ("r9", UnmatchedHumanCause.NOT_IN_RUN),
        ]

    def test_headline_gated_but_diagnostics_present(self) -> None:
        report = self._report()
        assert report.headline_ready is False
        assert report.headline is None
        assert len(report.axes) == 2  # diagnostics must survive the gate
        for axis in report.axes:
            assert sum(sum(row) for row in axis.confusion) == 3

    def test_rendered_report_states_the_gate_and_the_coverage_loss(self) -> None:
        text = render_agreement_report(self._report())
        assert "NOT REPORTABLE: n=3 < min_human_labels=30" in text
        assert "coverage loss" in text  # the refused human_only line
        assert "fewshot_collision" in text


class TestHeadlineBoundary:
    def _report(self, n: int):
        labeling = _outcome({f"rec-{i:02d}": _verdict() for i in range(30)})
        humans = [_human(f"rec-{i:02d}") for i in range(n)]
        return _compute(labeling, humans)

    def test_n_30_is_headline_ready(self) -> None:
        report = self._report(30)
        assert report.headline_ready is True
        assert report.headline is not None

    def test_n_29_is_not(self) -> None:
        report = self._report(29)
        assert report.headline_ready is False
        assert report.headline is None


# Fixture A as records: (C,C)x4, (C,P), (P,P)x2, (P,I), (I,I), (I,C).
_A_PAIRS = [
    ("correct", "correct"),
    ("correct", "correct"),
    ("correct", "correct"),
    ("correct", "correct"),
    ("correct", "partially_correct"),
    ("partially_correct", "partially_correct"),
    ("partially_correct", "partially_correct"),
    ("partially_correct", "incorrect"),
    ("incorrect", "incorrect"),
    ("incorrect", "correct"),
]


def _fixture_a_inputs():
    judged = {
        f"rec-{i:02d}": _verdict(outcome=judge, rationale=f"rationale for pair {i}")
        for i, (_, judge) in enumerate(_A_PAIRS)
    }
    humans = [_human(f"rec-{i:02d}", outcome=human) for i, (human, _) in enumerate(_A_PAIRS)]
    return _outcome(judged), humans


class TestSupportGateBoundary:
    def test_i_class_suppressed_at_min_class_support_5(self) -> None:
        report = _compute(*_fixture_a_inputs(), min_class_support=5)
        outcome_axis = report.axes[1]
        assert outcome_axis.global_kappa.kappa == 0.516129  # 16/31, hand-checked
        i_row = outcome_axis.per_class[2]
        assert i_row.value.status is KappaStatus.INSUFFICIENT_SUPPORT
        assert i_row.value.kappa is None
        assert (i_row.human_support, i_row.judge_support) == (2, 2)  # supports still shown

    def test_i_class_valued_at_min_class_support_1(self) -> None:
        report = _compute(*_fixture_a_inputs(), min_class_support=1)
        i_row = report.axes[1].per_class[2]
        assert i_row.value.status is KappaStatus.OK
        assert i_row.value.kappa == 0.375  # hand-checked exact value

    def test_degenerate_task_axis_is_typed_not_zero(self) -> None:
        # All 10 pairs agree on task 'other' -> p_e = 1 -> typed undefined status.
        report = _compute(*_fixture_a_inputs())
        task_axis = report.axes[0]
        assert task_axis.global_kappa.status is KappaStatus.UNDEFINED_SINGLE_CLASS
        assert task_axis.global_kappa.kappa is None


class TestTypedRefusals:
    def test_taxonomy_mismatch_refuses(self) -> None:
        labeling = _outcome({"r1": _verdict()})
        alien = HumanLabel(
            record_id="r1",
            taxonomy_id="tax-000000000000",
            task_type="other",
            outcome="correct",
            annotator="synthetic",
        )
        with pytest.raises(TaxonomyMismatchError, match="questionnaires"):
            _compute(labeling, [alien])

    def test_zero_matched_pairs_refuses(self) -> None:
        labeling = _outcome({"r1": _verdict()})
        with pytest.raises(NoMatchedPairsError, match="0 matched pairs"):
            _compute(labeling, [_human("r2")])

    def test_duplicate_human_labels_refuse_even_bypassing_the_loader(self) -> None:
        labeling = _outcome({"r1": _verdict()})
        with pytest.raises(DuplicateHumanLabelError, match="r1"):
            _compute(labeling, [_human("r1"), _human("r1")])


class TestDisagreementDrilldown:
    def test_entries_carry_the_judge_confidence_and_rationale_verbatim(self) -> None:
        judged = {
            "rec-a": _verdict(
                outcome="correct",
                confidence=JudgeConfidence.LOW,
                rationale="the output cites the right toll station",
            ),
            "rec-b": _verdict(outcome="correct"),
        }
        humans = [_human("rec-a", outcome="incorrect"), _human("rec-b", outcome="correct")]
        report = _compute(_outcome(judged), humans)
        (entry,) = report.axes[1].disagreements
        assert entry.record_id == "rec-a"
        assert entry.human_label == "incorrect"
        assert entry.judge_label == "correct"
        assert entry.judge_confidence is JudgeConfidence.LOW
        assert entry.judge_rationale == "the output cites the right toll station"

    def test_entries_sorted_by_human_judge_record(self) -> None:
        report = _compute(*_fixture_a_inputs())
        keys = [(d.human_label, d.judge_label, d.record_id) for d in report.axes[1].disagreements]
        assert keys == sorted(keys)
        assert len(keys) == 3  # (C,P), (P,I), (I,C) — the off-diagonal mass of fixture A


def _write_synthetic_labels(path: Path, indices: list[int]) -> None:
    """Write fixture-A human labels for the given pair indices as a JSONL file.

    Basename stays OUTSIDE the hook-protected ``human_labels*`` namespace and the
    annotator says ``synthetic`` — same discipline as the committed fixture.
    """
    lines = [
        json.dumps(
            {
                "record_id": f"rec-{i:02d}",
                "taxonomy_id": TAX,
                "task_type": "other",
                "outcome": _A_PAIRS[i][0],
                "annotator": "synthetic",
            }
        )
        for i in indices
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class TestLabelFileBinding:
    """Red-team M-1 regression — the §4 selective-filtering payload, replayed via files.

    The red team proved filtering is LEDGER-visible (human_in drops, the dropped
    ids surface in ``judge_only_ids``) but flagged the residual: nothing tied a
    published κ to the exact ground-truth bytes, so "κ=0.51 (n=40)" was
    replay-verifiable only against an unchanged file. Replay with fixture A on
    disk — dropping the 3 disagreeing labels (pairs 4, 7, 9) lifts outcome κ from
    16/31 = 0.516129 (hand-checked, ADR-0004 rule 1) to exactly 1.0:
    n=7, D=7, S = 4² + 2² + 1² = 21 < 49 = n² ⇒ κ = (49−21)/(49−21) = 1.0.
    Post-fix the two runs carry DIFFERENT sha256 bindings on their face, so the
    flattered κ can no longer masquerade as a measurement of the original file.
    """

    def test_selective_filtering_now_leaves_hash_evidence(self, tmp_path: Path) -> None:
        labeling, _ = _fixture_a_inputs()
        full_path = tmp_path / "annotations_full.jsonl"
        filtered_path = tmp_path / "annotations_filtered.jsonl"
        _write_synthetic_labels(full_path, list(range(10)))
        _write_synthetic_labels(filtered_path, [i for i in range(10) if i not in (4, 7, 9)])

        full_sha = hashlib.sha256(full_path.read_bytes()).hexdigest()
        filtered_sha = hashlib.sha256(filtered_path.read_bytes()).hexdigest()
        report_full = _compute(
            labeling,
            load_human_labels(full_path),
            human_labels_source=full_path.name,
            human_labels_sha256=full_sha,
        )
        report_filtered = _compute(
            labeling,
            load_human_labels(filtered_path),
            human_labels_source=filtered_path.name,
            human_labels_sha256=filtered_sha,
        )

        # The red-team lift, reproduced exactly (hand arithmetic in the docstring).
        assert report_full.axes[1].global_kappa.kappa == 0.516129
        assert report_filtered.axes[1].global_kappa.kappa == 1.0
        # The ledger evidence that already existed (red-team §4).
        assert report_filtered.accounting.human_in == 7
        assert report_filtered.accounting.judge_only_ids == ("rec-04", "rec-07", "rec-09")
        # The NEW tamper evidence: different bytes ⇒ different binding, on the face.
        assert full_sha != filtered_sha
        assert report_full.human_labels_sha256 == full_sha
        assert report_filtered.human_labels_sha256 == filtered_sha
        assert f"sha256={full_sha}" in render_agreement_report(report_full)
        assert f"sha256={filtered_sha}" in render_agreement_report(report_filtered)

    def test_an_unbound_report_says_unrecorded_on_its_face(self) -> None:
        # Absence never hides: a report without file provenance prints it.
        report = _compute(*_fixture_a_inputs())
        assert report.human_labels_sha256 is None
        assert "sha256=unrecorded" in render_agreement_report(report)


class TestGateVisibility:
    """Red-team M-2 regression (render half): the gate knobs print in the header
    even when no class happens to be suppressed."""

    def test_the_gates_line_is_always_in_the_header(self) -> None:
        report = _compute(*_fixture_a_inputs())
        assert report.min_class_support == 5
        text = render_agreement_report(report)
        assert "gates       min_human_labels=30  min_class_support=5" in text

    def test_the_gates_line_tracks_the_injected_knobs(self) -> None:
        report = _compute(*_fixture_a_inputs(), min_human_labels=3, min_class_support=1)
        text = render_agreement_report(report)
        assert "gates       min_human_labels=3  min_class_support=1" in text


class TestDeterminism:
    def test_double_run_is_byte_identical(self) -> None:
        labeling, humans = _fixture_a_inputs()
        a = _compute(labeling, humans, bootstrap_resamples=200)
        b = _compute(labeling, humans, bootstrap_resamples=200)
        assert a.model_dump_json() == b.model_dump_json()

    def test_human_label_input_order_does_not_matter(self) -> None:
        labeling, humans = _fixture_a_inputs()
        a = _compute(labeling, humans)
        b = _compute(labeling, list(reversed(humans)))
        assert a.model_dump_json() == b.model_dump_json()
