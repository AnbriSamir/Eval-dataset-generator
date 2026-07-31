"""Stratified sampling: hand-computed integer quotas, the floor-1 rule, seeded ranking.

The skewed-quota case is fully hand-derived (ADR-0002 rule 7): sizes [50, 3, 2],
k = 10 → r = 7, w = [49, 2, 1], W = 52; base quotas 1+(7·49)//52 = 7, 1+0, 1+0 (sum 9);
leftover 1 goes to remainder rank 1 ((7·49) mod 52 = 31 > 14 > 7) → quotas [8, 1, 1].
"""

from __future__ import annotations

from evalgen.cluster import stratified_sample
from evalgen.contracts import (
    NOISE_CLUSTER_ID,
    Cluster,
    ClusteringReport,
    EmbedderFingerprint,
    derive_cluster_id,
)

FP = EmbedderFingerprint(name="stub", dim=8, analyzer="stub", ngram_min=1, ngram_max=1)


def cluster_of(prefix: str, size: int) -> Cluster:
    ids = tuple(sorted(f"rec-{prefix}{i:04d}" for i in range(size)))
    return Cluster(cluster_id=derive_cluster_id(ids), record_ids=ids)


def report_with(sizes: list[int], noise: int = 0) -> ClusteringReport:
    clusters = [cluster_of(chr(ord("a") + i), size) for i, size in enumerate(sizes)]
    clusters.sort(key=lambda c: (-c.size, c.cluster_id))
    noise_ids = tuple(sorted(f"rec-z{i:04d}" for i in range(noise)))
    return ClusteringReport(
        embedder=FP,
        min_cluster_size=2,
        metric="euclidean_l2norm",
        records_in=sum(sizes) + noise,
        clusters=tuple(clusters),
        noise_record_ids=noise_ids,
    )


def quotas_of(report: object) -> list[int]:
    return [s.quota for s in report.strata]  # type: ignore[attr-defined]


# ------------------------------------------------------------ quota arithmetic


def test_skewed_sizes_hand_computed_largest_remainder() -> None:
    sampling = stratified_sample(report_with([50, 3, 2]), sample_size=10, seed=1750)
    assert [s.stratum_size for s in sampling.strata] == [50, 3, 2]
    assert quotas_of(sampling) == [8, 1, 1]  # exactly the hand computation above
    assert sampling.total_sampled == 10


def test_budget_below_stratum_count_gives_first_k_strata_one_each() -> None:
    sampling = stratified_sample(report_with([5, 4, 3]), sample_size=2, seed=1750)
    assert [s.stratum_size for s in sampling.strata] == [5, 4, 3]
    assert quotas_of(sampling) == [1, 1, 0]  # size-desc order; the zero stays visible


def test_budget_at_least_records_samples_everything() -> None:
    sampling = stratified_sample(report_with([4, 3], noise=2), sample_size=50, seed=1750)
    assert sampling.total_sampled == 9
    assert quotas_of(sampling) == [s.stratum_size for s in sampling.strata]


def test_all_singleton_strata_w_zero_path() -> None:
    sampling = stratified_sample(report_with([1, 1, 1]), sample_size=3, seed=1750)
    assert quotas_of(sampling) == [1, 1, 1]


def test_minority_stratum_never_starved_by_floor() -> None:
    # Naive floor(k*s/N) would give the size-2 stratum floor(10*2/55) = 0.
    sampling = stratified_sample(report_with([50, 3, 2]), sample_size=10, seed=1750)
    assert all(s.quota >= 1 for s in sampling.strata)


# ------------------------------------------------------------- noise stratum


def test_noise_is_a_first_class_stratum() -> None:
    sampling = stratified_sample(report_with([6], noise=4), sample_size=5, seed=1750)
    by_id = {s.cluster_id: s for s in sampling.strata}
    assert NOISE_CLUSTER_ID in by_id
    assert by_id[NOISE_CLUSTER_ID].quota >= 1


def test_empty_noise_produces_no_stratum() -> None:
    sampling = stratified_sample(report_with([6]), sample_size=3, seed=1750)
    assert [s.cluster_id for s in sampling.strata] != [NOISE_CLUSTER_ID]
    assert len(sampling.strata) == 1


# ---------------------------------------------------------- seeded selection


def test_sampled_ids_are_members_of_their_stratum() -> None:
    report = report_with([50, 3, 2], noise=4)
    sampling = stratified_sample(report, sample_size=12, seed=1750)
    members = {c.cluster_id: set(c.record_ids) for c in report.clusters}
    members[NOISE_CLUSTER_ID] = set(report.noise_record_ids)
    for stratum in sampling.strata:
        assert set(stratum.sampled_record_ids) <= members[stratum.cluster_id]


def test_seed_change_rerolls_selection_but_not_quotas() -> None:
    report = report_with([50, 3, 2])
    a = stratified_sample(report, sample_size=10, seed=1750)
    b = stratified_sample(report, sample_size=10, seed=1751)
    assert quotas_of(a) == quotas_of(b)
    assert [s.sampled_record_ids for s in a.strata] != [s.sampled_record_ids for s in b.strata]


def test_same_seed_is_byte_identical() -> None:
    report = report_with([50, 3, 2], noise=3)
    a = stratified_sample(report, sample_size=10, seed=1750)
    b = stratified_sample(report, sample_size=10, seed=1750)
    assert a.model_dump_json() == b.model_dump_json()


def test_seed_is_recorded_in_the_report() -> None:
    sampling = stratified_sample(report_with([3]), sample_size=2, seed=1750)
    assert sampling.seed == 1750
