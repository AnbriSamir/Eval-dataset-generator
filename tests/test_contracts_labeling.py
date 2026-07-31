"""Labeling contracts: five-bucket sums, tamper-evident ids, cross-checked outcomes.

Every refuse case is an ADR-0003 rule-7/8 invariant; the happy-path round-trips pin
that validators re-run on deserialization (same discipline as LogRecord/DedupOutcome).
"""

from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from conftest import STUB_VERDICT, make_stub_fingerprint
from evalgen.contracts import (
    CANONICAL_SEP,
    MAX_RATIONALE_LEN,
    TAXONOMY_V1,
    FewShotExample,
    JudgeConfidence,
    JudgeFingerprint,
    JudgeVerdict,
    Judgment,
    LabeledExample,
    LabelFailureEntry,
    LabelFailureReason,
    LabelingOutcome,
    LabelingReport,
    OutcomeLabel,
    TaskTypeLabel,
    derive_few_shot_id,
)

FP = make_stub_fingerprint()
SHA = hashlib.sha256(b"x").hexdigest()


def entry(record_id: str, reason: LabelFailureReason, detail: str = "detail") -> LabelFailureEntry:
    return LabelFailureEntry(record_id=record_id, reason=reason, detail=detail)


def report(**overrides: object) -> LabelingReport:
    fields: dict = {
        "judge": FP,
        "max_labels": 10,
        "records_in": 7,
        "labeled": 2,
        "refused": 1,
        "failed": 2,
        "skipped_budget": 1,
        "skipped_fewshot_collision": 1,
        "failures_by_reason": {
            LabelFailureReason.PARSE_ERROR: 1,
            LabelFailureReason.API_ERROR: 1,
        },
        "refusal_entries": (entry("rec-c", LabelFailureReason.REFUSAL),),
        "failure_entries": (
            entry("rec-d", LabelFailureReason.PARSE_ERROR),
            entry("rec-e", LabelFailureReason.API_ERROR),
        ),
        "skipped_budget_record_ids": ("rec-f",),
        "fewshot_collision_record_ids": ("rec-g",),
    }
    fields.update(overrides)
    return LabelingReport(**fields)


def example(record_id: str) -> LabeledExample:
    return LabeledExample(
        record_id=record_id,
        taxonomy_id=TAXONOMY_V1.taxonomy_id,
        model_id="stub-model-served",
        verdict=STUB_VERDICT,
    )


def outcome(**overrides: object) -> LabelingOutcome:
    fields: dict = {
        "labeled_examples": (example("rec-a"), example("rec-b")),
        "report": report(),
    }
    fields.update(overrides)
    return LabelingOutcome(**fields)


# ------------------------------------------------------------------ happy paths


def test_valid_report_roundtrips_and_revalidates() -> None:
    original = report()
    restored = LabelingReport.model_validate_json(original.model_dump_json())
    assert restored == original


def test_valid_outcome_roundtrips_and_revalidates() -> None:
    original = outcome()
    restored = LabelingOutcome.model_validate_json(original.model_dump_json())
    assert restored == original


# --------------------------------------------------------- verdict + few-shots


def test_verdict_refuses_out_of_enum_labels() -> None:
    with pytest.raises(ValidationError):
        JudgeVerdict.model_validate(
            {"task_type": "banana", "outcome": "correct", "confidence": "high", "rationale": "r"}
        )


def test_verdict_refuses_empty_and_oversized_rationales() -> None:
    with pytest.raises(ValidationError):
        JudgeVerdict(
            task_type=TaskTypeLabel.OTHER,
            outcome=OutcomeLabel.CORRECT,
            confidence=JudgeConfidence.LOW,
            rationale="",
        )
    with pytest.raises(ValidationError):
        JudgeVerdict(
            task_type=TaskTypeLabel.OTHER,
            outcome=OutcomeLabel.CORRECT,
            confidence=JudgeConfidence.LOW,
            rationale="x" * (MAX_RATIONALE_LEN + 1),
        )


