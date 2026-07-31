"""Stratified coverage sampling: floor-1 largest-remainder quotas, seeded hash ranking
(ADR-0002 rule 7).

Quota allocation protects the tail: naive ``floor(k·sᵢ/N)`` zeroes out minority
clusters — exactly what stratification exists to prevent. Every stratum (clusters +
noise) gets a floor of 1 when the budget allows; the remainder is split
largest-remainder (Hamilton) style in ALL-INTEGER arithmetic — no float remainder can
tie-wobble across platforms.

Within-stratum selection is a seeded hash ranking: score every member as
``sha256(f"{seed}␟{record_id}")`` and take the ``quota`` lexicographically-smallest
scores. Stateless (no RNG object to misuse), invariant to member iteration order,
ties impossible (ids are unique), and changing the seed re-rolls the whole sample
deterministically.
"""

from __future__ import annotations

import hashlib

from evalgen.contracts.clustering import (
    NOISE_CLUSTER_ID,
    ClusteringReport,
    SamplingReport,
    StratumSample,
)
from evalgen.contracts.records import CANONICAL_SEP


def _score(seed: int, record_id: str) -> str:
    return hashlib.sha256(f"{seed}{CANONICAL_SEP}{record_id}".encode()).hexdigest()


def stratified_sample(
    clustering: ClusteringReport, *, sample_size: int, seed: int
) -> SamplingReport:
    """Sample ``min(sample_size, records_in)`` records across all strata.

    Strata = clusters + (noise as a stratum, if non-empty), ordered
    ``(size desc, cluster_id asc)``. Quotas per ADR-0002 rule 7; the returned report
    self-validates its sums (Σ quotas == total_sampled == min(requested, records_in)).
    """
    strata: list[tuple[str, tuple[str, ...]]] = [
        (c.cluster_id, c.record_ids) for c in clustering.clusters
    ]
    if clustering.noise_record_ids:
        strata.append((NOISE_CLUSTER_ID, clustering.noise_record_ids))
    strata.sort(key=lambda s: (-len(s[1]), s[0]))

    m = len(strata)
    n_records = clustering.records_in
    k = min(sample_size, n_records)
    sizes = [len(members) for _, members in strata]

    if k <= m:
        quotas = [1 if i < k else 0 for i in range(m)]
    else:
        # Floor of 1 each, then largest-remainder over capacities — integers only.
        remainder = k - m
        capacities = [size - 1 for size in sizes]
        total_capacity = sum(capacities)  # == n_records - m >= remainder
        if total_capacity == 0:
            quotas = [1] * m  # all strata size 1 → k == m, remainder 0
        else:
            quotas = [1 + (remainder * w) // total_capacity for w in capacities]
            leftover = k - sum(quotas)
            rank = sorted(
                range(m),
                key=lambda i: (
                    -((remainder * capacities[i]) % total_capacity),
                    -sizes[i],
                    strata[i][0],
                ),
            )
            for i in rank:
                if leftover == 0:
                    break
                if quotas[i] < sizes[i]:
                    quotas[i] += 1
                    leftover -= 1
            if leftover != 0:  # unreachable: total capacity >= k
                raise AssertionError(f"quota allocation left {leftover} units unplaced")

    samples = []
    for (cluster_id, members), quota in zip(strata, quotas, strict=True):
        chosen = sorted(members, key=lambda rid: _score(seed, rid))[:quota]
        samples.append(
            StratumSample(
                cluster_id=cluster_id,
                stratum_size=len(members),
                quota=quota,
                sampled_record_ids=tuple(sorted(chosen)),
            )
        )
    return SamplingReport(
        seed=seed,
        sample_size_requested=sample_size,
        records_in=n_records,
        total_sampled=sum(quotas),
        strata=tuple(samples),
    )
