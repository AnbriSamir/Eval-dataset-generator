"""run_labeling: canonical order, five buckets, the budget in one place (ADR-0003 rule 6).

All judges here are test-local stubs (conftest) — the FakeJudge never raises, so every
failure path is exercised through scripted stubs, never production trigger tokens.
"""

from __future__ import annotations

import hashlib
import random

import pytest

from conftest import RaisingJudge, StubJudge, make_record
from evalgen.contracts import TAXONOMY_V1, LabelFailureReason, LogRecord
from evalgen.label import run_labeling
from evalgen.label.errors import JudgeAPIError, JudgeParseError, JudgeRefusalError


def records(n: int) -> list[LogRecord]:
    # line_no drives record_sort_key -> canonical order == r1, r2, ... rn.
    return [make_record(line_no=i, input_text=f"question {i} ?") for i in range(1, n + 1)]


def canonical_hash(record: LogRecord) -> str:
    return hashlib.sha256(record.canonical_text.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ happy path


def test_happy_path_labels_everything_and_sums_hold() -> None:
    recs = records(3)
    judge = StubJudge()
    outcome = run_labeling(recs, judge=judge, max_labels=10)
    report = outcome.report
    assert report.records_in == 3
    assert report.labeled == 3
    assert report.refused == report.failed == 0
    assert report.skipped_budget == report.skipped_fewshot_collision == 0
    assert len(outcome.labeled_examples) == 3
    for example in outcome.labeled_examples:
        # model id ACTUALLY served (from the Judgment), not the config echo.
        assert example.model_id == "stub-model-served"
        assert example.taxonomy_id == TAXONOMY_V1.taxonomy_id
    assert len(judge.calls) == 3


# ------------------------------------------------------------- failure buckets


def test_each_failure_kind_is_typed_counted_and_the_run_continues() -> None:
    recs = records(4)
    judge = StubJudge(
        errors={
            recs[0].input_text: JudgeRefusalError("declined"),
            recs[1].input_text: JudgeParseError("truncated"),
            recs[2].input_text: JudgeAPIError("HTTP 529"),
        }
    )
    outcome = run_labeling(recs, judge=judge, max_labels=10)
    report = outcome.report
    assert report.labeled == 1
    assert report.refused == 1
    assert report.failed == 2
    assert report.failures_by_reason == {
        LabelFailureReason.PARSE_ERROR: 1,
        LabelFailureReason.API_ERROR: 1,
    }
    assert report.refusal_entries[0].record_id == recs[0].record_id
    assert report.refusal_entries[0].detail == "declined"
    reasons = {e.record_id: e.reason for e in report.failure_entries}
    assert reasons == {
        recs[1].record_id: LabelFailureReason.PARSE_ERROR,
        recs[2].record_id: LabelFailureReason.API_ERROR,
    }


def test_failure_details_are_truncated_to_the_contract_bound() -> None:
    recs = records(1)
    judge = StubJudge(errors={recs[0].input_text: JudgeRefusalError("x" * 500)})
    outcome = run_labeling(recs, judge=judge, max_labels=10)
    assert len(outcome.report.refusal_entries[0].detail) == 200


# ------------------------------------------------------------------ the budget


def test_budget_counts_judge_calls_and_cuts_in_canonical_order() -> None:
    recs = records(7)
    # One refusal among the first 4 (canonical order): the refusal SPENDS budget.
    judge = StubJudge(errors={recs[1].input_text: JudgeRefusalError("declined")})
    outcome = run_labeling(recs, judge=judge, max_labels=4)
    report = outcome.report
    assert len(judge.calls) == 4  # exactly max_labels judge calls, never more
    assert report.labeled == 3
    assert report.refused == 1
    assert report.skipped_budget == 3
    assert report.skipped_budget_record_ids == tuple(sorted(r.record_id for r in recs[4:]))


def test_budget_cut_is_input_order_invariant() -> None:
    recs = records(7)
    shuffled = list(recs)
    random.Random(7).shuffle(shuffled)
    a = run_labeling(recs, judge=StubJudge(), max_labels=4)
    b = run_labeling(shuffled, judge=StubJudge(), max_labels=4)
    # Byte-identical outcome: the SAME records are labeled no matter how the caller
    # assembled the list (record_sort_key, ADR-0002 rule 1).
    assert a.model_dump_json() == b.model_dump_json()


def test_non_positive_budget_refuses_naming_the_knob() -> None:
    with pytest.raises(ValueError, match="max_labels"):
        run_labeling(records(1), judge=StubJudge(), max_labels=0)


# -------------------------------------------------------- the fewshot collision


def test_collision_is_skipped_and_consumes_no_budget() -> None:
    recs = records(7)
    planted = recs[2]
    judge = StubJudge(few_shot_content_hashes=(canonical_hash(planted),))
    # Budget == the 6 non-colliding records: if the collision consumed budget, one of
    # them would land in skipped_budget instead of labeled.
    outcome = run_labeling(recs, judge=judge, max_labels=6)
    report = outcome.report
    assert report.skipped_fewshot_collision == 1
    assert report.fewshot_collision_record_ids == (planted.record_id,)
    assert report.labeled == 6
    assert report.skipped_budget == 0
    # The judge NEVER saw the colliding record — it can never enter the κ join.
    assert planted.input_text not in [call[0] for call in judge.calls]


# --------------------------------------------------------------- caller errors


def test_duplicate_record_ids_refuse() -> None:
    recs = records(2)
    with pytest.raises(ValueError, match="post-dedup"):
        run_labeling(recs + [recs[0]], judge=StubJudge(), max_labels=10)


def test_non_judge_errors_propagate_instead_of_becoming_statistics() -> None:
    # An AttributeError is OUR bug — it must crash the run, never be laundered into an
    # api_error count (ADR-0003 options §3).
    with pytest.raises(AttributeError, match="boom"):
        run_labeling(records(2), judge=RaisingJudge(AttributeError("boom")), max_labels=10)
