"""The export gate battery (ADR-0005 rule 2): each check exercised alone, the
``>=`` boundary pinned, the straddle stated, the unavailable CI stated, and the
override scoped to check 5 only — with hand-built AgreementReports (reusing the
Phase 4 fixture factories) so every κ the gate reads is arithmetic-verified by the
report's own validators first.
"""

from __future__ import annotations

import hashlib

import pytest

from conftest import make_stub_fingerprint
from evalgen.contracts import (
    AgreementAxis,
    AgreementReport,
    AxisAgreement,
    BootstrapCI,
    ClassAgreement,
    DisagreementEntry,
    ExportGateOverride,
    ExportGateVerdict,
    GateCheckName,
    JudgeConfidence,
    JudgeFingerprint,
    KappaStatus,
    KappaValue,
    LabelingReport,
    MatchAccounting,
    OutcomeLabel,
)
from evalgen.export import evaluate_export_gate
from evalgen.export.errors import ExportInputError
from test_contracts_agreement import degenerate_axis_kwargs, report_kwargs

LABELS_SHA = hashlib.sha256(b"test ground-truth bytes").hexdigest()
OVERRIDE = ExportGateOverride(reason="test override — the honest low kappa rides the face")

C, P = OutcomeLabel.CORRECT.value, OutcomeLabel.PARTIALLY_CORRECT.value
OUTCOME_ORDER = tuple(m.value for m in OutcomeLabel)


def make_labeling_report(fingerprint: JudgeFingerprint | None = None) -> LabelingReport:
    """Minimal valid LabelingReport carrying the fingerprint the gate compares."""
    return LabelingReport(
        judge=fingerprint or make_stub_fingerprint(),
        max_labels=100,
        records_in=0,
        labeled=0,
        refused=0,
        failed=0,
        skipped_budget=0,
        skipped_fewshot_collision=0,
    )


def bound_report(min_human_labels: int = 10) -> AgreementReport:
    """Fixture-A report (headline κ=0.516129), bound to ground-truth bytes."""
    kwargs = report_kwargs(n_matched=10, min_human_labels=min_human_labels)
    kwargs["human_labels_sha256"] = LABELS_SHA
    return AgreementReport(**kwargs)


def _entry(rid: str, human: str, judge: str) -> DisagreementEntry:
    return DisagreementEntry(
        record_id=rid,
        human_label=human,
        judge_label=judge,
        judge_confidence=JudgeConfidence.MEDIUM,
        judge_rationale="stub rationale — gate battery",
    )


def _two_class_outcome_axis(agree: int, disagree: int, ci95: BootstrapCI | None) -> AxisAgreement:
    """Symmetric two-class outcome axis: κ = (agree − disagree) / (agree + disagree).

    Confusion ((a,d,0,0),(d,a,0,0)): n = 2(a+d), p_o = a/(a+d), p_e = 0.5 —
    hand-checkable in one line, and κ lands exactly on the value the test needs
    (per-class C and P binarize to the SAME 2x2, so their κ equals the global's).
    """
    confusion = (
        (agree, disagree, 0, 0),
        (disagree, agree, 0, 0),
        (0, 0, 0, 0),
        (0, 0, 0, 0),
    )
    kappa = (agree - disagree) / (agree + disagree)
    p_o = agree / (agree + disagree)
    per_class_value = KappaValue(status=KappaStatus.OK, po=p_o, pe=0.5, kappa=round(kappa, 6))
    disagreements = tuple(_entry(f"rec-cp{i:04d}", C, P) for i in range(disagree)) + tuple(
        _entry(f"rec-pc{i:04d}", P, C) for i in range(disagree)
    )
    support = agree + disagree
    return AxisAgreement(
        axis=AgreementAxis.OUTCOME,
        class_order=OUTCOME_ORDER,
        confusion=confusion,
        global_kappa=KappaValue(
            status=KappaStatus.OK, po=p_o, pe=0.5, kappa=round(kappa, 6), ci95=ci95
        ),
        per_class=(
            ClassAgreement(
                class_name=C,
                human_support=support,
                judge_support=support,
                both=agree,
                value=per_class_value,
            ),
            ClassAgreement(
                class_name=P,
                human_support=support,
                judge_support=support,
                both=agree,
                value=per_class_value,
            ),
            ClassAgreement(
                class_name=OutcomeLabel.INCORRECT.value,
                human_support=0,
                judge_support=0,
                both=0,
                value=KappaValue(status=KappaStatus.ABSENT),
            ),
            ClassAgreement(
                class_name=OutcomeLabel.UNJUDGEABLE.value,
                human_support=0,
                judge_support=0,
                both=0,
                value=KappaValue(status=KappaStatus.ABSENT),
            ),
        ),
        min_class_support=5,
        disagreements=disagreements,
    )


