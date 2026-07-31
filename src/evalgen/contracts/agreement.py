"""The agreement contracts: Cohen's κ arithmetic + the self-validating report (ADR-0004).

Three load-bearing choices live here:

1. **One κ formula, hosted where the validators live.** ``kappa_from_confusion`` is a
   pure function over an integer confusion matrix (no numpy — the ``derive_record_id``
   precedent): κ = (n·D − Σrᵢcᵢ)/(n² − Σrᵢcᵢ), one float division, with the degeneracy
   test in EXACT integers (p_e = 1 ⟺ Σrᵢcᵢ == n², by Cauchy–Schwarz "both raters
   single-class, same class"). Per-class κ IS the same function applied to
   ``binarize_confusion``'s 2×2 collapse. Every κ in the repo — global, per-class,
   every bootstrap resample — flows through this one function: hand-check it once,
   trust every number.
2. **A report that lies about its own κ refuses to exist.** ``AxisAgreement``
   recomputes p_o, p_e, κ, every per-class support and status, and the disagreement
   multiplicities from its own confusion matrix on every construction — including
   deserialization from disk (the ``LogRecord``/``LabelingReport`` tamper-evidence
   discipline). Stated limitation: the bootstrap *interval* is not recomputable here
   (it would re-run B resamples per deserialization); its integrity is owned by the
   determinism tests and the hand-checked fixtures (ADR-0004 rule 4).
3. **Degeneracy is a typed vocabulary, never a NaN and never a silent 0.**
   ``KappaStatus`` names each case (p_e = 1 undefined; class absent; support under the
   gate); a suppressed number always ships with its supports — suppression-with-status
   is the anti-cherry-picking guarantee (ADR-0004 options §4).
"""

from __future__ import annotations

import operator
import re
from collections import Counter
from collections.abc import Sequence
from datetime import date
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evalgen.contracts.labeling import MAX_RATIONALE_LEN, JudgeFingerprint
from evalgen.contracts.taxonomy import JudgeConfidence, OutcomeLabel, TaskTypeLabel

#: Report-boundary rounding of p_o / p_e / κ / CI bounds (``SIMILARITY_DECIMALS``
#: twin): decisions and comparisons always use full float64; ``round(x, 6)`` happens
#: exactly once, at model construction (ADR-0002 rounding discipline).
AGREEMENT_DECIMALS = 6


def _checked_matrix(matrix: Sequence[Sequence[int]]) -> list[tuple[int, ...]]:
    """Validate a square, non-negative integer confusion matrix; return exact int rows.

    ``operator.index`` accepts any true integer (including numpy integer scalars)
    while refusing floats — the exact-integer arithmetic below must never silently
    run on lossy values.
    """
    k = len(matrix)
    if k == 0:
        raise ValueError("confusion matrix is empty")
    rows: list[tuple[int, ...]] = []
    for r, raw_row in enumerate(matrix):
        cells: list[int] = []
        row = tuple(raw_row)
        if len(row) != k:
            raise ValueError(
                f"confusion matrix is ragged: row {r} has {len(row)} cells, expected {k} "
                "(rows = humans, cols = judge, one per class)"
            )
        for c, raw_cell in enumerate(row):
            try:
                cell = operator.index(raw_cell)
            except TypeError:
                raise ValueError(
                    f"confusion[{r}][{c}] = {raw_cell!r} is not an integer count"
                ) from None
            if cell < 0:
                raise ValueError(f"confusion[{r}][{c}] = {cell} is negative — counts cannot be")
            cells.append(cell)
        rows.append(tuple(cells))
    return rows


