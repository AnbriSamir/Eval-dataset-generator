"""Clustering + sampling contracts: partitions that lose records refuse to exist.

Covers derive_cluster_id stability, every ClusteringReport/SamplingReport refuse case,
and the tamper-evidence round-trips (validators re-run on deserialization).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from evalgen.contracts import (
    NOISE_CLUSTER_ID,
    Cluster,
    ClusteringReport,
    EmbedderFingerprint,
    SamplingReport,
    StratumSample,
    derive_cluster_id,
)

FP = EmbedderFingerprint(name="stub", dim=8, analyzer="stub", ngram_min=1, ngram_max=1)


def cluster(*ids: str) -> Cluster:
    ordered = tuple(sorted(ids))
    return Cluster(cluster_id=derive_cluster_id(ordered), record_ids=ordered)


# -------------------------------------------------------------- derive_cluster_id


def test_cluster_id_is_content_derived_and_order_invariant() -> None:
    assert derive_cluster_id(["rec-b", "rec-a"]) == derive_cluster_id(["rec-a", "rec-b"])
    assert derive_cluster_id(["rec-a"]).startswith("cl-")
    assert len(derive_cluster_id(["rec-a"])) == len("cl-") + 12


def test_cluster_id_changes_with_membership() -> None:
    assert derive_cluster_id(["rec-a"]) != derive_cluster_id(["rec-a", "rec-b"])


def test_cluster_id_join_cannot_collide_by_boundary_shift() -> None:
    assert derive_cluster_id(["ab", "c"]) != derive_cluster_id(["a", "bc"])


# ------------------------------------------------------------------------ Cluster


def test_cluster_validates_and_roundtrips() -> None:
    c = cluster("rec-a", "rec-b")
    assert c.size == 2
    assert Cluster.model_validate_json(c.model_dump_json()) == c


def test_cluster_with_wrong_id_refuses() -> None:
    with pytest.raises(ValidationError, match="content-derived"):
        Cluster(cluster_id="cl-000000000000", record_ids=("rec-a",))


def test_cluster_unsorted_or_duplicate_members_refuse() -> None:
    good = ("rec-a", "rec-b")
    with pytest.raises(ValidationError, match="sorted"):
        Cluster(cluster_id=derive_cluster_id(good), record_ids=("rec-b", "rec-a"))
    with pytest.raises(ValidationError, match="sorted"):
        Cluster(cluster_id=derive_cluster_id(("rec-a", "rec-a")), record_ids=("rec-a", "rec-a"))


def test_empty_cluster_refuses() -> None:
    with pytest.raises(ValidationError):
        Cluster(cluster_id=derive_cluster_id(()), record_ids=())


# -------------------------------------------------------------- ClusteringReport


def clustering_report(**overrides: object) -> ClusteringReport:
    fields: dict = {
        "embedder": FP,
        "min_cluster_size": 2,
        "metric": "euclidean_l2norm",
        "records_in": 5,
        "clusters": (cluster("rec-a", "rec-b", "rec-c"), cluster("rec-d", "rec-e")),
        "noise_record_ids": (),
    }
    fields.update(overrides)
    return ClusteringReport(**fields)


def test_clustering_report_roundtrips() -> None:
    report = clustering_report(records_in=6, noise_record_ids=("rec-z",))
    assert ClusteringReport.model_validate_json(report.model_dump_json()) == report


def test_lost_record_refuses() -> None:
    with pytest.raises(ValidationError, match="silently lost"):
        clustering_report(records_in=7)


def test_member_noise_overlap_refuses() -> None:
    with pytest.raises(ValidationError, match="disjoint"):
        clustering_report(records_in=6, noise_record_ids=("rec-a",))


def test_clusters_unsorted_by_size_refuse() -> None:
    small, big = cluster("rec-x", "rec-y"), cluster("rec-a", "rec-b", "rec-c")
    with pytest.raises(ValidationError, match="sorted"):
        clustering_report(clusters=(small, big))


def test_unsorted_noise_refuses() -> None:
    with pytest.raises(ValidationError, match="sorted"):
        clustering_report(records_in=7, noise_record_ids=("rec-z", "rec-y"))


def test_reserved_noise_id_smuggled_via_model_construct_refuses() -> None:
    # Cluster's own validator makes cluster_id == "noise" unconstructible; a forged
    # instance smuggled around it still dies at the REPORT boundary — either on the
    # content-derived id recomputation (pydantic re-validates the nested model) or on
    # the reserved-id check. Both are acceptable defense lines; what matters is that
    # a "noise"-labeled cluster can never exist inside a validated report.
    forged = Cluster.model_construct(cluster_id=NOISE_CLUSTER_ID, record_ids=("rec-a",))
    with pytest.raises(ValidationError, match="reserved|content-derived"):
        clustering_report(records_in=1, clusters=(forged,))


def test_min_cluster_size_below_two_refuses() -> None:
    with pytest.raises(ValidationError):
        clustering_report(min_cluster_size=1)


# ---------------------------------------------------------------- StratumSample


def test_stratum_sample_quota_must_match_ids() -> None:
    with pytest.raises(ValidationError, match="quota"):
        StratumSample(cluster_id="cl-x", stratum_size=3, quota=2, sampled_record_ids=("rec-a",))


def test_stratum_sample_quota_above_size_refuses() -> None:
    with pytest.raises(ValidationError, match="exceeds"):
        StratumSample(
            cluster_id="cl-x",
            stratum_size=1,
            quota=2,
            sampled_record_ids=("rec-a", "rec-b"),
        )


def test_stratum_sample_unsorted_ids_refuse() -> None:
    with pytest.raises(ValidationError, match="sorted"):
        StratumSample(
            cluster_id="cl-x",
            stratum_size=2,
            quota=2,
            sampled_record_ids=("rec-b", "rec-a"),
        )


# --------------------------------------------------------------- SamplingReport


def stratum(cluster_id: str, size: int, quota: int, ids: tuple[str, ...]) -> StratumSample:
    return StratumSample(
        cluster_id=cluster_id, stratum_size=size, quota=quota, sampled_record_ids=ids
    )


def sampling_report(**overrides: object) -> SamplingReport:
    fields: dict = {
        "seed": 1750,
        "sample_size_requested": 3,
        "records_in": 5,
        "total_sampled": 3,
        "strata": (
            stratum("cl-a", 3, 2, ("rec-a", "rec-b")),
            stratum(NOISE_CLUSTER_ID, 2, 1, ("rec-z",)),
        ),
    }
    fields.update(overrides)
    return SamplingReport(**fields)


def test_sampling_report_roundtrips() -> None:
    report = sampling_report()
    assert SamplingReport.model_validate_json(report.model_dump_json()) == report


def test_quota_sum_mismatch_refuses() -> None:
    with pytest.raises(ValidationError, match="must be equal"):
        sampling_report(total_sampled=2)


def test_requested_vs_sampled_mismatch_refuses() -> None:
    # k = min(requested, records_in) = 4, but quotas sum to 3
    with pytest.raises(ValidationError, match="must be equal"):
        sampling_report(sample_size_requested=4)


def test_stratum_sizes_must_sum_to_records_in() -> None:
    with pytest.raises(ValidationError, match="records_in"):
        sampling_report(records_in=6, sample_size_requested=3)


def test_globally_duplicate_sampled_ids_refuse() -> None:
    with pytest.raises(ValidationError, match="more than one"):
        sampling_report(
            strata=(
                stratum("cl-a", 3, 2, ("rec-a", "rec-b")),
                stratum(NOISE_CLUSTER_ID, 2, 1, ("rec-a",)),
            )
        )


def test_strata_order_pinned() -> None:
    with pytest.raises(ValidationError, match="sorted"):
        sampling_report(
            strata=(
                stratum(NOISE_CLUSTER_ID, 2, 1, ("rec-z",)),
                stratum("cl-a", 3, 2, ("rec-a", "rec-b")),
            )
        )
