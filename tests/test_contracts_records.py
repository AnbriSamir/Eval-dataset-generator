"""Phase 1 invariants: the LogRecord atom (ADR-0001 rules 1-2).

Ids must be pure functions of (origin, redacted exchange): stable across runs, distinct
across origins, immune to timestamp/metadata noise, and self-verifying so a record
whose id disagrees with its content cannot exist — even deserialized from disk.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from evalgen.contracts import (
    CANONICAL_SEP,
    LogRecord,
    RecordOrigin,
    SourceKind,
    derive_record_id,
)

ORIGIN = RecordOrigin(
    source_kind=SourceKind.TRACESPAN,
    source_name="demo.jsonl",
    line_no=7,
    span_id="span-abc123def456",
    task_id="task-9c81d2e4f305",
)


def make_record(**overrides: object) -> LogRecord:
    fields: dict = {
        "origin": ORIGIN,
        "input_text": "What is the capital of France?",
        "output_text": "Paris.",
    }
    fields.update(overrides)
    fields.setdefault(
        "record_id",
        derive_record_id(fields["origin"], fields["input_text"], fields["output_text"]),
    )
    return LogRecord(**fields)


# --------------------------------------------------------------------- derive_record_id


def test_id_format_is_rec_plus_16_hex() -> None:
    rid = derive_record_id(ORIGIN, "in", "out")
    assert rid.startswith("rec-")
    assert len(rid) == len("rec-") + 16
    int(rid[4:], 16)  # the suffix is valid hex


def test_id_is_deterministic() -> None:
    assert derive_record_id(ORIGIN, "in", "out") == derive_record_id(ORIGIN, "in", "out")


@pytest.mark.parametrize(
    "origin_override",
    [
        {"source_kind": SourceKind.GENERIC_JSONL},
        {"source_name": "other.jsonl"},
        {"line_no": 8},
    ],
)
def test_id_changes_with_every_identity_part(origin_override: dict) -> None:
    other = ORIGIN.model_copy(update=origin_override)
    assert derive_record_id(other, "in", "out") != derive_record_id(ORIGIN, "in", "out")


def test_id_changes_with_texts() -> None:
    base = derive_record_id(ORIGIN, "in", "out")
    assert derive_record_id(ORIGIN, "in2", "out") != base
    assert derive_record_id(ORIGIN, "in", "out2") != base


def test_id_ignores_span_and_task_ids() -> None:
    # span_id/task_id are provenance, not identity: the ADR formula hashes exactly
    # five parts. A span id embedded in identity would make generic re-exports of the
    # same exchange non-idempotent.
    other = ORIGIN.model_copy(update={"span_id": None, "task_id": None})
    assert derive_record_id(other, "in", "out") == derive_record_id(ORIGIN, "in", "out")


def test_separator_prevents_boundary_shift_collisions() -> None:
    # Without the unit separator, ("ab","c") and ("a","bc") would hash identically.
    assert derive_record_id(ORIGIN, "ab", "c") != derive_record_id(ORIGIN, "a", "bc")


# ------------------------------------------------------------------------- LogRecord


def test_validator_rejects_forged_id() -> None:
    with pytest.raises(ValidationError, match="content-derived"):
        make_record(record_id="rec-0000000000000000")


def test_validator_rejects_tampered_deserialization() -> None:
    # Tamper detection for free: edit a field in the serialized form and the
    # round-trip refuses — provenance cannot be silently rewritten on disk.
    dumped = json.loads(make_record().model_dump_json())
    dumped["input_text"] = "tampered question"
    with pytest.raises(ValidationError, match="content-derived"):
        LogRecord.model_validate(dumped)


def test_round_trip_is_lossless() -> None:
    record = make_record(
        timestamp=datetime(2026, 5, 14, 9, 30, tzinfo=UTC),
        metadata={"agent": "supervisor", "action": "plan"},
    )
    assert LogRecord.model_validate_json(record.model_dump_json()) == record


def test_record_is_frozen() -> None:
    record = make_record()
    with pytest.raises(ValidationError):
        record.input_text = "mutated"  # type: ignore[misc]


def test_empty_exchange_is_unrepresentable() -> None:
    with pytest.raises(ValidationError):
        make_record(input_text="")
    with pytest.raises(ValidationError):
        make_record(output_text="")


def test_timestamp_never_defaults_to_wall_clock() -> None:
    # A wall-clock default would make two runs over the same file differ — the
    # timestamp is None unless the SOURCE carried one.
    assert make_record().timestamp is None


def test_timestamp_and_metadata_do_not_change_identity() -> None:
    plain = make_record()
    decorated = make_record(
        timestamp=datetime(2026, 5, 14, tzinfo=UTC),
        metadata={"agent": "reviewer"},
    )
    assert plain.record_id == decorated.record_id


# -------------------------------------------------------------------- canonical texts


def test_canonical_text_joins_on_unit_separator() -> None:
    record = make_record()
    assert record.canonical_text == record.input_text + CANONICAL_SEP + record.output_text


def test_cluster_text_is_the_input_side() -> None:
    # Coverage is defined by incoming traffic, not by output phrasing.
    assert make_record().cluster_text == "What is the capital of France?"