def kappa_from_confusion(matrix: Sequence[Sequence[int]]) -> tuple[float, float, float] | None:
    """(p_o, p_e, κ) in full float64 — or ``None`` when p_e = 1 (κ undefined).

    For a k×k integer confusion matrix (rows = human, cols = judge) with
    n = Σ mᵢⱼ, diagonal D = Σ mᵢᵢ, row sums rᵢ, column sums cᵢ, S = Σᵢ rᵢ·cᵢ:

        p_o = D / n
        p_e = S / n²
        κ   = (n·D − S) / (n² − S)      (exact integer numerator/denominator,
                                         ONE float division)

    Degeneracy test in EXACT integers: p_e = 1 ⟺ S == n² (by Cauchy–Schwarz this
    happens iff both raters are single-class on the same class) — no float-epsilon
    hazard on the most dangerous branch. ``None`` is a typed "undefined", NOT 0:
    0 would claim chance-level agreement where agreement-above-chance is
    unmeasurable (ADR-0004 options §4).

    Raises ``ValueError`` on an empty/ragged/negative/non-integer matrix or n = 0.
    """
    rows = _checked_matrix(matrix)
    k = len(rows)
    n = sum(sum(row) for row in rows)
    if n == 0:
        raise ValueError("confusion matrix sums to 0 — there is nothing to measure")
    diagonal = sum(rows[i][i] for i in range(k))
    row_sums = [sum(row) for row in rows]
    col_sums = [sum(rows[r][c] for r in range(k)) for c in range(k)]
    s = sum(row_sums[i] * col_sums[i] for i in range(k))
    if s == n * n:
        return None
    p_o = diagonal / n
    p_e = s / (n * n)
    kappa = (n * diagonal - s) / (n * n - s)
    return (p_o, p_e, kappa)


def binarize_confusion(
    matrix: Sequence[Sequence[int]], index: int
) -> tuple[tuple[int, int], tuple[int, int]]:
    """One-vs-rest collapse of a k×k confusion matrix for class ``index``.

    a = mᵢᵢ, b = rᵢ − a, c = cᵢ − a, d = n − a − b − c → ((a, b), (c, d)).
    Feed the result back to ``kappa_from_confusion``: per-class κ IS global κ on the
    collapsed matrix — ONE formula total (ADR-0004 options §3).
    """
    rows = _checked_matrix(matrix)
    k = len(rows)
    if not 0 <= index < k:
        raise ValueError(f"class index {index} out of range for a {k}x{k} confusion matrix")
    n = sum(sum(row) for row in rows)
    a = rows[index][index]
    b = sum(rows[index]) - a
    c = sum(rows[r][index] for r in range(k)) - a
    d = n - a - b - c
    return ((a, b), (c, d))


def _round(value: float) -> float:
    return round(value, AGREEMENT_DECIMALS)


