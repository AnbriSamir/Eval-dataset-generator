"""TraceSpan adapter on the committed fixture (ADR-0001 rules 4-5).

The fixture mirrors the sibling repo's REAL span shapes (graph/builder.py): control-flow
spans, decision spans carrying exchanges, error/blocked spans, planted secrets, and
deliberately malformed lines. Every expected count below is hand-derived from the
fixture — a drift in either the fixture or the loader fails loudly.
"""

from __future__ import annotations

import json
from pathlib import Path

from evalgen.contracts import RejectReason, SkipReason, SourceKind
from evalgen.ingest import load_tracespan_jsonl

FIXTURE = Path(__file__).resolve().parent.parent / "data" / "fixtures" / "tracespans_demo.jsonl"

# Planted in the fixture; none of these strings may survive ingestion anywhere.
PLANTED_SECRETS = (
    "sk-ant-api03-Zx9yW8vU7tS6rQ5pO4nM3lK2",
    "XyZ0123456789012345",  # the zero-width-split key's tail
    "jean.dupont@acme-corp.fr",
    "claire.martin@other-corp.com",
    "+33 6 12 34 56 78",
)


def load() -> tuple:
    return load_tracespan_jsonl(FIXTURE)


# ------------------------------------------------------------------------- accounting


def test_report_counts_are_exact() -> None:
    _, report = load()
    assert report.source_kind is SourceKind.TRACESPAN
    assert report.lines_read == 16
    assert report.records_normalized == 6
    assert report.lines_rejected == 4
    assert report.lines_skipped == 6
    assert report.rejects_by_reason == {
        RejectReason.INVALID_ENCODING: 1,  # line 16: raw 0xFF byte
        RejectReason.INVALID_JSON: 1,  # line 12: truncated JSON
        RejectReason.SCHEMA_MISMATCH: 2,  # line 13: array · line 14: unknown status
    }
    assert report.skips_by_reason == {
        SkipReason.BLANK_LINE: 1,  # line 15
        SkipReason.ACTION_NOT_CANDIDATE: 2,  # lines 1-2: intake, retrieve
        SkipReason.STATUS_NOT_OK: 2,  # lines 7-8: error, blocked
        SkipReason.NO_EXCHANGE: 1,  # line 9: bookkeeping-only payload
    }
    assert report.timestamps_unparsed == 0


def test_malformed_lines_are_counted_never_silent() -> None:
    # One corrupt byte must cost exactly one line — and leave a typed trace.
    _, report = load()
    assert [s.line_no for s in report.reject_samples] == [12, 13, 14, 16]  # file order
    by_line = {s.line_no: s for s in report.reject_samples}
    assert by_line[16].reason is RejectReason.INVALID_ENCODING
    assert by_line[12].reason is RejectReason.INVALID_JSON


def test_records_come_out_in_file_order() -> None:
    records, _ = load()
    assert [r.origin.line_no for r in records] == [3, 4, 5, 6, 10, 11]


def test_double_load_is_byte_identical() -> None:
    # Same inputs -> byte-identical outputs: no wall clock, no uuid4, no dict-order
    # nondeterminism anywhere in the path.
    records_a, report_a = load()
    records_b, report_b = load()
    assert [r.model_dump_json() for r in records_a] == [r.model_dump_json() for r in records_b]
    assert report_a.model_dump_json() == report_b.model_dump_json()


# -------------------------------------------------------------------------- redaction


def test_no_planted_secret_survives_anywhere() -> None:
    # Records AND report (reject-sample details embed parse errors which embed raw
    # lines): nothing that leaves ingest may carry a planted secret.
    records, report = load()
    everything = "\n".join(r.model_dump_json() for r in records) + report.model_dump_json()
    for secret in PLANTED_SECRETS:
        assert secret not in everything


def test_planted_secrets_got_their_precise_categories() -> None:
    records, _ = load()
    by_line = {r.origin.line_no: r for r in records}
    assert "[REDACTED:email]" in by_line[4].output_text
    assert "[REDACTED:api_key]" in by_line[5].input_text
    assert "[REDACTED:api_key]" in by_line[10].input_text  # zero-width-split key
    assert "[REDACTED:phone]" in by_line[10].output_text