def test_judgment_requires_a_model_id() -> None:
    with pytest.raises(ValidationError):
        Judgment(verdict=STUB_VERDICT, model_id="")


def few_shot(input_text: str = "q?", output_text: str = "a.") -> FewShotExample:
    return FewShotExample(
        few_shot_id=derive_few_shot_id(input_text, output_text, STUB_VERDICT),
        input_text=input_text,
        output_text=output_text,
        verdict=STUB_VERDICT,
    )


def test_few_shot_id_is_content_derived_and_covers_the_verdict() -> None:
    shot = few_shot()
    assert shot.few_shot_id.startswith("fs-")
    other_verdict = JudgeVerdict(
        task_type=STUB_VERDICT.task_type,
        outcome=OutcomeLabel.INCORRECT,
        confidence=STUB_VERDICT.confidence,
        rationale=STUB_VERDICT.rationale,
    )
    # Editing the gold label is a DIFFERENT example — provenance can never silently
    # point at edited guidance.
    assert derive_few_shot_id("q?", "a.", other_verdict) != shot.few_shot_id


def test_tampered_few_shot_id_refuses() -> None:
    with pytest.raises(ValidationError, match="content-derived"):
        FewShotExample(
            few_shot_id="fs-0000000000000000",
            input_text="q?",
            output_text="a.",
            verdict=STUB_VERDICT,
        )


def test_content_hash_is_the_exact_dedup_identity_function() -> None:
    # The leakage-gate join key MUST equal the exact-dedup hash over canonical_text
    # (input ␟ output) — one identity function, three gates (ADR-0003 rule 8).
    shot = few_shot("in", "out")
    expected = hashlib.sha256(("in" + CANONICAL_SEP + "out").encode("utf-8")).hexdigest()
    assert shot.content_hash == expected


# ------------------------------------------------------------- the fingerprint


def test_fingerprint_refuses_non_hex_prompt_hash() -> None:
    for bad in ("abc", "Z" * 64, SHA.upper()):
        with pytest.raises(ValidationError, match="prompt_sha256"):
            JudgeFingerprint(
                judge_name="stub",
                model_id="m",
                taxonomy_id="tax-000000000000",
                prompt_sha256=bad,
            )


def test_fingerprint_refuses_id_hash_length_mismatch() -> None:
    with pytest.raises(ValidationError, match="exactly one id and one content hash"):
        JudgeFingerprint(
            judge_name="stub",
            model_id="m",
            taxonomy_id="tax-000000000000",
            prompt_sha256=SHA,
            few_shot_ids=("fs-0000000000000000",),
            few_shot_content_hashes=(),
        )


def test_fingerprint_refuses_unsorted_or_duplicate_few_shot_sets() -> None:
    with pytest.raises(ValidationError, match="sorted ascending and unique"):
        JudgeFingerprint(
            judge_name="stub",
            model_id="m",
            taxonomy_id="tax-000000000000",
            prompt_sha256=SHA,
            few_shot_ids=("fs-b", "fs-a"),
            few_shot_content_hashes=(SHA, hashlib.sha256(b"y").hexdigest()),
        )
    with pytest.raises(ValidationError, match="sorted ascending and unique"):
        JudgeFingerprint(
            judge_name="stub",
            model_id="m",
            taxonomy_id="tax-000000000000",
            prompt_sha256=SHA,
            few_shot_ids=("fs-a", "fs-a"),
            few_shot_content_hashes=sorted((SHA, hashlib.sha256(b"y").hexdigest())),
        )


# ------------------------------------------------- LabelingReport refuse cases


def test_buckets_that_do_not_sum_refuse() -> None:
    with pytest.raises(ValidationError, match="exactly one bucket"):
        report(records_in=8)


