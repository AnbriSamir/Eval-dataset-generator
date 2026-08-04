"""Generic JSONL adapter on the committed fixture (ADR-0001 rule 5).

The mapping is EXPLICIT: dot-paths into a support-chatbot log with nested metadata.
Counts are hand-derived from the fixture; nothing may pass through undeclared.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from evalgen.contracts import LogRecord, RejectReason, SkipReason, SourceKind
from evalgen.ingest import GenericMapping, load_generic_jsonl

FIXTURE = Path(__file__).resolve().parent.parent / "data" / "fixtures" / "generic_demo.jsonl"

MAPPING = GenericMapping(
    input_key="q",
    output_key="a",
    timestamp_key="meta.ts",
    id_key="meta.id",
    task_key="meta.conv",
    metadata_keys=("channel", "lang"),
)


def load() -> tuple[list[LogRecord], object]:
    return load_generic_jsonl(FIXTURE, MAPPING)


# ------------------------------------------------------------------------- accounting


def test_report_counts_are_exact() -> None:
    _, report = load()
    assert report.source_kind is SourceKind.GENERIC_JSONL
    assert report.lines_read == 8
    assert report.records_normalized == 3
    assert report.lines_rejected == 3
    assert report.lines_skipped == 2
    assert report.rejects_by_reason == {
        RejectReason.INVALID_JSON: 1,  # line 6: not JSON
        RejectReason.SCHEMA_MISMATCH: 1,  # line 7: JSON scalar
        RejectReason.MISSING_FIELD: 1,  # line 4: mapped output key absent
    }
    assert report.skips_by_reason == {
        SkipReason.BLANK_LINE: 1,  # line 8
        SkipReason.NO_EXCHANGE: 1,  # line 5: empty question
    }


def test_missing_mapped_key_is_a_typed_rejection_with_evidence() -> None:
    _, report = load()
    sample = next(s for s in report.reject_samples if s.reason is RejectReason.MISSING_FIELD)
    assert sample.line_no == 4
    assert "a" in sample.detail  # names the absent key, not the raw line


def test_double_load_is_byte_identical() -> None:
    records_a, report_a = load()
    records_b, report_b = load()
    assert [r.model_dump_json() for r in records_a] == [r.model_dump_json() for r in records_b]
    assert report_a.model_dump_json() == report_b.model_dump_json()


# ------------------------------------------------------------------------- timestamps


def test_iso_timestamp_is_parsed() -> None:
    records, _ = load()
    first = {r.origin.line_no: r for r in records}[1]
    assert first.timestamp == datetime(2026, 6, 9, 10, 30, tzinfo=UTC)


def test_bad_clock_demotes_to_none_and_warns_never_drops() -> None:
    # Line 3 says "yesterday": the record SURVIVES with timestamp=None (dropping it
    # would bias the sampled distribution) and the report carries the warning.
    records, report = load()
    third = {r.origin.line_no: r for r in records}[3]
    assert third.timestamp is None
    assert report.timestamps_unparsed == 1


# ------------------------------------------------------------------ mapping semantics


def test_dot_paths_resolve_into_nested_objects() -> None:
    records, _ = load()
    first = {r.origin.line_no: r for r in records}[1]
    assert first.origin.span_id == "evt-0101"  # meta.id
    assert first.origin.task_id == "conv-70"  # meta.conv


def test_metadata_is_opt_in_only() -> None:
    # No implicit "take everything": exactly the declared metadata_keys survive.
    records, _ = load()
    for record in records:
        assert set(record.metadata) == {"channel", "lang"}


def test_empty_exchange_is_skipped_not_normalized() -> None:
    records, _ = load()
    assert 5 not in {r.origin.line_no for r in records}


def test_invisible_only_field_is_one_skip_never_a_crash(tmp_path: Path) -> None:
    # RED-TEAM MAJOR, replayed verbatim: raw str.strip() does not see U+200B
    # ("\u200b".isspace() is False), so the middle line used to pass the raw
    # candidacy check, normalize to "" inside build_record, and detonate the WHOLE
    # file via the loader-bug ValueError. Candidacy now runs on sanitized text: one
    # crafted (or accidental) line costs exactly one no_exchange skip and every
    # other record survives.
    lines = [
        {"q": "real question one", "a": "real answer one"},
        {"q": "\u200b\u200b", "a": "answer two"},
        {"q": "real question three", "a": "real answer three"},
    ]
    path = tmp_path / "invisible_field.jsonl"
    path.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    records, report = load_generic_jsonl(path, GenericMapping(input_key="q", output_key="a"))
    assert [r.origin.line_no for r in records] == [1, 3]
    assert report.lines_read == 3
    assert report.records_normalized == 2
    assert report.skips_by_reason == {SkipReason.NO_EXCHANGE: 1}


def test_wider_invisible_family_only_field_is_also_a_skip(tmp_path: Path) -> None:
    # Same class, post-BLOCKER-fix members: word joiner + soft hyphen only. The
    # category-based strip makes MORE fields normalize to empty — every one of them
    # must be a skip, not a crash (the MAJOR fix must cover the whole family, not
    # just the four zero-width code points that happened to be in the old table).
    line = {"q": "\u2060\u00ad", "a": "answer"}
    path = tmp_path / "invisible_family.jsonl"
    path.write_text(json.dumps(line) + "\n", encoding="utf-8")
    records, report = load_generic_jsonl(path, GenericMapping(input_key="q", output_key="a"))
    assert records == []
    assert report.skips_by_reason == {SkipReason.NO_EXCHANGE: 1}


# -------------------------------------------------------------------------- redaction


def test_word_joiner_split_secret_never_persists_end_to_end(tmp_path: Path) -> None:
    # RED-TEAM BLOCKER, end to end: the leak was NOT display-only — the U+2060-split
    # key used to persist into record.input_text, hence into the content hash, the
    # embedding, the judge prompt, the export, and (via scrubbed reject details) the
    # report. Nothing leaving ingest may carry the key, either half, or the joiner.
    line = {
        "q": "api_key was sk-ant-abc1234567\u2060Zx9yW8vU7tS6rQ5pO4nM3lK2wXyZ today",
        "a": "rotated it",
    }
    path = tmp_path / "wj_attack.jsonl"
    path.write_text(json.dumps(line) + "\n", encoding="utf-8")
    records, report = load_generic_jsonl(path, GenericMapping(input_key="q", output_key="a"))
    assert len(records) == 1
    assert "[REDACTED:api_key]" in records[0].input_text
    everything = "\n".join(r.model_dump_json() for r in records) + report.model_dump_json()
    assert "sk-ant-abc1234567" not in everything
    assert "Zx9yW8vU7tS6rQ5pO4nM3lK2wXyZ" not in everything
    assert "\u2060" not in everything


def test_planted_pii_is_redacted_on_the_fixture() -> None:
    records, report = load()
    second = {r.origin.line_no: r for r in records}[2]
    assert "AKIAIOSFODNN7EXAMPLE" not in second.input_text
    assert "[REDACTED:aws_key]" in second.input_text
    assert "jdupont" not in second.input_text  # Windows user path's username segment
    assert "[REDACTED:user_path]" in second.input_text
    everything = "\n".join(r.model_dump_json() for r in records) + report.model_dump_json()
    assert "AKIAIOSFODNN7EXAMPLE" not in everything
    assert "jdupont" not in everything


def test_accented_french_content_survives_normalization() -> None:
    # NFKC must not mangle legitimate accented text (synthèses stays synthèses).
    records, _ = load()
    first = {r.origin.line_no: r for r in records}[1]
    assert "synthèses" in first.input_text
    assert "année" in first.output_text and "considéré" in first.output_text
