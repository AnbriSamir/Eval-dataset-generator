"""The labeling engine: canonical order, five buckets, budget in ONE place (ADR-0003 rule 6).

The cost guard lives HERE, not in the judges — the budget must bind identically on the
fake path (so the demo exercises it) and the real path (so it actually caps spend).
Records are processed in ``record_sort_key`` order (ADR-0002 rule 1), so the budget cut
and every report entry are input-order independent: shuffle the caller's list and the
SAME records get labeled.

Bucket semantics:

- **fewshot collision** — the record's canonical hash appears among the judge's few-shot
  content hashes: its answer key was handed to the judge, so it is never labeled and can
  never enter the Phase 4 κ join. Consumes NO budget (no API call was spent).
- **budget** — ``labeled + refused + failed == max_labels`` reached: every further
  record is a counted, id-listed skip. Refusals and failures CONSUME budget (an API
  call was spent — the guard caps spend, not success).
- Non-``JudgeError`` exceptions propagate: our own bugs are not labeling statistics.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Sequence

from evalgen.contracts import (
    Judge,
    LabeledExample,
    LabelFailureEntry,
    LabelFailureReason,
    LabelingOutcome,
    LabelingReport,
    LogRecord,
    record_sort_key,
)
from evalgen.contracts.labeling import MAX_FAILURE_DETAIL_LEN
from evalgen.label.errors import JudgeAPIError, JudgeParseError, JudgeRefusalError


def run_labeling(records: Sequence[LogRecord], *, judge: Judge, max_labels: int) -> LabelingOutcome:
    """Label ``records`` with ``judge`` under the ``max_labels`` cost guard.

    Raises ``ValueError`` on duplicate ``record_id``s (labeling expects post-dedup
    input — a duplicated id is a caller bug, not a statistic) and on a non-positive
    budget (fail early naming the knob, ADR-0002 amendment discipline).
    """
    if max_labels < 1:
        raise ValueError(f"max_labels must be >= 1, got {max_labels}")
    ids = [r.record_id for r in records]
    if len(set(ids)) != len(ids):
        counts = Counter(ids)
        duplicates = sorted(rid for rid, n in counts.items() if n > 1)
        raise ValueError(f"duplicate record_id(s) {duplicates} — labeling expects post-dedup input")

    order = sorted(records, key=record_sort_key)
    collision_hashes = set(judge.fingerprint.few_shot_content_hashes)
    taxonomy_id = judge.fingerprint.taxonomy_id

    labeled: list[LabeledExample] = []
    refusals: list[LabelFailureEntry] = []
    failures: list[LabelFailureEntry] = []
    budget_skipped: list[str] = []
    collisions: list[str] = []

    for record in order:
        canonical_hash = hashlib.sha256(record.canonical_text.encode("utf-8")).hexdigest()
        if canonical_hash in collision_hashes:
            collisions.append(record.record_id)
            continue
        if len(labeled) + len(refusals) + len(failures) == max_labels:
            budget_skipped.append(record.record_id)
            continue
        try:
            judgment = judge.judge(record.input_text, record.output_text)
        except JudgeRefusalError as exc:
            refusals.append(
                LabelFailureEntry(
                    record_id=record.record_id,
                    reason=LabelFailureReason.REFUSAL,
                    detail=exc.detail[:MAX_FAILURE_DETAIL_LEN],
                )
            )
        except JudgeParseError as exc:
            failures.append(
                LabelFailureEntry(
                    record_id=record.record_id,
                    reason=LabelFailureReason.PARSE_ERROR,
                    detail=exc.detail[:MAX_FAILURE_DETAIL_LEN],
                )
            )
        except JudgeAPIError as exc:
            failures.append(
                LabelFailureEntry(
                    record_id=record.record_id,
                    reason=LabelFailureReason.API_ERROR,
                    detail=exc.detail[:MAX_FAILURE_DETAIL_LEN],
                )
            )
        else:
            labeled.append(
                LabeledExample(
                    record_id=record.record_id,
                    taxonomy_id=taxonomy_id,
                    model_id=judgment.model_id,
                    verdict=judgment.verdict,
                )
            )

    labeled.sort(key=lambda e: e.record_id)
    refusals.sort(key=lambda e: e.record_id)
    failures.sort(key=lambda e: e.record_id)
    budget_skipped.sort()
    collisions.sort()

    # Only reasons that occurred appear, in enum declaration order (IngestReport style).
    failures_by_reason: dict[LabelFailureReason, int] = {}
    for reason in (LabelFailureReason.PARSE_ERROR, LabelFailureReason.API_ERROR):
        count = sum(1 for e in failures if e.reason is reason)
        if count:
            failures_by_reason[reason] = count

    report = LabelingReport(
        judge=judge.fingerprint,
        max_labels=max_labels,
        records_in=len(records),
        labeled=len(labeled),
        refused=len(refusals),
        failed=len(failures),
        skipped_budget=len(budget_skipped),
        skipped_fewshot_collision=len(collisions),
        failures_by_reason=failures_by_reason,
        refusal_entries=tuple(refusals),
        failure_entries=tuple(failures),
        skipped_budget_record_ids=tuple(budget_skipped),
        fewshot_collision_record_ids=tuple(collisions),
    )
    return LabelingOutcome(labeled_examples=tuple(labeled), report=report)
