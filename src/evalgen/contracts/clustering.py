"""Self-validating clustering + sampling contracts (ADR-0002 rules 6-8).

Cluster ids are content-derived (``derive_cluster_id``) — raw HDBSCAN integer labels
are an implementation artifact with no stability guarantee across runs or library
versions. Noise is first-class: it appears in the report, is summed by the validator
(cluster sizes + noise == records_in), and is sampled like any other stratum —
silently discarding noise is coverage gaming (CLAUDE.md §1 failure mode 2).
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

from evalgen.contracts.embeddings import EmbedderFingerprint
from evalgen.contracts.records import CANONICAL_SEP

#: Reserved stratum id for HDBSCAN noise (never a real cluster's id — validated).
NOISE_CLUSTER_ID = "noise"

_CLUSTER_ID_PREFIX = "cl-"
_CLUSTER_ID_HEX_LEN = 12


def derive_cluster_id(record_ids: Sequence[str]) -> str:
    """``"cl-" + sha256(CANONICAL_SEP.join(sorted(record_ids)))[:12]``.

    Content-derived, stable across runs and HDBSCAN label permutations (ADR-0002
    options §4); the separator prevents boundary-shift collisions between ids.
    """
    joined = CANONICAL_SEP.join(sorted(record_ids))
    digest = hashlib.sha256(joined.encode("utf-8")).hexdigest()
    return _CLUSTER_ID_PREFIX + digest[:_CLUSTER_ID_HEX_LEN]


class Cluster(BaseModel):
    """One HDBSCAN cluster: sorted unique members under a self-verifying id."""

    model_config = ConfigDict(frozen=True)

    cluster_id: str
    record_ids: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _members_sorted_unique_and_id_matches(self) -> Cluster:
        ids = list(self.record_ids)
        if ids != sorted(ids) or len(set(ids)) != len(ids):
            raise ValueError("record_ids must be sorted ascending and unique")
        expected = derive_cluster_id(self.record_ids)
        if self.cluster_id != expected:
            raise ValueError(
                f"cluster_id {self.cluster_id!r} does not match members "
                f"(expected {expected!r}) — cluster ids are content-derived"
            )
        return self

    @property
    def size(self) -> int:
        return len(self.record_ids)


class ClusteringReport(BaseModel):
    """The coverage map: clusters + first-class noise; refuses to lose a record."""

    model_config = ConfigDict(frozen=True)

    embedder: EmbedderFingerprint
    min_cluster_size: int = Field(ge=2)
    #: Fixed ``"euclidean_l2norm"`` — documents the trick: euclidean on L2-normalized
    #: rows is strictly monotone in cosine (d² = 2 − 2·cos), so density orderings
    #: match cosine's while staying tree-accelerated.
    metric: str = Field(min_length=1)
    records_in: int = Field(ge=0)
    clusters: tuple[Cluster, ...] = ()
    noise_record_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _partition_must_hold(self) -> ClusteringReport:
        noise = list(self.noise_record_ids)
        if noise != sorted(noise) or len(set(noise)) != len(noise):
            raise ValueError("noise_record_ids must be sorted ascending and unique")
        for cluster in self.clusters:
            if cluster.cluster_id == NOISE_CLUSTER_ID:
                raise ValueError(f"a cluster may not use the reserved id {NOISE_CLUSTER_ID!r}")
        all_ids: list[str] = list(noise)
        for cluster in self.clusters:
            all_ids.extend(cluster.record_ids)
        if len(set(all_ids)) != len(all_ids):
            raise ValueError("cluster members and noise ids must be pairwise disjoint")
        total = sum(c.size for c in self.clusters) + len(noise)
        if total != self.records_in:
            raise ValueError(
                f"cluster sizes + noise = {total} != records_in ({self.records_in}) "
                "— a record neither clustered nor noise has been silently lost"
            )
        order = [(-c.size, c.cluster_id) for c in self.clusters]
        if order != sorted(order):
            raise ValueError("clusters must be sorted by (size desc, cluster_id asc)")
        return self


class StratumSample(BaseModel):
    """One stratum's quota and the ids actually sampled from it."""

    model_config = ConfigDict(frozen=True)

    cluster_id: str = Field(min_length=1)
    stratum_size: int = Field(ge=1)
    quota: int = Field(ge=0)
    sampled_record_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _quota_must_hold(self) -> StratumSample:
        ids = list(self.sampled_record_ids)
        if ids != sorted(ids) or len(set(ids)) != len(ids):
            raise ValueError("sampled_record_ids must be sorted ascending and unique")
        if len(ids) != self.quota:
            raise ValueError(f"{len(ids)} sampled ids but quota = {self.quota}")
        if self.quota > self.stratum_size:
            raise ValueError(f"quota ({self.quota}) exceeds stratum_size ({self.stratum_size})")
        return self


class SamplingReport(BaseModel):
    """The stratified-sampling accounting: quotas, seed, and every sampled id."""

    model_config = ConfigDict(frozen=True)

    seed: int
    sample_size_requested: int = Field(ge=1)
    records_in: int = Field(ge=0)
    total_sampled: int = Field(ge=0)
    strata: tuple[StratumSample, ...] = ()

    @model_validator(mode="after")
    def _quotas_must_add_up(self) -> SamplingReport:
        if sum(s.stratum_size for s in self.strata) != self.records_in:
            raise ValueError(
                f"stratum sizes sum to {sum(s.stratum_size for s in self.strata)}, "
                f"expected records_in = {self.records_in}"
            )
        expected = min(self.sample_size_requested, self.records_in)
        quota_sum = sum(s.quota for s in self.strata)
        if not (quota_sum == self.total_sampled == expected):
            raise ValueError(
                f"sum(quotas) = {quota_sum}, total_sampled = {self.total_sampled}, "
                f"min(requested, records_in) = {expected} — all three must be equal"
            )
        sampled: list[str] = []
        for stratum in self.strata:
            sampled.extend(stratum.sampled_record_ids)
        if len(set(sampled)) != len(sampled):
            raise ValueError("a record_id was sampled in more than one stratum")
        stratum_ids = [s.cluster_id for s in self.strata]
        if len(set(stratum_ids)) != len(stratum_ids):
            raise ValueError("stratum cluster_ids must be unique")
        order = [(-s.stratum_size, s.cluster_id) for s in self.strata]
        if order != sorted(order):
            raise ValueError("strata must be sorted by (stratum_size desc, cluster_id asc)")
        return self
