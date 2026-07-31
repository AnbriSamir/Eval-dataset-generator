"""HDBSCAN coverage clustering on unit vectors, noise first-class (ADR-0002 rule 6).

Metric trick, documented in every report as ``"euclidean_l2norm"``: euclidean distance
on L2-normalized rows is strictly monotone in cosine (d² = 2 − 2·cos), so density
orderings match cosine's while staying KD/ball-tree-accelerated — ``metric="cosine"``
would force brute-force neighbor search.

Determinism: sklearn's HDBSCAN has no ``random_state`` — it is algorithmically
deterministic *given the same input matrix*. The matrix is made canonical by sorting
records with ``record_sort_key`` before embedding; a shuffle test pins that a permuted
input list yields the identical report.

What clustering embeds: ``cluster_text`` (the INPUT side only — ADR-0001 consumer
table). Coverage means covering the *traffic* distribution, and traffic is defined by
what came in, not by output phrasing.
"""

from __future__ import annotations

from collections.abc import Sequence

from sklearn.cluster import HDBSCAN

from evalgen.contracts.clustering import Cluster, ClusteringReport, derive_cluster_id
from evalgen.contracts.embeddings import Embedder
from evalgen.contracts.records import LogRecord, record_sort_key

#: The metric actually passed to HDBSCAN is "euclidean"; the report names the full
#: contract (euclidean over L2-normalized rows) so provenance is unambiguous.
_METRIC_NAME = "euclidean_l2norm"


def cluster_records(
    records: Sequence[LogRecord], *, embedder: Embedder, min_cluster_size: int
) -> ClusteringReport:
    """Map the traffic distribution; every record lands in a cluster or in noise.

    Fewer records than ``min_cluster_size`` skips HDBSCAN entirely (all-noise
    partition) — an explicit guard, not an sklearn error surface. Noise labels are
    ``label < 0`` (NOT ``== -1``: sklearn HDBSCAN can emit −2/−3 "infinite" labels
    for degenerate points).
    """
    ordered = sorted(records, key=record_sort_key)
    if not ordered:
        return ClusteringReport(
            embedder=embedder.fingerprint,
            min_cluster_size=min_cluster_size,
            metric=_METRIC_NAME,
            records_in=0,
        )
    if len(ordered) < min_cluster_size:
        return ClusteringReport(
            embedder=embedder.fingerprint,
            min_cluster_size=min_cluster_size,
            metric=_METRIC_NAME,
            records_in=len(ordered),
            noise_record_ids=tuple(sorted(r.record_id for r in ordered)),
        )

    matrix = embedder.embed([r.cluster_text for r in ordered])
    labels = HDBSCAN(min_cluster_size=min_cluster_size, metric="euclidean").fit_predict(matrix)

    noise_ids: list[str] = []
    members_by_label: dict[int, list[str]] = {}
    for record, label in zip(ordered, labels, strict=True):
        if label < 0:
            noise_ids.append(record.record_id)
        else:
            members_by_label.setdefault(int(label), []).append(record.record_id)

    clusters = [
        Cluster(cluster_id=derive_cluster_id(ids), record_ids=tuple(sorted(ids)))
        for ids in members_by_label.values()
    ]
    clusters.sort(key=lambda c: (-c.size, c.cluster_id))
    return ClusteringReport(
        embedder=embedder.fingerprint,
        min_cluster_size=min_cluster_size,
        metric=_METRIC_NAME,
        records_in=len(ordered),
        clusters=tuple(clusters),
        noise_record_ids=tuple(sorted(noise_ids)),
    )
