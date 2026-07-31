"""Exact dedup: id-collapse, then content-hash groups (ADR-0002 rule 2).

The survivor of any duplicate group is the minimum under ``record_sort_key`` — a pure
function of record *content* (origin is part of the record), independent of load order.
"first seen in input order" was rejected: reorder the loader calls and the survivor
(and every dedup-report reference) would change.

Id-collapse runs first: two records can share a ``record_id`` only via double-ingest of
the same origin+exchange (the id is a pure function of both). The sort key cannot break
such a tie, and a dedup entry "dropped rec-X, kept rec-X" would be self-referential
nonsense — so identical ids collapse *before* exact dedup, counted separately
(``id_collapsed``), making re-ingestion idempotent and visible.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import NamedTuple

from evalgen.contracts.dedup import ExactDupEntry
from evalgen.contracts.records import LogRecord, record_sort_key


class _ExactResult(NamedTuple):
    """Module-private plumbing — the public seam is ``dedup.run_dedup``."""

    kept: tuple[LogRecord, ...]
    id_collapsed: int
    entries: tuple[ExactDupEntry, ...]


def content_hash(record: LogRecord) -> str:
    """Full SHA-256 hex over ``canonical_text`` — ADR-0001's "content equality is the
    dedup hash's job" hash. Publishable: the text is post-redaction by construction."""
    return hashlib.sha256(record.canonical_text.encode("utf-8")).hexdigest()


def exact_dedup(records: Sequence[LogRecord]) -> _ExactResult:
    """Collapse duplicate ids, then group by content hash; survivor = sort-key minimum.

    Returns kept records in canonical (``record_sort_key``) order and entries sorted by
    ``dropped_record_id``. Input may arrive in any order — never load-bearing.
    """
    ordered = sorted(records, key=record_sort_key)

    # Id-collapse pass. Copies sharing a record_id differ at most in timestamp/
    # metadata (excluded from the id); keep the lexicographically-smallest
    # model_dump_json() so even exotic double-ingests collapse deterministically.
    by_id: dict[str, LogRecord] = {}
    id_collapsed = 0
    for record in ordered:
        existing = by_id.get(record.record_id)
        if existing is None:
            by_id[record.record_id] = record
        else:
            id_collapsed += 1
            if record.model_dump_json() < existing.model_dump_json():
                by_id[record.record_id] = record
    survivors = sorted(by_id.values(), key=record_sort_key)

    # Content groups: first member in canonical order survives (list already sorted).
    groups: dict[str, list[LogRecord]] = {}
    for record in survivors:
        groups.setdefault(content_hash(record), []).append(record)

    kept: list[LogRecord] = []
    entries: list[ExactDupEntry] = []
    for digest, members in groups.items():
        keeper = members[0]
        kept.append(keeper)
        entries.extend(
            ExactDupEntry(
                dropped_record_id=member.record_id,
                kept_record_id=keeper.record_id,
                content_hash=digest,
            )
            for member in members[1:]
        )
    kept.sort(key=record_sort_key)
    entries.sort(key=lambda e: e.dropped_record_id)
    return _ExactResult(kept=tuple(kept), id_collapsed=id_collapsed, entries=tuple(entries))
