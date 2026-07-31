"""Near-dup: the >= boundary, the transitive-chain trap, shuffle invariance, and the
cross-stage BLOCKER-1 regression (an exact survivor that is itself near-dropped).

Injected stub vectors make every similarity EXACT — the rule under test is `>=` and
the chain semantics, not the hashing embedder's numbers. The boundary test uses
threshold 0.5 (float64-exact-friendly) on purpose: the rule is >=, not the value 0.92.
The BLOCKER-1 regression is the one deliberate exception: it replays the red-team
payload verbatim with the PRODUCTION embedder, because the crash was only reachable
through real cross-stage composition.
"""

from __future__ import annotations

import math
import random

import pytest

from conftest import StubEmbedder, make_record
from evalgen.dedup import run_dedup
from evalgen.dedup.neardup import near_dedup

# ------------------------------------------------------------------- boundary


def test_similarity_exactly_at_threshold_drops_inclusive() -> None:
    a = make_record(line_no=1, input_text="alpha")
    b = make_record(line_no=2, input_text="beta")
    embedder = StubEmbedder(
        {
            a.canonical_text: (1.0, 0.0),
            b.canonical_text: (0.5, 0.8660254037844386),  # dot with a == exactly 0.5
        },
        dim=2,
    )
    result = near_dedup([a, b], embedder=embedder, threshold=0.5)
    assert len(result.kept) == 1
    assert result.kept[0].record_id == a.record_id  # canonical minimum survives
    (entry,) = result.entries
    assert entry.similarity == 0.5
    assert entry.via_chain is False  # 0.5 >= 0.5: a direct match, not a chain


def test_similarity_just_below_threshold_keeps_both() -> None:
    a = make_record(line_no=1, input_text="alpha")
    b = make_record(line_no=2, input_text="beta")
    embedder = StubEmbedder(
        {
            a.canonical_text: (1.0, 0.0),
            b.canonical_text: (0.4999999, 0.8660254615831876),  # dot 0.4999999 < 0.5
        },
        dim=2,
    )
    result = near_dedup([a, b], embedder=embedder, threshold=0.5)
    assert len(result.kept) == 2
    assert result.entries == ()


# ---------------------------------------------------------------------- chain


def _chain_vectors() -> tuple[tuple[float, ...], tuple[float, ...], tuple[float, ...]]:
    """3D unit vectors with dots A·B = A~0.93, B·C ~0.93, A·C = 0.80 (PSD-checked)."""
    b2 = math.sqrt(1.0 - 0.93**2)
    c2 = (0.93 - 0.93 * 0.80) / b2
    c3 = math.sqrt(1.0 - 0.80**2 - c2**2)
    return (1.0, 0.0, 0.0), (0.93, b2, 0.0), (0.80, c2, c3)


def test_transitive_chain_collapses_with_flagged_entry() -> None:
    va, vb, vc = _chain_vectors()
    # Feasibility asserted in-test: unit norms and the three pairwise dots.
    for v in (va, vb, vc):
        assert math.isclose(sum(x * x for x in v), 1.0, abs_tol=1e-12)

    def dot(u: tuple[float, ...], v: tuple[float, ...]) -> float:
        return sum(x * y for x, y in zip(u, v, strict=True))

    assert dot(va, vb) >= 0.92 and dot(vb, vc) >= 0.92
    assert dot(va, vc) == 0.80

    a = make_record(line_no=1, input_text="chain a")
    b = make_record(line_no=2, input_text="chain b")
    c = make_record(line_no=3, input_text="chain c")
    embedder = StubEmbedder(
        {a.canonical_text: va, b.canonical_text: vb, c.canonical_text: vc}, dim=3
    )
    outcome = run_dedup([a, b, c], embedder=embedder, threshold=0.92)
    report = outcome.report

    assert [r.record_id for r in outcome.kept] == [a.record_id]  # canonical min survives
    assert report.near_dropped == 2
    assert report.near_dropped_via_chain == 1
    by_dropped = {e.dropped_record_id: e for e in report.near_entries}
    assert by_dropped[b.record_id].via_chain is False
    assert by_dropped[b.record_id].kept_record_id == a.record_id
    entry_c = by_dropped[c.record_id]
    assert entry_c.via_chain is True  # dropped BELOW threshold w.r.t. its survivor
    assert entry_c.kept_record_id == a.record_id
    assert entry_c.similarity == 0.8


# -------------------------------------------------------------- rounding guard