def test_semantic_content_survives_redaction() -> None:
    # Over-redaction is the chosen failure direction, but the exchange must remain
    # judgeable — not "[REDACTED] soup" (labels and clusters are computed over this).
    records, _ = load()
    by_line = {r.origin.line_no: r for r in records}
    assert "incident de collecte" in by_line[4].input_text
    assert "comptes à prévenir" in by_line[4].output_text
    assert "export du fichier compteurs" in by_line[5].input_text


def test_post_redaction_duplicates_collapse_to_the_same_canonical_text() -> None:
    # Lines 4 and 11 differ ONLY by their planted email (and metadata): after
    # redaction they are the same eval case. Distinct ids (origin is identity — the
    # dedup report needs both handles), identical canonical_text (dedup collapses).
    records, _ = load()
    by_line = {r.origin.line_no: r for r in records}
    assert by_line[4].canonical_text == by_line[11].canonical_text
    assert by_line[4].record_id != by_line[11].record_id


# ------------------------------------------------------------------ provenance/policy


def test_origin_and_metadata_carry_the_span_signals() -> None:
    records, _ = load()
    plan = {r.origin.line_no: r for r in records}[3]
    assert plan.origin.span_id == "span-c3d4e5f60718"
    assert plan.origin.task_id == "task-4f7a2b91c6d0"
    assert plan.metadata["agent"] == "supervisor"
    assert plan.metadata["action"] == "plan"
    assert plan.metadata["status"] == "ok"
    assert plan.metadata["model_id"] == "claude-opus-4-8"
    assert plan.metadata["tokens_in"] == "786"
    assert plan.metadata["cost_usd"] == "0.0214"


def test_absent_optional_signals_are_omitted_not_stringified_none() -> None:
    records, _ = load()
    zws_span = {r.origin.line_no: r for r in records}[10]  # fixture omits model_id etc.
    assert "model_id" not in zws_span.metadata


def test_source_name_is_the_basename_never_the_path() -> None:
    records, report = load()
    assert report.source_name == "tracespans_demo.jsonl"
    assert all(r.origin.source_name == "tracespans_demo.jsonl" for r in records)


def test_timestamp_is_always_none_for_tracespans() -> None:
    # TraceSpan carries no clock field; inventing one from the wall clock would break
    # byte-identical re-ingestion.
    records, _ = load()
    assert all(r.timestamp is None for r in records)


def test_error_and_blocked_spans_are_never_mined() -> None:
    records, _ = load()
    assert all(r.metadata["status"] == "ok" for r in records)


def test_candidate_actions_are_configurable_not_hardcoded() -> None:
    # Narrowing the allowlist to execute-only reroutes the plan/verdict spans (lines
    # 3, 6 — and the blocked verdict on line 8, which now fails the action check
    # BEFORE the status check) into action_not_candidate: 2 control-flow + 3 = 5.
    records, report = load_tracespan_jsonl(FIXTURE, candidate_actions={"execute"})
    assert [r.origin.line_no for r in records] == [4, 5, 10, 11]
    assert report.skips_by_reason[SkipReason.ACTION_NOT_CANDIDATE] == 5
    assert report.lines_read == 16  # accounting still sums


def test_source_name_override_is_honored_and_scrubbed() -> None:
    _, report = load_tracespan_jsonl(FIXTURE, source_name="orchestrator-run-17")
    assert report.source_name == "orchestrator-run-17"


def test_invisible_only_payload_field_is_a_skip_never_a_crash(tmp_path: Path) -> None:
    # Symmetry guard for the red-team MAJOR (found in the generic adapter): this
    # adapter is immune because _extract_text sanitizes BEFORE its empty check — an
    # invisible-only "input" normalizes to "" and the span is one no_exchange skip.
    # Pinned so a refactor to a raw str.strip() check cannot reintroduce the crash.
    span = {
        "span_id": "span-000000000001",
        "task_id": "task-000000000001",
        "agent": "worker",
        "action": "execute",
        "status": "ok",
        "payload": {"input": "\u2060\u200b", "output": "an answer"},
    }
    path = tmp_path / "invisible_payload.jsonl"
    path.write_text(json.dumps(span) + "\n", encoding="utf-8")
    records, report = load_tracespan_jsonl(path)
    assert records == []
    assert report.skips_by_reason == {SkipReason.NO_EXCHANGE: 1}
    assert report.lines_read == 1