def kappa_report(agree: int, disagree: int, ci95: BootstrapCI | None = None) -> AgreementReport:
    """A bound, headline-ready report whose outcome κ = (agree−disagree)/(agree+disagree)."""
    n = 2 * (agree + disagree)
    return AgreementReport(
        judge=make_stub_fingerprint(),
        taxonomy_id=make_stub_fingerprint().taxonomy_id,
        human_labels_source="annotations_test.jsonl",
        human_labels_sha256=LABELS_SHA,
        annotators=("synthetic",),
        accounting=MatchAccounting(
            judged_in=n, human_in=n, n_matched=n, judge_only_ids=(), human_only=()
        ),
        min_human_labels=10,
        headline_ready=True,
        min_class_support=5,
        bootstrap_resamples=100,
        seed=1750,
        axes=(
            AxisAgreement(**degenerate_axis_kwargs(AgreementAxis.TASK_TYPE, n)),
            _two_class_outcome_axis(agree, disagree, ci95),
        ),
    )


def _flags(decision) -> tuple[bool, ...]:
    return tuple(check.passed for check in decision.checks)


# --------------------------------------------------------------- per-check failures


class TestEachCheck:
    def test_all_checks_pass_verdict_passed(self) -> None:
        # κ = (33-7)/40 = 0.65 exactly; min 0.6 → clean pass, no override involved.
        report = kappa_report(33, 7)
        decision = evaluate_export_gate(report, make_labeling_report(), min_export_kappa=0.6)
        assert _flags(decision) == (True, True, True, True, True)
        assert decision.verdict is ExportGateVerdict.PASSED
        assert decision.override is None
        assert decision.kappa == 0.65

    def test_headline_not_ready_blocks(self) -> None:
        report = bound_report(min_human_labels=30)  # n=10 < 30
        decision = evaluate_export_gate(report, make_labeling_report(), min_export_kappa=0.6)
        assert decision.checks[0].passed is False
        assert "headline not reportable" in decision.checks[0].detail
        assert decision.checks[1].detail == "no headline (not headline_ready)"
        assert decision.kappa is None
        assert decision.verdict is ExportGateVerdict.BLOCKED

    def test_undefined_single_class_headline_blocks(self) -> None:
        # ADR-0004 Amendment (d): present headline, status != ok → blocks like missing.
        kwargs = report_kwargs(n_matched=30)  # degenerate outcome axis, ready=True
        kwargs["human_labels_sha256"] = LABELS_SHA
        report = AgreementReport(**kwargs)
        decision = evaluate_export_gate(report, make_labeling_report(), min_export_kappa=0.6)
        assert decision.checks[0].passed is True
        assert decision.checks[1].passed is False
        assert "undefined_single_class" in decision.checks[1].detail
        assert "amendment (d)" in decision.checks[1].detail
        assert decision.kappa is None
        assert decision.verdict is ExportGateVerdict.BLOCKED

    def test_fingerprint_drift_blocks_and_names_the_field(self) -> None:
        report = kappa_report(33, 7)
        drifted = JudgeFingerprint(
            **{**dict(make_stub_fingerprint()), "model_id": "stub-model-DRIFTED"}
        )
        decision = evaluate_export_gate(report, make_labeling_report(drifted), min_export_kappa=0.6)
        assert decision.checks[2].passed is False
        assert "first differing field: model_id" in decision.checks[2].detail
        assert decision.verdict is ExportGateVerdict.BLOCKED

    def test_unbound_report_blocks(self) -> None:
        report = AgreementReport(**report_kwargs(n_matched=10, min_human_labels=10))
        decision = evaluate_export_gate(report, make_labeling_report(), min_export_kappa=0.4)
        assert decision.checks[3].passed is False
        assert "unrecorded" in decision.checks[3].detail
        assert decision.verdict is ExportGateVerdict.BLOCKED

    def test_low_kappa_blocks_without_override(self) -> None:
        report = bound_report()  # κ = 0.516129
        decision = evaluate_export_gate(report, make_labeling_report(), min_export_kappa=0.6)
        assert _flags(decision) == (True, True, True, True, False)
        assert decision.checks[4].detail == "kappa=0.516129 < min_export_kappa=0.6"
        assert decision.verdict is ExportGateVerdict.BLOCKED

    def test_low_kappa_with_override_passes_loudly(self) -> None:
        report = bound_report()
        decision = evaluate_export_gate(
            report, make_labeling_report(), min_export_kappa=0.6, override=OVERRIDE
        )
        assert decision.verdict is ExportGateVerdict.PASSED_WITH_OVERRIDE
        assert decision.override == OVERRIDE
        assert decision.kappa == 0.516129  # the honest low kappa rides the decision