def test_refusal_entry_count_mismatch_refuses() -> None:
    with pytest.raises(ValidationError, match="refusal entries"):
        report(refusal_entries=())


def test_refusal_entry_with_wrong_reason_refuses() -> None:
    with pytest.raises(ValidationError, match="must all be 'refusal'"):
        report(refusal_entries=(entry("rec-c", LabelFailureReason.PARSE_ERROR),))


def test_failure_entry_count_mismatch_refuses() -> None:
    with pytest.raises(ValidationError, match="failure entries"):
        report(failure_entries=(entry("rec-d", LabelFailureReason.PARSE_ERROR),))


def test_failure_entry_with_refusal_reason_refuses() -> None:
    with pytest.raises(ValidationError, match="parse_error or api_error only"):
        report(
            failure_entries=(
                entry("rec-d", LabelFailureReason.PARSE_ERROR),
                entry("rec-e", LabelFailureReason.REFUSAL),
            )
        )


def test_failures_by_reason_sum_mismatch_refuses() -> None:
    with pytest.raises(ValidationError, match="failures_by_reason sums"):
        report(failures_by_reason={LabelFailureReason.PARSE_ERROR: 1})


def test_failures_by_reason_per_reason_mismatch_refuses() -> None:
    with pytest.raises(ValidationError, match="carry that reason"):
        report(failures_by_reason={LabelFailureReason.PARSE_ERROR: 2})


def test_failures_by_reason_refuses_the_refusal_key() -> None:
    with pytest.raises(ValidationError, match="separate bucket"):
        report(
            failures_by_reason={
                LabelFailureReason.REFUSAL: 1,
                LabelFailureReason.PARSE_ERROR: 1,
            }
        )


def test_budget_can_never_be_exceeded() -> None:
    # labeled + refused + failed = 5 judge calls > max_labels — a report claiming more
    # calls than its budget refuses to exist.
    with pytest.raises(ValidationError, match="never be exceeded"):
        report(max_labels=4)


def test_unsorted_failure_entries_refuse() -> None:
    with pytest.raises(ValidationError, match="sorted by record_id"):
        report(
            failure_entries=(
                entry("rec-e", LabelFailureReason.API_ERROR),
                entry("rec-d", LabelFailureReason.PARSE_ERROR),
            )
        )


def test_duplicate_budget_skip_ids_refuse() -> None:
    with pytest.raises(ValidationError, match="sorted ascending and unique"):
        report(
            records_in=8,
            skipped_budget=2,
            skipped_budget_record_ids=("rec-f", "rec-f"),
        )


def test_overlapping_id_buckets_refuse() -> None:
    with pytest.raises(ValidationError, match="pairwise disjoint"):
        report(fewshot_collision_record_ids=("rec-f",))


def test_zero_max_labels_refuses() -> None:
    with pytest.raises(ValidationError):
        report(max_labels=0)


# ------------------------------------------------ LabelingOutcome refuse cases


def test_labeled_count_mismatch_refuses() -> None:
    with pytest.raises(ValidationError, match="report.labeled"):
        outcome(labeled_examples=(example("rec-a"),))


def test_unsorted_labeled_examples_refuse() -> None:
    with pytest.raises(ValidationError, match="sorted by record_id"):
        outcome(labeled_examples=(example("rec-b"), example("rec-a")))


def test_labeled_id_overlapping_a_report_bucket_refuses() -> None:
    # rec-c is the report's refusal — it cannot ALSO be labeled.
    with pytest.raises(ValidationError, match="exactly one bucket"):
        outcome(labeled_examples=(example("rec-a"), example("rec-c")))


def test_taxonomy_mismatch_refuses() -> None:
    alien = LabeledExample(
        record_id="rec-b",
        taxonomy_id="tax-ffffffffffff",
        model_id="stub-model-served",
        verdict=STUB_VERDICT,
    )
    with pytest.raises(ValidationError, match="different"):
        outcome(labeled_examples=(example("rec-a"), alien))
