"""Exact dedup: the survivor rule, the REAL fixture twin pair, id-collapse idempotence.

The tracespan fixture's lines 4 and 11 differ only in their email addresses — both
redact to the same placeholder, so their canonical_texts are byte-equal while their
record_ids differ (ADR-0002 measured ground truth). The survivor must be line 4 (the
record_sort_key minimum), regardless of input order.
"""

from __future__ import annotations

import random
from pathlib import Path

from conftest import StubEmbedder, make_record
from evalgen.contracts import record_sort_key
from evalgen.dedup import run_dedup
from evalgen.dedup.exact import content_hash, exact_dedup
from evalgen.ingest import load_tracespan_jsonl

FIXTURE = Path(__file__).resolve().parent.parent / "data" / "fixtures" / "tracespans_demo.jsonl"


def test_fixture_redaction_twins_collapse_to_line_4() -> None:
    records, _ = load_tracespan_jsonl(FIXTURE)
    result = exact_dedup(records)
    assert result.id_collapsed == 0
    assert len(result.entries) == 1
    entry = result.entries[0]
    by_id = {r.record_id: r for r in records}
    assert by_id[entry.kept_record_id].origin.line_no == 4
    assert by_id[entry.dropped_record_id].origin.line_no == 11
    assert entry.content_hash == content_hash(by_id[entry.kept_record_id])
    assert len(result.kept) == len(records) - 1


def test_survivor_rule_is_input_order_independent() -> None:
    records, _ = load_tracespan_jsonl(FIXTURE)
    baseline = exact_dedup(records)
    for seed in (1, 7, 42):
        shuffled = list(records)
        random.Random(seed).shuffle(shuffled)
        result = exact_dedup(shuffled)
        assert result == baseline


def test_kept_records_come_out_in_canonical_order() -> None:
    records, _ = load_tracespan_jsonl(FIXTURE)
    kept = exact_dedup(records).kept
    assert [record_sort_key(r) for r in kept] == sorted(record_sort_key(r) for r in kept)


def test_double_ingest_is_idempotent_and_counted() -> None:
    records, _ = load_tracespan_jsonl(FIXTURE)
    single = exact_dedup(records)
    double = exact_dedup(list(records) + list(records))
    assert double.id_collapsed == len(records)  # every re-ingested record collapses by id
    assert double.kept == single.kept
    assert double.entries == single.entries


def test_double_ingest_through_run_dedup_keeps_the_same_records() -> None:
    records, _ = load_tracespan_jsonl(FIXTURE)
    # One orthogonal axis per distinct canonical text: near-dup finds nothing, so the
    # comparison isolates the id-collapse + exact stages.
    texts = sorted({r.canonical_text for r in records})
    dim = max(2, len(texts))
    mapping = {
        text: tuple(1.0 if i == idx else 0.0 for i in range(dim)) for idx, text in enumerate(texts)
    }
    embedder = StubEmbedder(mapping, dim=dim)
    single = run_dedup(records, embedder=embedder, threshold=0.92)
    double = run_dedup(list(records) + list(records), embedder=embedder, threshold=0.92)
    assert [r.record_id for r in double.kept] == [r.record_id for r in single.kept]
    assert double.report.id_collapsed == len(records)
    assert double.report.records_in == 2 * len(records)
    assert double.report.exact_dropped == single.report.exact_dropped


def test_empty_input() -> None:
    result = exact_dedup([])
    assert result.kept == ()
    assert result.id_collapsed == 0
    assert result.entries == ()


def test_distinct_contents_all_survive() -> None:
    records = [
        make_record(line_no=i, input_text=f"question {i}", output_text=f"answer {i}")
        for i in range(1, 4)
    ]
    result = exact_dedup(records)
    assert len(result.kept) == 3
    assert result.entries == ()