class HumanLabel(BaseModel):
    """One human-annotated record from the filled label template (ADR-0004 rule 2).

    ``extra="ignore"`` is load-bearing and deliberate: the filled template still
    carries ``input_text``/``output_text`` display copies, which the loader ignores
    BY DECLARATION — record text has one source of truth (``LogRecord``, joined by
    ``record_id``), so a human accidentally editing a display copy cannot alter what
    κ is computed on. No content-derived id either (humans don't compute hashes):
    tamper evidence for ground truth is the protect hook + git history, stated in
    ADR-0004 options §1.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    record_id: str = Field(min_length=1)
    taxonomy_id: str = Field(min_length=1)
    task_type: TaskTypeLabel
    outcome: OutcomeLabel
    #: Free-form pseudonym, never an email (publish-grade PII discipline).
    annotator: str = Field(min_length=1)
    #: Optional and never wall-clock defaulted (byte-identical runs).
    labeled_on: date | None = None
    note: str = ""


class AgreementAxis(StrEnum):
    """The two taxonomy axes κ is computed on — pinned to ``TAXONOMY_V1.axes`` by test."""

    TASK_TYPE = "task_type"
    OUTCOME = "outcome"


#: Enum-declaration class order per axis — THE canonical confusion-matrix order.
_AXIS_CLASS_ORDER: dict[AgreementAxis, tuple[str, ...]] = {
    AgreementAxis.TASK_TYPE: tuple(member.value for member in TaskTypeLabel),
    AgreementAxis.OUTCOME: tuple(member.value for member in OutcomeLabel),
}


class KappaStatus(StrEnum):
    """Typed degeneracy vocabulary (ADR-0004 options §4) — never a NaN, never a silent 0."""

    OK = "ok"
    #: p_e = 1: both raters single-class on the same class — κ undefined, no number.
    UNDEFINED_SINGLE_CLASS = "undefined_single_class"
    #: Per-class only: the class appears in neither rater (supports 0/0).
    ABSENT = "absent"
    #: Per-class only: 0 < human+judge support < min_class_support — the κ value is
    #: suppressed (a κ on 3 occurrences is noise wearing a number's clothes) but the
    #: row and its supports always ship.
    INSUFFICIENT_SUPPORT = "insufficient_support"


class BootstrapCI(BaseModel):
    """A paired percentile-bootstrap CI95 with its degenerate resamples counted.

    Bounds are ``None`` exactly when EVERY resample was degenerate (b_used = 0) —
    reported as unavailable-with-count, never as [NaN, NaN] (ADR-0004 rule 5).
    """

    model_config = ConfigDict(frozen=True)

    lower: float | None
    upper: float | None
    b_total: int = Field(ge=1)
    b_degenerate: int = Field(ge=0)
    method: Literal["percentile"] = "percentile"

    @model_validator(mode="after")
    def _bounds_and_counts_must_agree(self) -> BootstrapCI:
        if self.b_degenerate > self.b_total:
            raise ValueError(f"b_degenerate ({self.b_degenerate}) exceeds b_total ({self.b_total})")
        if (self.lower is None) != (self.upper is None):
            raise ValueError("lower and upper must both be present or both be None")
        all_degenerate = self.b_degenerate == self.b_total
        if (self.lower is None) != all_degenerate:
            raise ValueError(
                "bounds must be None exactly when every resample was degenerate "
                f"(lower={self.lower!r}, b_degenerate={self.b_degenerate}, "
                f"b_total={self.b_total})"
            )
        if (
            self.lower is not None
            and self.upper is not None
            and not (-1.0 <= self.lower <= self.upper <= 1.0)
        ):
            raise ValueError(
                f"CI bounds must satisfy -1 <= lower <= upper <= 1, got "
                f"[{self.lower!r}, {self.upper!r}]"
            )
        return self


class KappaValue(BaseModel):
    """One κ with its status: a number only when the statistic exists (ADR-0004 rule 4)."""

    model_config = ConfigDict(frozen=True)

    status: KappaStatus
    po: float | None = None
    pe: float | None = None
    kappa: float | None = None
    ci95: BootstrapCI | None = None

    @model_validator(mode="after")
    def _values_must_match_status(self) -> KappaValue:
        if self.status is KappaStatus.OK:
            if self.po is None or self.pe is None or self.kappa is None:
                raise ValueError("status 'ok' requires po, pe and kappa to be present")
            if not (0.0 <= self.po <= 1.0) or not (0.0 <= self.pe <= 1.0):
                raise ValueError(f"po/pe must lie in [0, 1], got po={self.po!r} pe={self.pe!r}")
            if not (-1.0 <= self.kappa <= 1.0):
                raise ValueError(f"kappa must lie in [-1, 1], got {self.kappa!r}")
        else:
            if (
                self.po is not None
                or self.pe is not None
                or self.kappa is not None
                or self.ci95 is not None
            ):
                raise ValueError(
                    f"status {self.status.value!r} must carry no values — a suppressed or "
                    "undefined kappa never ships a number (ADR-0004 options §4)"
                )
        return self


class ClassAgreement(BaseModel):
    """One per-class row: supports on both sides + the (possibly suppressed) κ."""

    model_config = ConfigDict(frozen=True)

    class_name: str = Field(min_length=1)
    #: Row sum over matched pairs (how often the human used this class).
    human_support: int = Field(ge=0)
    #: Column sum over matched pairs (how often the judge used this class).
    judge_support: int = Field(ge=0)
    #: Diagonal cell — both raters chose this class.
    both: int = Field(ge=0)
    value: KappaValue

    @model_validator(mode="after")
    def _diagonal_cannot_exceed_marginals(self) -> ClassAgreement:
        if self.both > min(self.human_support, self.judge_support):
            raise ValueError(
                f"class {self.class_name!r}: both={self.both} exceeds "
                f"min(human_support={self.human_support}, judge_support={self.judge_support})"
            )
        return self


class DisagreementEntry(BaseModel):
    """One matched pair where human ≠ judge — the flywheel's fuel (ADR-0004 rule 6).

    The judge's confidence and rationale appear HERE, as drill-down evidence —
    never as a κ filter (that would be self-grading, the κ-gaming CLAUDE.md forbids).
    """

    model_config = ConfigDict(frozen=True)

    record_id: str = Field(min_length=1)
    human_label: str = Field(min_length=1)
    judge_label: str = Field(min_length=1)
    judge_confidence: JudgeConfidence
    judge_rationale: str = Field(min_length=1, max_length=MAX_RATIONALE_LEN)

    @model_validator(mode="after")
    def _must_actually_disagree(self) -> DisagreementEntry:
        if self.human_label == self.judge_label:
            raise ValueError(
                f"record {self.record_id!r}: human and judge both say "
                f"{self.human_label!r} — an agreement is not a disagreement entry"
            )
        return self


class AxisAgreement(BaseModel):
    """One axis's full agreement picture — self-validating against its own confusion.

    THE load-bearing validator (ADR-0004 rule 4): p_o, p_e, κ, per-class supports,
    every status, and the disagreement multiplicities are recomputed from
    ``confusion`` on every construction; any mismatch refuses to exist. NOT
    recomputable here: the bootstrap intervals — owned by the determinism tests and
    the hand fixtures (re-running B resamples per deserialization is not a
    validator's job).
    """

    model_config = ConfigDict(frozen=True)

    axis: AgreementAxis
    #: Enum declaration order, pinned — reordering classes is a different questionnaire.
    class_order: tuple[str, ...]
    #: Rows = human, cols = judge, in ``class_order``.
    confusion: tuple[tuple[int, ...], ...]
    global_kappa: KappaValue
    #: ONE row per class, declared order, never dropped.
    per_class: tuple[ClassAgreement, ...]
    min_class_support: int = Field(ge=1)
    disagreements: tuple[DisagreementEntry, ...]

    @model_validator(mode="after")
    def _must_match_own_confusion(self) -> AxisAgreement:
        expected_order = _AXIS_CLASS_ORDER[self.axis]
        if self.class_order != expected_order:
            raise ValueError(
                f"axis {self.axis.value!r}: class_order {self.class_order!r} != enum "
                f"declaration order {expected_order!r}"
            )
        k = len(self.class_order)
        rows = _checked_matrix(self.confusion)
        if len(rows) != k:
            raise ValueError(
                f"axis {self.axis.value!r}: confusion is {len(rows)}x{len(rows)} but the "
                f"axis has {k} classes"
            )
        n = sum(sum(row) for row in rows)
        if n < 1:
            raise ValueError(f"axis {self.axis.value!r}: confusion sums to 0")
        self._check_global(rows)
        self._check_per_class(rows, n)
        self._check_disagreements(rows, n)
        return self

    def _check_global(self, rows: list[tuple[int, ...]]) -> None:
        result = kappa_from_confusion(rows)
        value = self.global_kappa
        if result is None:
            if value.status is not KappaStatus.UNDEFINED_SINGLE_CLASS:
                raise ValueError(
                    f"axis {self.axis.value!r}: confusion is degenerate (p_e = 1) but "
                    f"global status is {value.status.value!r} — must be "
                    "'undefined_single_class'"
                )
            return
        if value.status is not KappaStatus.OK:
            raise ValueError(
                f"axis {self.axis.value!r}: global kappa is defined but status is "
                f"{value.status.value!r} — must be 'ok'"
            )
        p_o, p_e, kappa = result
        expected = (_round(p_o), _round(p_e), _round(kappa))
        stored = (value.po, value.pe, value.kappa)
        if stored != expected:
            raise ValueError(
                f"axis {self.axis.value!r}: stored (po, pe, kappa) = {stored!r} but the "
                f"confusion matrix says {expected!r} — a report that lies about its own "
                "kappa refuses to exist (ADR-0004 rule 4)"
            )

    def _check_per_class(self, rows: list[tuple[int, ...]], n: int) -> None:
        k = len(self.class_order)
        if len(self.per_class) != k:
            raise ValueError(
                f"axis {self.axis.value!r}: {len(self.per_class)} per-class rows for "
                f"{k} classes — one row per class, never dropped"
            )
        col_sums = [sum(rows[r][c] for r in range(k)) for c in range(k)]
        for i, (name, entry) in enumerate(zip(self.class_order, self.per_class, strict=True)):
            if entry.class_name != name:
                raise ValueError(
                    f"axis {self.axis.value!r}: per_class[{i}] is {entry.class_name!r}, "
                    f"expected {name!r} (declared order)"
                )
            h, j, a = sum(rows[i]), col_sums[i], rows[i][i]
            if (entry.human_support, entry.judge_support, entry.both) != (h, j, a):
                raise ValueError(
                    f"axis {self.axis.value!r}, class {name!r}: stored supports "
                    f"(h={entry.human_support}, j={entry.judge_support}, "
                    f"both={entry.both}) but the confusion marginals say "
                    f"(h={h}, j={j}, both={a})"
                )
            if h + j == 0:
                expected_status = KappaStatus.ABSENT
            elif a == n:
                expected_status = KappaStatus.UNDEFINED_SINGLE_CLASS
            elif h + j < self.min_class_support:
                expected_status = KappaStatus.INSUFFICIENT_SUPPORT
            else:
                expected_status = KappaStatus.OK
            if entry.value.status is not expected_status:
                raise ValueError(
                    f"axis {self.axis.value!r}, class {name!r}: status "
                    f"{entry.value.status.value!r} but supports h={h}, j={j}, both={a} "
                    f"under min_class_support={self.min_class_support} demand "
                    f"{expected_status.value!r}"
                )
            if expected_status is KappaStatus.OK:
                result = kappa_from_confusion(binarize_confusion(rows, i))
                if result is None:
                    # Mathematically unreachable: degenerate 2x2 means a == n (all in
                    # the agreeing class cell) or h + j == 0 (all in d) — both handled
                    # above. Kept as a loud guard, never a silent number.
                    raise ValueError(
                        f"axis {self.axis.value!r}, class {name!r}: binarized matrix "
                        "unexpectedly degenerate"
                    )
                p_o, p_e, kappa = result
                expected_values = (_round(p_o), _round(p_e), _round(kappa))
                stored = (entry.value.po, entry.value.pe, entry.value.kappa)
                if stored != expected_values:
                    raise ValueError(
                        f"axis {self.axis.value!r}, class {name!r}: stored "
                        f"(po, pe, kappa) = {stored!r} but the binarized confusion says "
                        f"{expected_values!r}"
                    )

    def _check_disagreements(self, rows: list[tuple[int, ...]], n: int) -> None:
        k = len(self.class_order)
        diagonal = sum(rows[i][i] for i in range(k))
        off_diagonal = n - diagonal
        if len(self.disagreements) != off_diagonal:
            raise ValueError(
                f"axis {self.axis.value!r}: {len(self.disagreements)} disagreement "
                f"entries but the confusion has {off_diagonal} off-diagonal pairs"
            )
        counts = Counter((d.human_label, d.judge_label) for d in self.disagreements)
        for hi in range(k):
            for ji in range(k):
                if hi == ji:
                    continue
                expected = rows[hi][ji]
                got = counts.pop((self.class_order[hi], self.class_order[ji]), 0)
                if got != expected:
                    raise ValueError(
                        f"axis {self.axis.value!r}: {got} disagreement entries for "
                        f"({self.class_order[hi]!r} -> {self.class_order[ji]!r}) but "
                        f"confusion[{hi}][{ji}] = {expected} — per-cell multiplicity "
                        "must match"
                    )
        if counts:
            raise ValueError(
                f"axis {self.axis.value!r}: disagreement entries carry labels outside "
                f"class_order: {sorted(counts)!r}"
            )
        keys = [(d.human_label, d.judge_label, d.record_id) for d in self.disagreements]
        if keys != sorted(keys):
            raise ValueError(
                f"axis {self.axis.value!r}: disagreements must be sorted by "
                "(human_label, judge_label, record_id)"
            )
        record_ids = [d.record_id for d in self.disagreements]
        if len(set(record_ids)) != len(record_ids):
            raise ValueError(
                f"axis {self.axis.value!r}: duplicate record_id among disagreements — "
                "one matched pair disagrees at most once per axis"
            )


class UnmatchedHumanCause(StrEnum):
    """Why a human-labeled record has no judge label — classified, never averaged away."""

    REFUSED = "refused"
    FAILED = "failed"
    SKIPPED_BUDGET = "skipped_budget"
    #: The ADR-0003 corollary made visible: a judge-seen record can never enter κ.
    FEWSHOT_COLLISION = "fewshot_collision"
    NOT_IN_RUN = "not_in_run"


class UnmatchedHuman(BaseModel):
    """One human-only record with its classified cause (ADR-0004 rule 3)."""

    model_config = ConfigDict(frozen=True)

    record_id: str = Field(min_length=1)
    cause: UnmatchedHumanCause


class MatchAccounting(BaseModel):
    """The join ledger: every label on either side lands in exactly one bucket.

    Sums are enforced by construction — a join that silently drops a side makes
    "κ = 0.71 (n = 42)" unverifiable (ADR-0004 context, failure mode 2).
    """

    model_config = ConfigDict(frozen=True)

    #: len(labeling.labeled_examples) — the judge side of the join.
    judged_in: int = Field(ge=0)
    #: len(human_labels) — the human side of the join.
    human_in: int = Field(ge=0)
    n_matched: int = Field(ge=1)
    judge_only_ids: tuple[str, ...]
    human_only: tuple[UnmatchedHuman, ...]

    @model_validator(mode="after")
    def _sums_and_id_sets_must_hold(self) -> MatchAccounting:
        if self.judged_in != self.n_matched + len(self.judge_only_ids):
            raise ValueError(
                f"judged_in ({self.judged_in}) != n_matched ({self.n_matched}) + "
                f"judge_only ({len(self.judge_only_ids)}) — every judgment lands in "
                "exactly one bucket"
            )
        if self.human_in != self.n_matched + len(self.human_only):
            raise ValueError(
                f"human_in ({self.human_in}) != n_matched ({self.n_matched}) + "
                f"human_only ({len(self.human_only)}) — every human label lands in "
                "exactly one bucket"
            )
        judge_ids = list(self.judge_only_ids)
        if judge_ids != sorted(judge_ids) or len(set(judge_ids)) != len(judge_ids):
            raise ValueError("judge_only_ids must be sorted ascending and unique")
        human_ids = [u.record_id for u in self.human_only]
        if human_ids != sorted(human_ids) or len(set(human_ids)) != len(human_ids):
            raise ValueError("human_only must be sorted by record_id ascending and unique")
        overlap = sorted(set(judge_ids) & set(human_ids))
        if overlap:
            raise ValueError(
                f"record_id(s) {overlap} appear in both judge_only and human_only — "
                "an orphan has exactly one side"
            )
        return self


class AgreementReport(BaseModel):
    """THE Phase 4 artifact: κ per axis with n, supports, CI95 and the join ledger.

    Self-validating (ADR-0004 rule 4): the axes re-verify their own arithmetic, the
    accounting re-verifies its sums, and this model ties them together — confusion
    totals equal ``n_matched``, every CI's ``b_total`` equals the declared resample
    count, ``headline_ready`` cannot lie about ``min_human_labels``, every axis
    carries the ONE report-level ``min_class_support`` (ADR-0004 amendment, red-team
    M-2), and the taxonomy id cannot drift from the judge fingerprint's.
    """

    model_config = ConfigDict(frozen=True)

    judge: JudgeFingerprint
    taxonomy_id: str = Field(min_length=1)
    #: BASENAME only — an absolute path would leak the environment (ADR-0001 PII rule).
    human_labels_source: str = Field(min_length=1)
    #: sha256 hexdigest of the EXACT label-file bytes, supplied by the composition
    #: layer that read the file (ADR-0004 amendment, red-team M-1): a published κ is
    #: replay-verifiable only against unchanged ground truth, so the binding travels
    #: on the report's face. ``None`` only for hand-assembled reports without file
    #: provenance — the renderer then prints "unrecorded", never nothing.
    human_labels_sha256: str | None = None
    annotators: tuple[str, ...] = Field(min_length=1)
    accounting: MatchAccounting
    min_human_labels: int = Field(ge=1)
    headline_ready: bool
    #: THE per-class support gate — part of the measurement protocol, as visible as
    #: B and seed; every axis must carry it verbatim (validator below, red-team M-2).
    min_class_support: int = Field(ge=1)
    bootstrap_resamples: int = Field(ge=1)
    seed: int
    #: Exactly (task_type, outcome), enum declaration order.
    axes: tuple[AxisAgreement, ...]

    @field_validator("human_labels_source")
    @classmethod
    def _must_be_a_basename(cls, value: str) -> str:
        if "/" in value or "\\" in value:
            raise ValueError(
                f"human_labels_source must be a basename, got {value!r} — paths leak "
                "the environment (ADR-0001 PII rule)"
            )
        return value

    @field_validator("human_labels_sha256")
    @classmethod
    def _must_be_a_sha256_hexdigest(cls, value: str | None) -> str | None:
        if value is not None and re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise ValueError(
                "human_labels_sha256 must be 64 lowercase hex chars (a sha256 "
                f"hexdigest of the label-file bytes), got {value!r}"
            )
        return value

    @model_validator(mode="after")
    def _report_must_cohere(self) -> AgreementReport:
        if self.taxonomy_id != self.judge.taxonomy_id:
            raise ValueError(
                f"taxonomy_id {self.taxonomy_id!r} != judge fingerprint's "
                f"{self.judge.taxonomy_id!r} — agreement between different "
                "questionnaires is not agreement (ADR-0003 rule 1)"
            )
        names = list(self.annotators)
        if names != sorted(names) or len(set(names)) != len(names):
            raise ValueError("annotators must be sorted ascending and unique")
        expected_ready = self.accounting.n_matched >= self.min_human_labels
        if self.headline_ready != expected_ready:
            raise ValueError(
                f"headline_ready={self.headline_ready} but n_matched="
                f"{self.accounting.n_matched} vs min_human_labels="
                f"{self.min_human_labels} demands {expected_ready} — the headline "
                "gate cannot be asserted into existence"
            )
        expected_axes = (AgreementAxis.TASK_TYPE, AgreementAxis.OUTCOME)
        if tuple(axis.axis for axis in self.axes) != expected_axes:
            raise ValueError(
                f"axes must be exactly {[a.value for a in expected_axes]!r} in order, "
                f"got {[a.axis.value for a in self.axes]!r}"
            )
        for axis in self.axes:
            if axis.min_class_support != self.min_class_support:
                raise ValueError(
                    f"axis {axis.axis.value!r} carries min_class_support="
                    f"{axis.min_class_support} but the report declares "
                    f"{self.min_class_support} — ONE support gate per measurement "
                    "protocol (red-team M-2: a per-axis gate could be quietly "
                    "trivialized)"
                )
            total = sum(sum(row) for row in axis.confusion)
            if total != self.accounting.n_matched:
                raise ValueError(
                    f"axis {axis.axis.value!r}: confusion sums to {total} but "
                    f"n_matched = {self.accounting.n_matched} — every matched pair "
                    "appears exactly once per axis"
                )
            values = [axis.global_kappa] + [c.value for c in axis.per_class]
            for value in values:
                if value.ci95 is not None and value.ci95.b_total != self.bootstrap_resamples:
                    raise ValueError(
                        f"axis {axis.axis.value!r}: a CI carries b_total="
                        f"{value.ci95.b_total} but the report declares "
                        f"bootstrap_resamples={self.bootstrap_resamples}"
                    )
        return self

    @property
    def headline(self) -> KappaValue | None:
        """The outcome-axis global κ — ``None`` when not headline-ready.

        THE number Phase 5's export gate reads against ``min_export_kappa``
        (ADR-0004 options §6; the gate itself lives in export, not here).
        """
        if not self.headline_ready:
            return None
        return next(a for a in self.axes if a.axis is AgreementAxis.OUTCOME).global_kappa
