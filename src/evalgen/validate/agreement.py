"""``compute_agreement`` — THE public seam of Phase 4 (ADR-0004 rule 3).

Order of operations is load-bearing:

1. Taxonomy guard, then duplicate guard (defense in depth behind the strict loader).
2. Join on ``record_id``: matched pairs sorted ascending — THE canonical pair order
   the bootstrap index matrix indexes into (content-derived, input-order free).
3. Human-only records CLASSIFIED by cause from the labeling report's own buckets —
   refusals are κ's blind spot stated, not hidden (they correlate with hard cases).
4. ONE index matrix per run, shared by every metric — comparable CIs, one source of
   randomness, byte-identical across runs.
5. Per axis: confusion → global κ → per-class statuses per the ADR rule-4 table →
   CIs only where a κ exists → disagreements with the judge's own confidence and
   rationale (drill-down evidence, never a filter).
6. The frozen ``AgreementReport`` re-verifies everything on construction.

All knobs are INJECTED by the composition layer — ``validate/`` imports no config
(the ``label/`` precedent). Full float64 everywhere; ``round(x, AGREEMENT_DECIMALS)``
only at model construction.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

import numpy as np

from evalgen.contracts import (
    AGREEMENT_DECIMALS,
    AgreementAxis,
    AgreementReport,
    AxisAgreement,
    ClassAgreement,
    DisagreementEntry,
    HumanLabel,
    KappaStatus,
    KappaValue,
    LabeledExample,
    LabelingOutcome,
    MatchAccounting,
    OutcomeLabel,
    TaskTypeLabel,
    UnmatchedHuman,
    UnmatchedHumanCause,
    binarize_confusion,
    kappa_from_confusion,
)
from evalgen.validate.bootstrap import bootstrap_kappa, draw_index_matrix
from evalgen.validate.errors import (
    DuplicateHumanLabelError,
    NoMatchedPairsError,
    TaxonomyMismatchError,
)
from evalgen.validate.kappa import confusion_matrix


def _axis_labels(axis: AgreementAxis, human: HumanLabel, judged: LabeledExample) -> tuple[str, str]:
    """The (human, judge) label pair for one matched record on one axis."""
    if axis is AgreementAxis.TASK_TYPE:
        return (human.task_type.value, judged.verdict.task_type.value)
    return (human.outcome.value, judged.verdict.outcome.value)


def _classify_human_only(
    record_ids: Sequence[str], outcome_report: LabelingOutcome
) -> tuple[UnmatchedHuman, ...]:
    """Why does each human-labeled record have no judge label? Read the report's buckets.

    The four id sets are built ONCE for the whole orphan list (red-team N-4:
    rebuilding them per orphan was O(orphans × report size)) — same causes, same
    order, linear cost.
    """
    report = outcome_report.report
    refused = {e.record_id for e in report.refusal_entries}
    failed = {e.record_id for e in report.failure_entries}
    skipped_budget = set(report.skipped_budget_record_ids)
    collisions = set(report.fewshot_collision_record_ids)

    def cause(record_id: str) -> UnmatchedHumanCause:
        if record_id in refused:
            return UnmatchedHumanCause.REFUSED
        if record_id in failed:
            return UnmatchedHumanCause.FAILED
        if record_id in skipped_budget:
            return UnmatchedHumanCause.SKIPPED_BUDGET
        if record_id in collisions:
            return UnmatchedHumanCause.FEWSHOT_COLLISION
        return UnmatchedHumanCause.NOT_IN_RUN

    return tuple(UnmatchedHuman(record_id=rid, cause=cause(rid)) for rid in record_ids)


def _compute_axis(
    axis: AgreementAxis,
    matched_ids: Sequence[str],
    humans: dict[str, HumanLabel],
    judged: dict[str, LabeledExample],
    index_matrix: np.ndarray,
    min_class_support: int,
) -> AxisAgreement:
    """One axis: confusion, global κ, per-class table, CIs, disagreements."""
    label_enum = TaskTypeLabel if axis is AgreementAxis.TASK_TYPE else OutcomeLabel
    class_order = tuple(member.value for member in label_enum)
    k = len(class_order)
    index_of = {name: i for i, name in enumerate(class_order)}

    pairs = [_axis_labels(axis, humans[rid], judged[rid]) for rid in matched_ids]
    human_seq = [h for h, _ in pairs]
    judge_seq = [j for _, j in pairs]
    confusion = confusion_matrix(human_seq, judge_seq, class_order)
    human_idx = np.array([index_of[h] for h in human_seq], dtype=np.int64)
    judge_idx = np.array([index_of[j] for j in judge_seq], dtype=np.int64)
    n = len(pairs)

    result = kappa_from_confusion(confusion)
    if result is None:
        global_kappa = KappaValue(status=KappaStatus.UNDEFINED_SINGLE_CLASS)
    else:
        p_o, p_e, kappa = result
        global_kappa = KappaValue(
            status=KappaStatus.OK,
            po=round(p_o, AGREEMENT_DECIMALS),
            pe=round(p_e, AGREEMENT_DECIMALS),
            kappa=round(kappa, AGREEMENT_DECIMALS),
            ci95=bootstrap_kappa(human_idx, judge_idx, k, index_matrix),
        )

    col_sums = [sum(confusion[r][c] for r in range(k)) for c in range(k)]
    per_class: list[ClassAgreement] = []
    for i, name in enumerate(class_order):
        h, j, a = sum(confusion[i]), col_sums[i], confusion[i][i]
        if h + j == 0:
            value = KappaValue(status=KappaStatus.ABSENT)
        elif a == n:
            value = KappaValue(status=KappaStatus.UNDEFINED_SINGLE_CLASS)
        elif h + j < min_class_support:
            value = KappaValue(status=KappaStatus.INSUFFICIENT_SUPPORT)
        else:
            collapsed = kappa_from_confusion(binarize_confusion(confusion, i))
            if collapsed is None:
                # Mathematically unreachable (degenerate 2x2 ⟺ a == n or h + j == 0,
                # both handled above) — a loud guard, never a silent number.
                raise RuntimeError(
                    f"axis {axis.value!r}, class {name!r}: binarized matrix degenerate"
                )
            p_o, p_e, kappa = collapsed
            value = KappaValue(
                status=KappaStatus.OK,
                po=round(p_o, AGREEMENT_DECIMALS),
                pe=round(p_e, AGREEMENT_DECIMALS),
                kappa=round(kappa, AGREEMENT_DECIMALS),
                ci95=bootstrap_kappa(human_idx, judge_idx, k, index_matrix, class_index=i),
            )
        per_class.append(
            ClassAgreement(class_name=name, human_support=h, judge_support=j, both=a, value=value)
        )

    disagreements = sorted(
        (
            DisagreementEntry(
                record_id=rid,
                human_label=h,
                judge_label=j,
                judge_confidence=judged[rid].verdict.confidence,
                judge_rationale=judged[rid].verdict.rationale,
            )
            for rid, (h, j) in zip(matched_ids, pairs, strict=True)
            if h != j
        ),
        key=lambda d: (d.human_label, d.judge_label, d.record_id),
    )

    return AxisAgreement(
        axis=axis,
        class_order=class_order,
        confusion=confusion,
        global_kappa=global_kappa,
        per_class=tuple(per_class),
        min_class_support=min_class_support,
        disagreements=tuple(disagreements),
    )


def compute_agreement(
    labeling: LabelingOutcome,
    human_labels: Sequence[HumanLabel],
    *,
    human_labels_source: str,
    human_labels_sha256: str | None = None,
    min_human_labels: int,
    min_class_support: int,
    bootstrap_resamples: int,
    seed: int,
) -> AgreementReport:
    """Measure judge/human agreement — never modify either side (ADR-0004 rule 7).

    ``human_labels_sha256`` is the sha256 hexdigest of the exact label-file bytes,
    computed by the composition layer that read the file (ADR-0004 amendment,
    red-team M-1): it binds the published κ to unchanged ground truth. ``None``
    only when no file backs the labels — the report then says "unrecorded".

    Raises:
        TaxonomyMismatchError: any human label answers a different questionnaire
            than the judge fingerprint's.
        DuplicateHumanLabelError: a ``record_id`` is human-labeled twice (defense
            in depth behind the strict loader).
        NoMatchedPairsError: the join is empty — nothing to measure, nothing to
            diagnose.
    """
    fingerprint = labeling.report.judge
    foreign = {label.taxonomy_id for label in human_labels} - {fingerprint.taxonomy_id}
    mismatched = sorted(foreign)
    if mismatched:
        raise TaxonomyMismatchError(
            f"human labels carry taxonomy_id(s) {mismatched} but the judge fingerprint "
            f"says {fingerprint.taxonomy_id!r} — agreement between different "
            "questionnaires is not agreement (ADR-0003 rule 1)"
        )
    counts = Counter(label.record_id for label in human_labels)
    duplicates = sorted(record_id for record_id, count in counts.items() if count > 1)
    if duplicates:
        raise DuplicateHumanLabelError(
            f"record_id(s) {duplicates} are human-labeled more than once — ground "
            "truth must be single-valued"
        )

    judged = {example.record_id: example for example in labeling.labeled_examples}
    humans = {label.record_id: label for label in human_labels}
    matched_ids = sorted(set(judged) & set(humans))
    if not matched_ids:
        raise NoMatchedPairsError(
            f"0 matched pairs between {len(judged)} judgments and {len(humans)} human "
            "labels — a report with n = 0 is a lie machine"
        )
    judge_only_ids = tuple(sorted(set(judged) - set(humans)))
    human_only = _classify_human_only(sorted(set(humans) - set(judged)), labeling)
    accounting = MatchAccounting(
        judged_in=len(judged),
        human_in=len(humans),
        n_matched=len(matched_ids),
        judge_only_ids=judge_only_ids,
        human_only=human_only,
    )

    # ONE index matrix per run — a function of (seed, B, n) only, shared by every
    # metric so adding one never moves the draws (ADR-0004 rule 5).
    index_matrix = draw_index_matrix(seed=seed, resamples=bootstrap_resamples, n=len(matched_ids))
    axes = tuple(
        _compute_axis(axis, matched_ids, humans, judged, index_matrix, min_class_support)
        for axis in (AgreementAxis.TASK_TYPE, AgreementAxis.OUTCOME)
    )

    return AgreementReport(
        judge=fingerprint,
        taxonomy_id=fingerprint.taxonomy_id,
        human_labels_source=human_labels_source,
        human_labels_sha256=human_labels_sha256,
        annotators=tuple(sorted({label.annotator for label in human_labels})),
        accounting=accounting,
        min_human_labels=min_human_labels,
        headline_ready=len(matched_ids) >= min_human_labels,
        min_class_support=min_class_support,
        bootstrap_resamples=bootstrap_resamples,
        seed=seed,
        axes=axes,
    )
