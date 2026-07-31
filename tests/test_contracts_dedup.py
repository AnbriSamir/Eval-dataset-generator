"""DedupReport / DedupOutcome self-validation: a report that lies refuses to exist.

Every refuse case below is one of the ADR-0002 rule-8 invariants; the happy-path
round-trip pins that validators re-run on deserialization (tamper-evidence, same
discipline as LogRecord).
"""

from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from conftest import make_record
from evalgen.contracts import (
    DedupOutcome,
    DedupReport,
    EmbedderFingerprint,
    ExactDupEntry,
    NearDupEntry,
)

FP = EmbedderFingerprint(name="stub", dim=8, analyzer="stub", ngram_min=1, ngram_max=1)
HASH = hashlib.sha256(b"payload").hexdigest()


def exact_entry(dropped: str = "rec-a", kept: str = "rec-b") -> ExactDupEntry:
    return ExactDupEntry(dropped_record_id=dropped, kept_record_id=kept, content_hash=HASH)


def near_entry(
    dropped: str = "rec-c", kept: str = "rec-d", sim: float = 0.95, chain: bool = False
) -> NearDupEntry:
    return NearDupEntry(
        dropped_record_id=dropped, kept_record_id=kept, similarity=sim, via_chain=chain
    )


def report(**overrides: object) -> DedupReport:
    fields: dict = {
        "threshold": 0.92,
        "embedder": FP,
        "records_in": 4,
        "records_out": 2,
        "id_collapsed": 0,
        "exact_dropped": 1,
        "near_dropped": 1,
        "near_dropped_via_chain": 0,
        "exact_entries": (exact_entry(),),
        "near_entries": (near_entry(),),
    }
    fields.update(overrides)
    return DedupReport(**fields)


# ------------------------------------------------------------------ happy path


def test_valid_report_roundtrips_and_revalidates() -> None:
    original = report()
    restored = DedupReport.model_validate_json(original.model_dump_json())
    assert restored == original


def test_chain_entry_and_counter_accepted_together() -> None:
    r = report(
        near_entries=(near_entry(sim=0.5, chain=True),),
        near_dropped_via_chain=1,
    )
    assert r.near_dropped_via_chain == 1


# ---------------------------------------------------------------- refuse cases


def test_sums_that_do_not_add_up_refuse() -> None:
    with pytest.raises(ValidationError, match="accounted"):
        report(records_out=3)  # 4 != 3 + 0 + 1 + 1


def test_entry_count_mismatch_refuses() -> None:
    with pytest.raises(ValidationError, match="exact"):
        report(exact_dropped=2, records_in=5)
    with pytest.raises(ValidationError, match="near"):
        report(near_dropped=2, records_in=5)


def test_via_chain_counter_mismatch_refuses() -> None:
    with pytest.raises(ValidationError, match="via_chain"):
        report(near_dropped_via_chain=1)  # entry says False


def test_via_chain_flag_lying_about_similarity_refuses() -> None:
    # similarity 0.5 < threshold 0.92 but flag claims a direct match
    with pytest.raises(ValidationError, match="chain flag"):
        report(near_entries=(near_entry(sim=0.5, chain=False),))
    # similarity above threshold flagged as chain
    with pytest.raises(ValidationError, match="chain flag"):
        report(near_entries=(near_entry(sim=0.95, chain=True),), near_dropped_via_chain=1)


def test_kept_id_among_dropped_refuses() -> None:
    with pytest.raises(ValidationError, match="survivor"):
        report(
            exact_entries=(exact_entry(dropped="rec-a", kept="rec-b"),),
            near_entries=(near_entry(dropped="rec-b", kept="rec-x"),),
        )


def test_duplicate_dropped_ids_refuse() -> None:
    with pytest.raises(ValidationError, match="more than one"):
        report(
            exact_entries=(exact_entry(dropped="rec-a"),),
            near_entries=(near_entry(dropped="rec-a"),),
        )


def test_unsorted_entries_refuse() -> None:
    with pytest.raises(ValidationError, match="sorted"):
        report(
            records_in=5,
            exact_dropped=2,
            exact_entries=(exact_entry(dropped="rec-z"), exact_entry(dropped="rec-a")),
        )


def test_self_drop_entry_refuses() -> None:
    with pytest.raises(ValidationError, match="itself"):
        exact_entry(dropped="rec-a", kept="rec-a")
    with pytest.raises(ValidationError, match="itself"):
        near_entry(dropped="rec-a", kept="rec-a")


def test_content_hash_must_be_full_sha256_hex() -> None:
    with pytest.raises(ValidationError, match="hex"):
        ExactDupEntry(dropped_record_id="rec-a", kept_record_id="rec-b", content_hash="abc123")
    with pytest.raises(ValidationError, match="hex"):
        ExactDupEntry(dropped_record_id="rec-a", kept_record_id="rec-b", content_hash="Z" * 64)


def test_similarity_out_of_range_refuses() -> None:
    with pytest.raises(ValidationError):
        near_entry(sim=1.5)
    with pytest.raises(ValidationError):
        near_entry(sim=-1.5)


# ---------------------------------------------------------------- DedupOutcome


def _two_records() -> tuple:
    return (
        make_record(line_no=1, input_text="alpha"),
        make_record(line_no=2, input_text="beta"),
    )


def _outcome_report(records_out: int) -> DedupReport:
    return report(
        records_in=records_out,
        records_out=records_out,
        exact_dropped=0,
        near_dropped=0,
        exact_entries=(),
        near_entries=(),
    )


def test_outcome_happy_path_roundtrips() -> None:
    kept = _two_records()
    outcome = DedupOutcome(kept=kept, report=_outcome_report(2))
    restored = DedupOutcome.model_validate_json(outcome.model_dump_json())
    assert restored == outcome


def test_outcome_count_mismatch_refuses() -> None:
    kept = _two_records()
    with pytest.raises(ValidationError, match="records_out"):
        DedupOutcome(kept=kept, report=_outcome_report(3))


def test_outcome_unsorted_kept_refuses() -> None:
    a, b = _two_records()
    with pytest.raises(ValidationError, match="record_sort_key"):
        DedupOutcome(kept=(b, a), report=_outcome_report(2))


def test_outcome_duplicate_kept_ids_refuse() -> None:
    a, _ = _two_records()
    with pytest.raises(ValidationError, match="duplicate"):
        DedupOutcome(kept=(a, a), report=_outcome_report(2))


# The two cross-model forgeries the red team proved validated before the fix
# (probes P3/P3b): kept and the report's entries must cross-check each other.


def test_outcome_with_kept_record_also_reported_dropped_refuses() -> None:
    lo, mid = _two_records()
    forged = report(
        records_in=3,
        records_out=2,
        exact_dropped=0,
        near_dropped=1,
        exact_entries=(),
        near_entries=(near_entry(dropped=mid.record_id, kept=lo.record_id),),
    )
    # mid is simultaneously in kept AND among the dropped entries.
    with pytest.raises(ValidationError, match="cannot be returned"):
        DedupOutcome(kept=(lo, mid), report=forged)


def test_outcome_with_ghost_survivor_reference_refuses() -> None:
    lo, mid = _two_records()
    forged = report(
        records_in=2,
        records_out=1,
        exact_dropped=0,
        near_dropped=1,
        exact_entries=(),
        near_entries=(near_entry(dropped=mid.record_id, kept="rec-00000000deadbeef"),),
    )
    # The entry names a survivor that exists nowhere among the kept records.
    with pytest.raises(ValidationError, match="ghost"):
        DedupOutcome(kept=(lo,), report=forged)