def test_similarity_within_rounding_distance_of_threshold_crashes() -> None:
    # A~B and B~C link the component; C's similarity to survivor A is 0.4999999999,
    # which rounds to 0.500000 — the stored value would land on the wrong side of the
    # threshold and make via_chain lie. near_dedup must refuse loudly.
    a = make_record(line_no=1, input_text="guard a")
    b = make_record(line_no=2, input_text="guard b")
    c = make_record(line_no=3, input_text="guard c")
    ac = 0.4999999999
    c3 = math.sqrt(1.0 - ac**2 - 0.25)
    embedder = StubEmbedder(
        {
            a.canonical_text: (1.0, 0.0, 0.0),
            b.canonical_text: (0.8, 0.6, 0.0),
            c.canonical_text: (ac, 0.5, c3),
        },
        dim=3,
    )
    # sanity: the component is linked (A·B = 0.8, B·C = 0.4·ac + 0.3 ≈ 0.7 >= 0.5)
    with pytest.raises(ValueError, match="via_chain"):
        near_dedup([a, b, c], embedder=embedder, threshold=0.5)


# ---------------------- cross-stage: exact survivor itself near-dropped (BLOCKER-1)


def test_exact_survivor_near_dropped_remaps_exact_entries_to_final_survivor() -> None:
    """Red-team BLOCKER-1 payload replayed VERBATIM: production embedder, 0.92.

    B is an exact dup of A (byte-equal canonical_text); A is a near-dup of the
    earlier-sorting Z (measured cosine 0.933872 >= 0.92 under the production
    HashingEmbedder at dim 512). Before the fix the report contained exact(B → kept A)
    AND near(A → kept Z), so the "no kept among dropped" validator refused and
    run_dedup crashed on ordinary data. Exact entries must name the FINAL survivor.
    """
    from evalgen.cluster import HashingEmbedder

    base = "Quels sont les horaires du peage de Saint-Arnoult ?"
    variant = "Quels sont les horaires du peage de Saint-Arnoult, svp ?"
    z = make_record(line_no=1, input_text=base)
    a = make_record(line_no=2, input_text=variant)
    b = make_record(line_no=3, input_text=variant)

    outcome = run_dedup([z, a, b], embedder=HashingEmbedder(dim=512), threshold=0.92)
    report = outcome.report

    assert [r.record_id for r in outcome.kept] == [z.record_id]
    assert (report.records_in, report.records_out) == (3, 1)
    assert (report.exact_dropped, report.near_dropped) == (1, 1)
    (exact_entry,) = report.exact_entries
    assert exact_entry.dropped_record_id == b.record_id
    assert exact_entry.kept_record_id == z.record_id  # the FINAL survivor, never a
    (near_entry,) = report.near_entries
    assert near_entry.dropped_record_id == a.record_id
    assert near_entry.kept_record_id == z.record_id
    assert near_entry.similarity == 0.933872  # the red-team's measured cosine, rounded
    assert near_entry.via_chain is False


def test_exact_survivor_near_dropped_is_input_order_independent() -> None:
    from evalgen.cluster import HashingEmbedder

    base = "Quels sont les horaires du peage de Saint-Arnoult ?"
    variant = "Quels sont les horaires du peage de Saint-Arnoult, svp ?"
    z = make_record(line_no=1, input_text=base)
    a = make_record(line_no=2, input_text=variant)
    b = make_record(line_no=3, input_text=variant)
    embedder = HashingEmbedder(dim=512)

    baseline = run_dedup([z, a, b], embedder=embedder, threshold=0.92)
    for seed in (5, 13):
        shuffled = [z, a, b]
        random.Random(seed).shuffle(shuffled)
        outcome = run_dedup(shuffled, embedder=embedder, threshold=0.92)
        assert outcome.report.model_dump_json() == baseline.report.model_dump_json()
        assert [r.record_id for r in outcome.kept] == [r.record_id for r in baseline.kept]


# ---------------------------------------------------------- shuffle invariance


def test_shuffled_input_yields_identical_report_bytes() -> None:
    va, vb, vc = _chain_vectors()
    a = make_record(line_no=1, input_text="chain a")
    b = make_record(line_no=2, input_text="chain b")
    c = make_record(line_no=3, input_text="chain c")
    d = make_record(line_no=4, input_text="unrelated")
    embedder = StubEmbedder(
        {
            a.canonical_text: va,
            b.canonical_text: vb,
            c.canonical_text: vc,
            d.canonical_text: (0.0, 0.0, -1.0),
        },
        dim=3,
    )
    baseline = run_dedup([a, b, c, d], embedder=embedder, threshold=0.92)
    for seed in (3, 11, 2024):
        shuffled = [a, b, c, d]
        random.Random(seed).shuffle(shuffled)
        outcome = run_dedup(shuffled, embedder=embedder, threshold=0.92)
        assert outcome.report.model_dump_json() == baseline.report.model_dump_json()
        assert [r.record_id for r in outcome.kept] == [r.record_id for r in baseline.kept]


# ------------------------------------------------------------------- trivia


def test_single_and_empty_inputs_are_trivial() -> None:
    a = make_record(line_no=1, input_text="alone")
    embedder = StubEmbedder({a.canonical_text: (1.0, 0.0)}, dim=2)
    assert near_dedup([a], embedder=embedder, threshold=0.9).kept == (a,)
    assert near_dedup([], embedder=embedder, threshold=0.9).kept == ()
