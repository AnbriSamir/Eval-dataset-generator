"""Exact dedup (content hash) then near-dup (embedding cosine, measured threshold).

Emits a self-validating dedup report: what was dropped, kept in favor of whom, at what
similarity, chain collapses flagged (ADR-0002). ``run_dedup`` is the public seam —
records may arrive in ANY order (the canonical ``record_sort_key`` sort happens inside
``exact_dedup``); the injected :class:`~evalgen.contracts.embeddings.Embedder` keeps
this package free of any ``cluster`` import.
"""

from collections.abc import Sequence

from evalgen.contracts.dedup import DedupOutcome, DedupReport, ExactDupEntry
from evalgen.contracts.embeddings import Embedder
from evalgen.contracts.records import LogRecord
from evalgen.dedup.calibrate import calibrate_threshold, load_labeled_pairs
from evalgen.dedup.exact import exact_dedup
from evalgen.dedup.neardup import near_dedup

__all__ = [
    "calibrate_threshold",
    "exact_dedup",
    "load_labeled_pairs",
    "near_dedup",
    "run_dedup",
]


def run_dedup(
    records: Sequence[LogRecord], *, embedder: Embedder, threshold: float
) -> DedupOutcome:
    """id-collapse → exact → near-dup; assembles the self-validating ``DedupReport``.

    The report's own validator is the final integrity gate — it is constructed, never
    bypassed: a pass whose sums do not hold cannot return.

    Exact entries name the FINAL survivor (ADR-0002 rule 2, amended after the Phase 2
    red team): an exact survivor can itself be near-dropped in favor of an
    earlier-sorting near-duplicate, so every exact ``kept_record_id`` is remapped
    through the near-dup drop map before the report is assembled. ONE hop suffices:
    near survivors are component representatives and are never themselves dropped
    (exact groups partition the corpus and id-collapse runs first, so the only
    cross-stage hazard is exact-kept → near-dropped). ``content_hash`` stays untouched
    — it documents the dropped record's exact group, which remains true.
    """
    exact = exact_dedup(records)
    near = near_dedup(exact.kept, embedder=embedder, threshold=threshold)
    final_survivor = {e.dropped_record_id: e.kept_record_id for e in near.entries}
    exact_entries = tuple(
        (
            entry
            if entry.kept_record_id not in final_survivor
            else ExactDupEntry(
                dropped_record_id=entry.dropped_record_id,
                kept_record_id=final_survivor[entry.kept_record_id],
                content_hash=entry.content_hash,
            )
        )
        for entry in exact.entries
    )
    report = DedupReport(
        threshold=threshold,
        embedder=embedder.fingerprint,
        records_in=len(records),
        records_out=len(near.kept),
        id_collapsed=exact.id_collapsed,
        exact_dropped=len(exact.entries),
        near_dropped=len(near.entries),
        near_dropped_via_chain=sum(e.via_chain for e in near.entries),
        exact_entries=exact_entries,
        near_entries=near.entries,
    )
    return DedupOutcome(kept=near.kept, report=report)
