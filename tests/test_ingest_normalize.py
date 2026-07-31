"""The single-constructor discipline: normalize -> redact -> derive-id -> freeze.

``build_record`` is the half of the id invariant the contracts validator cannot check
(that redaction RAN before hashing) — so it is proven here, explicitly.
"""

from __future__ import annotations

import pytest

from evalgen.contracts import RejectReason, SourceKind, derive_record_id
from evalgen.ingest import ReportBuilder, build_record


def test_hash_after_redaction_proven() -> None:
    # THE Phase 1 invariant. The id must be derived from the REDACTED text: an id over
    # raw text would leak secret bits into a published value and rotate whenever the
    # secret does.
    raw_input = "Retry with api_key=sk-ant-api03-Zx9yW8vU7tS6rQ5pO4nM3lK2 please."
    record = build_record(
        source_kind=SourceKind.TRACESPAN,
        source_name="demo.jsonl",
        line_no=5,
        input_text=raw_input,
        output_text="Done.",
    )
    origin = record.origin
    # The id matches the redacted content...
    assert record.record_id == derive_record_id(origin, record.input_text, record.output_text)
    # ...and does NOT match anything derived from the raw secret-bearing text.
    assert record.record_id != derive_record_id(origin, raw_input, "Done.")
    assert "sk-ant" not in record.input_text
    assert "[REDACTED:api_key]" in record.input_text


def test_same_id_when_only_the_secret_differs() -> None:
    # Constant placeholders make two records differing only in their secrets exact
    # duplicates: same id here, and dedup will collapse them downstream — correct,
    # they ARE the same eval case.
    common: dict = {
        "source_kind": SourceKind.TRACESPAN,
        "source_name": "demo.jsonl",
        "line_no": 5,
        "output_text": "Done.",
    }
    a = build_record(input_text="use sk-ant-aaaaaaaaaaaaaaaaaaaa now", **common)
    b = build_record(input_text="use sk-ant-bbbbbbbbbbbbbbbbbbbb now", **common)
    assert a.record_id == b.record_id
    assert a.input_text == b.input_text


def test_empty_after_sanitization_is_a_loader_bug_not_a_record() -> None:
    # An exchange that is only zero-width characters normalizes to nothing — loaders
    # must have skipped it as no_exchange; build_record refuses to paper over it.
    with pytest.raises(ValueError, match="no_exchange"):
        build_record(
            source_kind=SourceKind.TRACESPAN,
            source_name="demo.jsonl",
            line_no=1,
            input_text="​​",
            output_text="ok",
        )


def test_metadata_is_stringified_scrubbed_and_key_sorted() -> None:
    record = build_record(
        source_kind=SourceKind.TRACESPAN,
        source_name="demo.jsonl",
        line_no=1,
        input_text="q",
        output_text="a",
        metadata={
            "tokens_in": 812,
            "contact": "jean.dupont@acme-corp.fr",
            "sk-ant-abcdefghij0123456789": "a secret-bearing KEY",
        },
    )
    # Stringified + scrubbed, keys included; sorted so serialization never depends on
    # the caller's insertion order.
    assert record.metadata == {
        "[REDACTED:api_key]": "a secret-bearing KEY",
        "contact": "[REDACTED:email]",
        "tokens_in": "812",
    }
    assert list(record.metadata) == sorted(record.metadata)


def test_source_name_cannot_smuggle_an_absolute_path() -> None:
    # Loaders pass basenames, but a caller handing over a full path must not leak a
    # username into provenance either.
    record = build_record(
        source_kind=SourceKind.GENERIC_JSONL,
        source_name=r"C:\Users\samir\logs\prod.jsonl",
        line_no=1,
        input_text="q",
        output_text="a",
    )
    assert "samir" not in record.origin.source_name
    assert "[REDACTED:user_path]" in record.origin.source_name


def test_native_ids_are_scrubbed_too() -> None:
    record = build_record(
        source_kind=SourceKind.GENERIC_JSONL,
        source_name="demo.jsonl",
        line_no=1,
        input_text="q",
        output_text="a",
        span_id="evt-jean.dupont@acme-corp.fr",
        task_id="conv-42",
    )
    # The email pattern's local part legitimately eats the "evt-" prefix too
    # (over-redaction is the chosen failure direction) — what matters is: no PII.
    assert record.origin.span_id == "[REDACTED:email]"
    assert record.origin.task_id == "conv-42"


# ---------------------------------------------------------------------- ReportBuilder


def test_builder_sums_by_construction_and_caps_samples() -> None:
    builder = ReportBuilder(source_kind=SourceKind.TRACESPAN, source_name="demo.jsonl")
    for i in range(1, 26):
        builder.reject(i, RejectReason.INVALID_JSON, f"bad line {i}")
    builder.normalized()
    report = builder.build()
    assert report.lines_read == 26
    assert report.lines_rejected == 25
    assert len(report.reject_samples) == 20  # capped, first-in-file order
    assert [s.line_no for s in report.reject_samples] == list(range(1, 21))


def test_builder_scrubs_and_truncates_reject_details() -> None:
    # Parse-error messages embed the raw line (pydantic prints input_value=...) — the
    # redactor must run on them BEFORE truncation.
    builder = ReportBuilder(source_kind=SourceKind.TRACESPAN, source_name="demo.jsonl")
    detail = "input_value contained api_key=sk-ant-api03-Zx9yW8vU7tS6rQ5pO4nM3lK2 " + "x" * 300
    builder.reject(3, RejectReason.SCHEMA_MISMATCH, detail)
    sample = builder.build().reject_samples[0]
    assert "sk-ant" not in sample.detail
    assert len(sample.detail) <= 200