# ------------------------------------------------------------- boundary + straddle


class TestBoundaryAndStraddle:
    def test_kappa_exactly_at_threshold_passes(self) -> None:
        # κ = (4-1)/5 = 0.6 exactly; "below this value blocks" → 0.6 passes.
        report = kappa_report(4, 1)
        decision = evaluate_export_gate(report, make_labeling_report(), min_export_kappa=0.6)
        assert decision.kappa == 0.6
        assert decision.checks[4].passed is True
        assert decision.checks[4].detail == "kappa=0.6 >= min_export_kappa=0.6"
        assert decision.verdict is ExportGateVerdict.PASSED

    def test_stored_kappa_one_millionth_below_blocks(self) -> None:
        report = bound_report()  # stored κ = 0.516129 (the printed number)
        passing = evaluate_export_gate(report, make_labeling_report(), min_export_kappa=0.516129)
        blocking = evaluate_export_gate(report, make_labeling_report(), min_export_kappa=0.51613)
        assert passing.checks[4].passed is True
        assert blocking.checks[4].passed is False
        assert blocking.verdict is ExportGateVerdict.BLOCKED

    def test_straddling_ci_passes_stated(self) -> None:
        ci = BootstrapCI(lower=0.55, upper=0.75, b_total=100, b_degenerate=0)
        report = kappa_report(33, 7, ci95=ci)  # κ = 0.65
        decision = evaluate_export_gate(report, make_labeling_report(), min_export_kappa=0.6)
        assert decision.verdict is ExportGateVerdict.PASSED
        assert decision.ci_straddles_threshold is True
        assert (decision.ci_lower, decision.ci_upper) == (0.55, 0.75)

    def test_non_straddling_ci_is_not_stated(self) -> None:
        ci = BootstrapCI(lower=0.62, upper=0.75, b_total=100, b_degenerate=0)
        report = kappa_report(33, 7, ci95=ci)
        decision = evaluate_export_gate(report, make_labeling_report(), min_export_kappa=0.6)
        assert decision.ci_straddles_threshold is False

    def test_unavailable_ci_gates_on_the_point_estimate(self) -> None:
        ci = BootstrapCI(lower=None, upper=None, b_total=100, b_degenerate=100)
        report = kappa_report(33, 7, ci95=ci)
        decision = evaluate_export_gate(report, make_labeling_report(), min_export_kappa=0.6)
        assert decision.verdict is ExportGateVerdict.PASSED
        assert decision.ci_lower is None and decision.ci_upper is None
        assert decision.ci_straddles_threshold is False


# ------------------------------------------------------------------ override scope


class TestOverrideScope:
    def test_override_on_non_overridable_failure_raises_naming_the_check(self) -> None:
        report = bound_report(min_human_labels=30)  # headline_ready fails
        with pytest.raises(ExportInputError, match="'headline_ready'"):
            evaluate_export_gate(
                report, make_labeling_report(), min_export_kappa=0.6, override=OVERRIDE
            )

    def test_override_when_everything_passes_raises(self) -> None:
        report = kappa_report(33, 7)
        with pytest.raises(ExportInputError, match="must override something"):
            evaluate_export_gate(
                report, make_labeling_report(), min_export_kappa=0.6, override=OVERRIDE
            )

    def test_override_scope_is_exactly_the_kappa_check(self) -> None:
        assert list(GateCheckName)[-1] is GateCheckName.KAPPA_THRESHOLD


# ------------------------------------------------------------------- determinism


class TestDeterminism:
    def test_two_evaluations_are_identical(self) -> None:
        report = bound_report()
        first = evaluate_export_gate(
            report, make_labeling_report(), min_export_kappa=0.6, override=OVERRIDE
        )
        second = evaluate_export_gate(
            report, make_labeling_report(), min_export_kappa=0.6, override=OVERRIDE
        )
        assert first == second
        assert first.model_dump_json() == second.model_dump_json()
