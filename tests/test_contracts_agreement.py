"""The κ arithmetic core + the self-validating agreement contracts (ADR-0004 rules 1, 4).

Every fixture here is COMPUTED BY HAND in its docstring — the calculation IS the
spec, and the tests assert the exact value: a wrong κ must fail a test, not ship.
The forged-report battery then proves the other half: a report that lies about its
own κ, supports, statuses, sums, or ordering refuses to exist.
"""

from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from conftest import make_stub_fingerprint
from evalgen.contracts import (
    TAXONOMY_V1,
    AgreementAxis,
    AgreementReport,
    AxisAgreement,
    BootstrapCI,
    ClassAgreement,
    DisagreementEntry,
    HumanLabel,
    JudgeConfidence,
    KappaStatus,
    KappaValue,
    MatchAccounting,
    OutcomeLabel,
    TaskTypeLabel,
    UnmatchedHuman,
    UnmatchedHumanCause,
    binarize_confusion,
    kappa_from_confusion,
)

# ---------------------------------------------------------------- fixture matrices
# Fixture A (ADR-0004 rule 1, outcome axis, order C, P, INC, U):
# pairs (C,C)x4, (C,P)x1, (P,P)x2, (P,INC)x1, (INC,INC)x1, (INC,C)x1 -> n=10.
#         C  P  INC  U   row
#    C    4  1  0  0    5
#    P    0  2  1  0    3
#    INC    1  0  1  0    2
#    U    0  0  0  0    0
#   col   5  3  2  0
FIXTURE_A = ((4, 1, 0, 0), (0, 2, 1, 0), (1, 0, 1, 0), (0, 0, 0, 0))


