"""Near-dup dedup: union-find components over cosine >= threshold (ADR-0002 rule 3).

The duplicate graph is symmetric; its connected components are order-independent and
well-defined regardless of iteration — unlike greedy pairwise-to-representative, whose
semantics depend on which record happened to become representative first. One survivor
per component: the ``record_sort_key`` minimum.

The accepted cost is *chain collapse* (A~B~C above threshold while A~C is below it):
made visible, never hidden — every dropped record's entry carries its cosine to the
survivor it was dropped against, plus ``via_chain = (similarity < threshold)``. A
reviewer can count chain-collapsed drops and audit each one.

Boundary rule: ``similarity >= threshold`` drops — INCLUSIVE, pinned by the config
docstring ("at/above which two records are near-duplicates") and a boundary test.
Comparisons run in full float64; similarities are rounded to 6 decimals only at the
report boundary.

What near-dup embeds: ``canonical_text`` (the same text exact dedup hashes — ADR-0001
consumer table), *not* ``cluster_text``: the same input with a meaningfully different
output must be able to survive as a distinct eval case.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import NamedTuple

import numpy as np

from evalgen.contracts.dedup import SIMILARITY_DECIMALS, NearDupEntry
from evalgen.contracts.embeddings import Embedder
from evalgen.contracts.records import LogRecord


class _NearResult(NamedTuple):
    """Module-private plumbing — the public seam is ``dedup.run_dedup``."""

    kept: tuple[LogRecord, ...]
    entries: tuple[NearDupEntry, ...]


class _UnionFind:
    """Plain path-compression union-find over list positions.

    Positions are canonical — the ``record_sort_key`` sort happened upstream — and
    components are order-independent anyway (the graph is symmetric).
    """

    def __init__(self, n: int) -> None:
        self._parent = list(range(n))

    def find(self, i: int) -> int:
        root = i
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[i] != root:  # path compression
            self._parent[i], i = root, self._parent[i]
        return root

    def union(self, i: int, j: int) -> None:
        root_i, root_j = self.find(i), self.find(j)
        if root_i != root_j:
            # Attach the larger root under the smaller: the smallest index stays the
            # root of its component, which is exactly the record_sort_key minimum.
            if root_i < root_j:
                self._parent[root_j] = root_i
            else:
                self._parent[root_i] = root_j


def near_dedup(
    records: Sequence[LogRecord], *, embedder: Embedder, threshold: float
) -> _NearResult:
    """Drop near-duplicates among exact-dedup survivors (already in canonical order).

    Raises ``ValueError`` if any dropped record's similarity to its survivor sits
    within 5e-7 of the threshold (rounding to 6 decimals could then cross the
    boundary and make the stored ``via_chain`` flag lie — a calibration bug worth
    crashing on, never worth a lying report).
    """
    n = len(records)
    if n <= 1:
        return _NearResult(kept=tuple(records), entries=())

    embeddings = embedder.embed([r.canonical_text for r in records])
    similarity = embeddings @ embeddings.T
    np.clip(similarity, -1.0, 1.0, out=similarity)

    uf = _UnionFind(n)
    for i in range(n):
        for j in range(i + 1, n):
            if similarity[i, j] >= threshold:  # INCLUSIVE boundary (ADR-0002 rule 3)
                uf.union(i, j)

    components: dict[int, list[int]] = {}
    for i in range(n):
        components.setdefault(uf.find(i), []).append(i)

    kept: list[LogRecord] = []
    entries: list[NearDupEntry] = []
    for root in sorted(components):
        members = components[root]  # ascending index order == canonical order
        rep = members[0]  # smallest index == record_sort_key minimum
        kept.append(records[rep])
        for m in members[1:]:
            sim = float(similarity[m, rep])
            rounded = round(sim, SIMILARITY_DECIMALS)
            if (rounded < threshold) != (sim < threshold):
                raise ValueError(
                    f"similarity {sim!r} of record {records[m].record_id!r} to its "
                    f"survivor sits within rounding distance of threshold {threshold!r}"
                    " — refusing to emit a report whose via_chain flag would lie"
                )
            entries.append(
                NearDupEntry(
                    dropped_record_id=records[m].record_id,
                    kept_record_id=records[rep].record_id,
                    similarity=rounded,
                    via_chain=sim < threshold,  # FULL precision, never the rounded value
                )
            )
    entries.sort(key=lambda e: e.dropped_record_id)
    return _NearResult(kept=tuple(kept), entries=tuple(entries))
