"""Phase 1 invariants: the self-validating IngestReport (ADR-0001 rule 4).

A report that cannot account for every single line must refuse to exist — the
denominator of every published dataset depends on it.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from evalgen.contracts import (
    IngestReport,
    RejectReason,
    RejectSample,
    SkipReason,
    SourceKind,
)


def make_report(**overrides: object) -> IngestReport:
    fields: dict = {
        "source_kind": SourceKind.TRACESPAN,
        "source_name": "demo.jsonl",
        "lines_read": 6,
        "records_normalized": 3,
        "lines_rejected": 2,
        "lines_skipped": 1,
        "rejects_by_reason": {RejectReason.INVALID_JSON: 1, RejectReason.SCHEMA_MISMATCH: 1},
        "skips_by_reason": {SkipReason.BLANK_LINE: 1},
        "reject_samples": (
            RejectSample(line_no=2, reason=RejectReason.INVALID_JSON, detail="Expecting value"),
        ),
    }
    fields.update(overrides)
    return IngestReport(**fields)


def test_valid_report_constructs_and_round_trips() -> None:
    report = make_report()
    assert IngestReport.model_validate_json(report.model_dump_json()) == report


def test_report_refuses_buckets_that_do_not_sum() -> None:
    # The headline invariant: lines_read == normalized + rejected + skipped.
    with pytest.raises(ValidationError, match="exactly one bucket"):
        make_report(lines_read=7)


def test_report_refuses_wrong_reject_reason_sum() -> None:
    with pytest.raises(ValidationError, match="rejects_by_reason"):
        make_report(rejects_by_reason={RejectReason.INVALID_JSON: 1})


def test_report_refuses_wrong_skip_reason_sum() -> None:
    with pytest.raises(ValidationError, match="skips_by_reason"):
        make_report(skips_by_reason={SkipReason.BLANK_LINE: 2})


def test_report_refuses_more_samples_than_rejections() -> None:
    samples = tuple(
        RejectSample(line_no=i, reason=RejectReason.INVALID_JSON, detail="x") for i in (1, 2, 3)
    )
    with pytest.raises(ValidationError, match="reject samples"):
        make_report(reject_samples=samples)


def test_report_caps_samples_at_twenty() -> None:
    # The cap is structural (a field constraint), not a courtesy of the builder.
    samples = tuple(
        RejectSample(line_no=i, reason=RejectReason.INVALID_JSON, detail="x") for i in range(1, 22)
    )
    with pytest.raises(ValidationError):
        make_report(
            lines_read=25,
            records_normalized=3,
            lines_rejected=21,
            lines_skipped=1,
            rejects_by_reason={RejectReason.INVALID_JSON: 21},
            reject_samples=samples,
        )


def test_report_is_frozen() -> None:
    report = make_report()
    with pytest.raises(ValidationError):
        report.lines_read = 99  # type: ignore[misc]


def test_empty_source_report_is_valid() -> None:
    report = IngestReport(
        source_kind=SourceKind.GENERIC_JSONL,
        source_name="empty.jsonl",
        lines_read=0,
        records_normalized=0,
        lines_rejected=0,
        lines_skipped=0,
    )
    assert report.reject_samples == ()