class TestKappaFromConfusion:
    def test_fixture_a_global(self) -> None:
        """D = 7 -> p_o = 0.7; S = 5*5 + 3*3 + 2*2 + 0 = 38 -> p_e = 0.38;
        kappa = (10*7 - 38) / (100 - 38) = 32/62 = 16/31 = 0.5161290..."""
        result = kappa_from_confusion(FIXTURE_A)
        assert result is not None
        p_o, p_e, kappa = result
        assert p_o == 0.7
        assert p_e == 0.38
        assert kappa == 16 / 31
        assert round(kappa, 6) == 0.516129

    def test_fixture_a_per_class_c(self) -> None:
        """C: a=4, b=5-4=1, c=5-4=1, d=10-6=4 -> p_o = 8/10 = 0.8;
        p_e = (5*5 + 5*5)/100 = 0.5; kappa = (0.8-0.5)/0.5 = 0.6 exactly."""
        collapsed = binarize_confusion(FIXTURE_A, 0)
        assert collapsed == ((4, 1), (1, 4))
        result = kappa_from_confusion(collapsed)
        assert result == (0.8, 0.5, 0.6)

    def test_fixture_a_per_class_p(self) -> None:
        """P: a=2, b=1, c=1, d=6 -> p_o = 0.8; p_e = (3*3 + 7*7)/100 = 0.58;
        kappa = 0.22/0.42 = 11/21 = 0.5238095... -> stored 0.52381 (6 decimals)."""
        collapsed = binarize_confusion(FIXTURE_A, 1)
        assert collapsed == ((2, 1), (1, 6))
        result = kappa_from_confusion(collapsed)
        assert result is not None
        assert result[0] == 0.8
        assert result[1] == 0.58
        assert result[2] == 11 / 21
        assert round(result[2], 6) == 0.52381

    def test_fixture_a_per_class_i(self) -> None:
        """INC: a=1, b=1, c=1, d=7 -> p_o = 0.8; p_e = (2*2 + 8*8)/100 = 0.68;
        kappa = 0.12/0.32 = 0.375 exactly."""
        collapsed = binarize_confusion(FIXTURE_A, 2)
        assert collapsed == ((1, 1), (1, 7))
        result = kappa_from_confusion(collapsed)
        assert result == (0.8, 0.68, 0.375)

    def test_fixture_a_per_class_u_degenerate(self) -> None:
        """U: row + col = 0 -> a=b=c=0, d=10 -> S = 0*0 + 10*10 = 100 = n^2 ->
        p_e = 1 -> undefined (typed None, the per-class 'absent' sub-case)."""
        collapsed = binarize_confusion(FIXTURE_A, 3)
        assert collapsed == ((0, 0), (0, 10))
        assert kappa_from_confusion(collapsed) is None

    def test_fixture_b_perfect_agreement(self) -> None:
        """(C,C)x3, (P,P)x2, (INC,INC)x1 -> n=6, D=6, S = 9+4+1 = 14;
        kappa = (36-14)/(36-14) = 1.0 exactly (perfect agreement, p_e < 1)."""
        result = kappa_from_confusion(((3, 0, 0), (0, 2, 0), (0, 0, 1)))
        assert result is not None
        assert result[2] == 1.0

    def test_fixture_c_pe_one_is_typed_undefined_not_zero(self) -> None:
        """(C,C)x5 -> n=5, D=5, S = 25 = n^2 -> p_e = 1 -> kappa UNDEFINED.
        sklearn emits NaN + warning here; we return a typed None — 0 would claim
        chance-level agreement where agreement-above-chance is unmeasurable."""
        assert kappa_from_confusion(((5,),)) is None
        assert kappa_from_confusion(((5, 0), (0, 0))) is None

    def test_fixture_d_chance_only(self) -> None:
        """(C,C), (C,P), (P,C), (P,P) -> n=4, D=2, S = 2*2 + 2*2 = 8;
        kappa = (8-8)/(16-8) = 0.0 exactly."""
        result = kappa_from_confusion(((1, 1), (1, 1)))
        assert result is not None
        assert result[2] == 0.0

    def test_fixture_e_perfect_disagreement(self) -> None:
        """(C,P), (P,C) -> n=2, D=0, S = 1+1 = 2; kappa = (0-2)/(4-2) = -1.0."""
        result = kappa_from_confusion(((0, 1), (1, 0)))
        assert result is not None
        assert result[2] == -1.0

    def test_fixture_f_monoclass_judge_scores_exactly_zero(self) -> None:
        """Anti-gaming theorem (a): rows C=3, U=1; judge says C always (col C=4).
        n=4, D=3, S = 3*4 + 1*0 = 12; kappa = (12-12)/(16-12) = 0.0 exactly —
        'always say correct' earns zero, never a flattering number."""
        result = kappa_from_confusion(((3, 0), (1, 0)))
        assert result is not None
        assert result[2] == 0.0

    def test_fixture_f_one_side_absent_class_scores_exactly_zero(self) -> None:
        """Anti-gaming theorem (b): class U absent on the judge side only.
        Binarized U: a=0, b=1, c=0, d=3 -> p_o = 0.75; p_e = (1*0 + 3*4)/16 = 0.75;
        kappa = 0.0 exactly (structural zero; supports 1/0 still reported)."""
        collapsed = binarize_confusion(((3, 0), (1, 0)), 1)
        assert collapsed == ((0, 1), (0, 3))
        result = kappa_from_confusion(collapsed)
        assert result == (0.75, 0.75, 0.0)

    def test_empty_matrix_refuses(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            kappa_from_confusion(())

    def test_all_zero_matrix_refuses(self) -> None:
        with pytest.raises(ValueError, match="nothing to measure"):
            kappa_from_confusion(((0, 0), (0, 0)))

    def test_ragged_matrix_refuses(self) -> None:
        with pytest.raises(ValueError, match="ragged"):
            kappa_from_confusion(((1, 2), (3,)))

    def test_negative_entry_refuses(self) -> None:
        with pytest.raises(ValueError, match="negative"):
            kappa_from_confusion(((1, -1), (0, 2)))

    def test_non_integer_entry_refuses(self) -> None:
        with pytest.raises(ValueError, match="not an integer"):
            kappa_from_confusion(((1.5, 0), (0, 1)))  # type: ignore[arg-type]

    def test_binarize_out_of_range_index_refuses(self) -> None:
        with pytest.raises(ValueError, match="out of range"):
            binarize_confusion(FIXTURE_A, 4)


# ------------------------------------------------------------------ model helpers

C, P, INC, U = (m.value for m in OutcomeLabel)
OUTCOME_ORDER = tuple(m.value for m in OutcomeLabel)
TASK_ORDER = tuple(m.value for m in TaskTypeLabel)


def _kv(po: float, pe: float, kappa: float, ci95: BootstrapCI | None = None) -> KappaValue:
    return KappaValue(status=KappaStatus.OK, po=po, pe=pe, kappa=kappa, ci95=ci95)


def _entry(rid: str, human: str, judge: str) -> DisagreementEntry:
    return DisagreementEntry(
        record_id=rid,
        human_label=human,
        judge_label=judge,
        judge_confidence=JudgeConfidence.MEDIUM,
        judge_rationale="stub rationale — forged-report battery",
    )


def axis_a_kwargs(min_class_support: int = 5) -> dict:
    """Valid AxisAgreement kwargs for fixture A (outcome axis, no CIs)."""
    if min_class_support == 5:
        i_value = KappaValue(status=KappaStatus.INSUFFICIENT_SUPPORT)
    else:
        i_value = _kv(0.8, 0.68, 0.375)
    return {
        "axis": AgreementAxis.OUTCOME,
        "class_order": OUTCOME_ORDER,
        "confusion": FIXTURE_A,
        "global_kappa": _kv(0.7, 0.38, 0.516129),
        "per_class": (
            ClassAgreement(
                class_name=C, human_support=5, judge_support=5, both=4, value=_kv(0.8, 0.5, 0.6)
            ),
            ClassAgreement(
                class_name=P,
                human_support=3,
                judge_support=3,
                both=2,
                value=_kv(0.8, 0.58, 0.52381),
            ),
            ClassAgreement(class_name=INC, human_support=2, judge_support=2, both=1, value=i_value),
            ClassAgreement(
                class_name=U,
                human_support=0,
                judge_support=0,
                both=0,
                value=KappaValue(status=KappaStatus.ABSENT),
            ),
        ),
        "min_class_support": min_class_support,
        "disagreements": (
            _entry("rec-a1", C, P),
            _entry("rec-b2", INC, C),
            _entry("rec-c3", P, INC),
        ),
    }


def degenerate_axis_kwargs(axis: AgreementAxis, n: int) -> dict:
    """All pairs agree on the FIRST class: global p_e = 1 -> typed undefined."""
    order = TASK_ORDER if axis is AgreementAxis.TASK_TYPE else OUTCOME_ORDER
    k = len(order)
    confusion = tuple(tuple(n if r == 0 and c == 0 else 0 for c in range(k)) for r in range(k))
    per_class = [
        ClassAgreement(
            class_name=order[0],
            human_support=n,
            judge_support=n,
            both=n,
            value=KappaValue(status=KappaStatus.UNDEFINED_SINGLE_CLASS),
        )
    ]
    per_class.extend(
        ClassAgreement(
            class_name=name,
            human_support=0,
            judge_support=0,
            both=0,
            value=KappaValue(status=KappaStatus.ABSENT),
        )
        for name in order[1:]
    )
    return {
        "axis": axis,
        "class_order": order,
        "confusion": confusion,
        "global_kappa": KappaValue(status=KappaStatus.UNDEFINED_SINGLE_CLASS),
        "per_class": tuple(per_class),
        "min_class_support": 5,
        "disagreements": (),
    }


def report_kwargs(n_matched: int = 10, min_human_labels: int = 30) -> dict:
    """A valid AgreementReport: fixture-A outcome axis + degenerate task axis (n=10)."""
    outcome_axis = (
        AxisAgreement(**axis_a_kwargs())
        if n_matched == 10
        else AxisAgreement(**degenerate_axis_kwargs(AgreementAxis.OUTCOME, n_matched))
    )
    return {
        "judge": make_stub_fingerprint(),
        "taxonomy_id": TAXONOMY_V1.taxonomy_id,
        "human_labels_source": "annotations_test.jsonl",
        "annotators": ("synthetic",),
        "accounting": MatchAccounting(
            judged_in=n_matched,
            human_in=n_matched,
            n_matched=n_matched,
            judge_only_ids=(),
            human_only=(),
        ),
        "min_human_labels": min_human_labels,
        "headline_ready": n_matched >= min_human_labels,
        "min_class_support": 5,
        "bootstrap_resamples": 100,
        "seed": 1750,
        "axes": (
            AxisAgreement(**degenerate_axis_kwargs(AgreementAxis.TASK_TYPE, n_matched)),
            outcome_axis,
        ),
    }


# ------------------------------------------------------- the valid shapes construct


class TestValidConstruction:
    def test_fixture_a_axis_validates(self) -> None:
        axis = AxisAgreement(**axis_a_kwargs())
        assert axis.global_kappa.kappa == 0.516129

    def test_fixture_a_axis_with_support_gate_off_values_i_class(self) -> None:
        axis = AxisAgreement(**axis_a_kwargs(min_class_support=1))
        assert axis.per_class[2].value.kappa == 0.375

    def test_full_report_validates_and_headline_gates(self) -> None:
        report = AgreementReport(**report_kwargs())
        assert report.headline_ready is False
        assert report.headline is None  # n=10 < 30: diagnostics live, headline gated

    def test_headline_is_the_outcome_axis_global_kappa(self) -> None:
        report = AgreementReport(**report_kwargs(n_matched=30))
        assert report.headline_ready is True
        assert report.headline is report.axes[1].global_kappa


# ------------------------------------------------------------ forged-report battery


class TestForgedAxisRefusals:
    def test_kappa_inconsistent_with_own_confusion(self) -> None:
        kwargs = axis_a_kwargs()
        kwargs["global_kappa"] = _kv(0.7, 0.38, 0.6)  # flattered kappa
        with pytest.raises(ValidationError, match="lies about its own kappa"):
            AxisAgreement(**kwargs)

    def test_wrong_po_pe(self) -> None:
        kwargs = axis_a_kwargs()
        kwargs["global_kappa"] = _kv(0.8, 0.38, 0.516129)
        with pytest.raises(ValidationError, match="lies about its own kappa"):
            AxisAgreement(**kwargs)

    def test_ok_status_with_missing_kappa(self) -> None:
        with pytest.raises(ValidationError, match="requires po, pe and kappa"):
            KappaValue(status=KappaStatus.OK, po=0.7, pe=0.38, kappa=None)

    def test_non_ok_status_with_a_kappa_value(self) -> None:
        with pytest.raises(ValidationError, match="must carry no values"):
            KappaValue(status=KappaStatus.INSUFFICIENT_SUPPORT, po=0.8, pe=0.68, kappa=0.375)

    def test_undefined_global_status_on_a_defined_confusion(self) -> None:
        kwargs = axis_a_kwargs()
        kwargs["global_kappa"] = KappaValue(status=KappaStatus.UNDEFINED_SINGLE_CLASS)
        with pytest.raises(ValidationError, match="must be 'ok'"):
            AxisAgreement(**kwargs)

    def test_per_class_absent_with_nonzero_support(self) -> None:
        kwargs = axis_a_kwargs()
        forged = list(kwargs["per_class"])
        forged[2] = ClassAgreement(
            class_name=INC,
            human_support=2,
            judge_support=2,
            both=1,
            value=KappaValue(status=KappaStatus.ABSENT),
        )
        kwargs["per_class"] = tuple(forged)
        with pytest.raises(ValidationError, match="demand 'insufficient_support'"):
            AxisAgreement(**kwargs)

    def test_per_class_insufficient_at_exactly_min_class_support(self) -> None:
        # P has h+j = 6; with min_class_support=6 the gate is h+j < 6, so P is OK —
        # forging 'insufficient_support' AT the boundary must refuse.
        kwargs = axis_a_kwargs()
        kwargs["min_class_support"] = 6
        forged = list(kwargs["per_class"])
        forged[1] = ClassAgreement(
            class_name=P,
            human_support=3,
            judge_support=3,
            both=2,
            value=KappaValue(status=KappaStatus.INSUFFICIENT_SUPPORT),
        )
        forged[2] = ClassAgreement(
            class_name=INC,
            human_support=2,
            judge_support=2,
            both=1,
            value=KappaValue(status=KappaStatus.INSUFFICIENT_SUPPORT),
        )
        kwargs["per_class"] = tuple(forged)
        with pytest.raises(ValidationError, match="demand 'ok'"):
            AxisAgreement(**kwargs)

    def test_per_class_supports_inconsistent_with_marginals(self) -> None:
        kwargs = axis_a_kwargs()
        forged = list(kwargs["per_class"])
        forged[0] = ClassAgreement(
            class_name=C, human_support=6, judge_support=5, both=4, value=_kv(0.8, 0.5, 0.6)
        )
        kwargs["per_class"] = tuple(forged)
        with pytest.raises(ValidationError, match="marginals say"):
            AxisAgreement(**kwargs)

    def test_dropped_per_class_row_refuses(self) -> None:
        kwargs = axis_a_kwargs()
        kwargs["per_class"] = kwargs["per_class"][:3]  # drop the weak U row
        with pytest.raises(ValidationError, match="one row per class, never dropped"):
            AxisAgreement(**kwargs)

    def test_wrong_class_order_refuses(self) -> None:
        kwargs = axis_a_kwargs()
        kwargs["class_order"] = tuple(reversed(OUTCOME_ORDER))
        with pytest.raises(ValidationError, match="enum\\s+declaration order"):
            AxisAgreement(**kwargs)

    def test_disagreement_count_mismatch(self) -> None:
        kwargs = axis_a_kwargs()
        kwargs["disagreements"] = kwargs["disagreements"][:2]
        with pytest.raises(ValidationError, match="off-diagonal"):
            AxisAgreement(**kwargs)

    def test_disagreement_multiplicity_mismatch(self) -> None:
        # Right TOTAL (3) but wrong per-cell multiplicity: (C,P)x2 instead of 1.
        kwargs = axis_a_kwargs()
        kwargs["disagreements"] = (
            _entry("rec-a1", C, P),
            _entry("rec-a2", C, P),
            _entry("rec-b2", INC, C),
        )
        with pytest.raises(ValidationError, match="multiplicity"):
            AxisAgreement(**kwargs)

    def test_unsorted_disagreements_refuse(self) -> None:
        kwargs = axis_a_kwargs()
        kwargs["disagreements"] = (
            _entry("rec-c3", P, INC),
            _entry("rec-b2", INC, C),
            _entry("rec-a1", C, P),
        )
        with pytest.raises(ValidationError, match="sorted"):
            AxisAgreement(**kwargs)

    def test_duplicate_disagreement_record_id_refuses(self) -> None:
        kwargs = axis_a_kwargs()
        kwargs["disagreements"] = (
            _entry("rec-a1", C, P),
            _entry("rec-a1", INC, C),
            _entry("rec-c3", P, INC),
        )
        with pytest.raises(ValidationError, match="duplicate record_id"):
            AxisAgreement(**kwargs)

    def test_disagreement_entry_that_agrees_refuses(self) -> None:
        with pytest.raises(ValidationError, match="not a disagreement"):
            _entry("rec-a1", C, C)


class TestForgedAccountingAndReportRefusals:
    def test_judged_in_sum_broken(self) -> None:
        with pytest.raises(ValidationError, match="judged_in"):
            MatchAccounting(
                judged_in=7,
                human_in=6,
                n_matched=3,
                judge_only_ids=("r1", "r2", "r3"),
                human_only=(
                    UnmatchedHuman(record_id="r7", cause=UnmatchedHumanCause.REFUSED),
                    UnmatchedHuman(record_id="r8", cause=UnmatchedHumanCause.NOT_IN_RUN),
                    UnmatchedHuman(record_id="r9", cause=UnmatchedHumanCause.NOT_IN_RUN),
                ),
            )

    def test_judge_only_and_human_only_overlap_refuses(self) -> None:
        with pytest.raises(ValidationError, match="both judge_only and human_only"):
            MatchAccounting(
                judged_in=2,
                human_in=2,
                n_matched=1,
                judge_only_ids=("r7",),
                human_only=(UnmatchedHuman(record_id="r7", cause=UnmatchedHumanCause.REFUSED),),
            )

    def test_headline_ready_lie_at_the_boundary_both_directions(self) -> None:
        # n=30 with min=30 -> ready MUST be True; n=29 -> MUST be False.
        kwargs = report_kwargs(n_matched=30)
        kwargs["headline_ready"] = False
        with pytest.raises(ValidationError, match="cannot be asserted"):
            AgreementReport(**kwargs)
        kwargs = report_kwargs(n_matched=29)
        kwargs["headline_ready"] = True
        with pytest.raises(ValidationError, match="cannot be asserted"):
            AgreementReport(**kwargs)

    def test_taxonomy_id_diverging_from_fingerprint_refuses(self) -> None:
        kwargs = report_kwargs()
        kwargs["taxonomy_id"] = "tax-000000000000"
        with pytest.raises(ValidationError, match="questionnaires"):
            AgreementReport(**kwargs)

    def test_confusion_sum_diverging_from_n_matched_refuses(self) -> None:
        kwargs = report_kwargs()  # axes sum to 10
        kwargs["accounting"] = MatchAccounting(
            judged_in=11, human_in=11, n_matched=11, judge_only_ids=(), human_only=()
        )
        with pytest.raises(ValidationError, match="confusion sums to"):
            AgreementReport(**kwargs)

    def test_ci_b_total_diverging_from_bootstrap_resamples_refuses(self) -> None:
        kwargs = report_kwargs()
        ci = BootstrapCI(lower=0.0, upper=0.9625, b_total=5, b_degenerate=1)
        axis_kwargs = axis_a_kwargs()
        axis_kwargs["global_kappa"] = _kv(0.7, 0.38, 0.516129, ci95=ci)
        kwargs["axes"] = (kwargs["axes"][0], AxisAgreement(**axis_kwargs))
        kwargs["bootstrap_resamples"] = 100  # != ci.b_total
        with pytest.raises(ValidationError, match="b_total"):
            AgreementReport(**kwargs)

    def test_human_labels_source_with_path_separator_refuses(self) -> None:
        for source in ("data/labels/x.jsonl", "data\\labels\\x.jsonl"):
            kwargs = report_kwargs()
            kwargs["human_labels_source"] = source
            with pytest.raises(ValidationError, match="basename"):
                AgreementReport(**kwargs)

    def test_axes_wrong_order_refuses(self) -> None:
        kwargs = report_kwargs()
        kwargs["axes"] = tuple(reversed(kwargs["axes"]))
        with pytest.raises(ValidationError, match="axes must be exactly"):
            AgreementReport(**kwargs)


class TestReportLevelSupportGate:
    """Red-team M-2 regression: ONE support gate per measurement protocol.

    The red-team payload: ``compute_agreement`` injects one config value into both
    axes, but a HAND-ASSEMBLED report could carry a different (or trivial ``=1``)
    gate per axis and still validate. Post-fix, ``AgreementReport`` hoists
    ``min_class_support`` and refuses any axis that diverges — in both directions,
    like the ``headline_ready`` boundary test.
    """

    def test_an_axis_with_a_trivialized_gate_refuses(self) -> None:
        # The outcome axis assembled under gate=1 (its INC kappa un-suppressed at
        # 0.375) smuggled into a report declaring gate=5 — the exact M-2 forgery.
        kwargs = report_kwargs()
        kwargs["axes"] = (kwargs["axes"][0], AxisAgreement(**axis_a_kwargs(min_class_support=1)))
        with pytest.raises(ValidationError, match="ONE support gate"):
            AgreementReport(**kwargs)

    def test_a_report_gate_diverging_from_its_axes_refuses(self) -> None:
        # The other direction: axes honestly carry 5, the report claims 1.
        kwargs = report_kwargs()
        kwargs["min_class_support"] = 1
        with pytest.raises(ValidationError, match="ONE support gate"):
            AgreementReport(**kwargs)

    def test_the_report_exposes_the_gate_it_applied(self) -> None:
        report = AgreementReport(**report_kwargs())
        assert report.min_class_support == 5
        assert all(axis.min_class_support == 5 for axis in report.axes)


class TestHumanLabelsSha256Binding:
    """Red-team M-1 regression (contract half): the ground-truth binding field.

    Nothing tied a published κ to the exact label-file bytes; post-fix the report
    carries ``sha256(labels-file bytes)`` supplied by the composition layer, and
    the field refuses anything that is not a sha256 hexdigest (a truncated or
    hand-typed "hash" is not tamper evidence).
    """

    def test_a_valid_sha256_hexdigest_is_carried(self) -> None:
        digest = hashlib.sha256(b"the exact ground-truth bytes").hexdigest()
        kwargs = report_kwargs()
        kwargs["human_labels_sha256"] = digest
        assert AgreementReport(**kwargs).human_labels_sha256 == digest

    def test_none_is_allowed_for_fileless_reports(self) -> None:
        assert AgreementReport(**report_kwargs()).human_labels_sha256 is None

    def test_non_hexdigest_values_refuse(self) -> None:
        bad_values = (
            "deadbeef",  # too short
            "z" * 64,  # not hex
            hashlib.sha256(b"x").hexdigest().upper(),  # not the canonical lowercase
            hashlib.sha256(b"x").hexdigest() + "00",  # too long
        )
        for bad in bad_values:
            kwargs = report_kwargs()
            kwargs["human_labels_sha256"] = bad
            with pytest.raises(ValidationError, match="64 lowercase hex"):
                AgreementReport(**kwargs)


class TestBootstrapCIInvariants:
    def test_valid_ci(self) -> None:
        ci = BootstrapCI(lower=0.0, upper=0.9625, b_total=5, b_degenerate=1)
        assert (ci.lower, ci.upper) == (0.0, 0.9625)
        assert ci.method == "percentile"

    def test_all_degenerate_means_no_bounds(self) -> None:
        ci = BootstrapCI(lower=None, upper=None, b_total=5, b_degenerate=5)
        assert ci.lower is None and ci.upper is None

    def test_degenerate_exceeding_total_refuses(self) -> None:
        with pytest.raises(ValidationError, match="exceeds b_total"):
            BootstrapCI(lower=None, upper=None, b_total=5, b_degenerate=6)

    def test_half_none_bounds_refuse(self) -> None:
        with pytest.raises(ValidationError, match="both"):
            BootstrapCI(lower=0.1, upper=None, b_total=5, b_degenerate=0)

    def test_bounds_present_despite_all_degenerate_refuse(self) -> None:
        with pytest.raises(ValidationError, match="degenerate"):
            BootstrapCI(lower=0.0, upper=0.5, b_total=5, b_degenerate=5)

    def test_none_bounds_despite_valid_resamples_refuse(self) -> None:
        with pytest.raises(ValidationError, match="degenerate"):
            BootstrapCI(lower=None, upper=None, b_total=5, b_degenerate=1)

    def test_lower_above_upper_refuses(self) -> None:
        with pytest.raises(ValidationError, match="lower <= upper"):
            BootstrapCI(lower=0.5, upper=0.1, b_total=5, b_degenerate=0)

    def test_bounds_outside_kappa_range_refuse(self) -> None:
        with pytest.raises(ValidationError, match="-1 <= lower"):
            BootstrapCI(lower=-1.5, upper=0.1, b_total=5, b_degenerate=0)


class TestClassAgreement:
    def test_diagonal_exceeding_marginals_refuses(self) -> None:
        with pytest.raises(ValidationError, match="exceeds"):
            ClassAgreement(
                class_name=C,
                human_support=2,
                judge_support=5,
                both=3,
                value=KappaValue(status=KappaStatus.INSUFFICIENT_SUPPORT),
            )


class TestHumanLabel:
    def test_extra_display_fields_are_ignored_by_declaration(self) -> None:
        # The filled template carries input/output display copies; the loader must
        # ignore them BY DECLARATION — text truth lives in LogRecord via record_id.
        label = HumanLabel.model_validate(
            {
                "record_id": "rec-1",
                "taxonomy_id": TAXONOMY_V1.taxonomy_id,
                "task_type": "factual_query",
                "outcome": "correct",
                "annotator": "synthetic",
                "input_text": "edited display copy",
                "output_text": "does not matter",
            }
        )
        assert not hasattr(label, "input_text")

    def test_unfilled_enum_field_refuses(self) -> None:
        with pytest.raises(ValidationError):
            HumanLabel.model_validate(
                {
                    "record_id": "rec-1",
                    "taxonomy_id": TAXONOMY_V1.taxonomy_id,
                    "task_type": "",
                    "outcome": "correct",
                    "annotator": "synthetic",
                }
            )

    def test_frozen(self) -> None:
        label = HumanLabel(
            record_id="rec-1",
            taxonomy_id=TAXONOMY_V1.taxonomy_id,
            task_type="factual_query",
            outcome="correct",
            annotator="synthetic",
        )
        with pytest.raises(ValidationError):
            label.record_id = "rec-2"  # type: ignore[misc]
