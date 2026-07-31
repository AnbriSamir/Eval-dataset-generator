"""cluster_records: guards, shuffle invariance, determinism, and the `label < 0` rule.

The negative-label test monkeypatches HDBSCAN with a stub returning a −2 label —
sklearn can emit −2/−3 "infinite" labels for degenerate points, and treating only −1
as noise would silently invent a cluster (ADR-0002 rule 6).
"""

from __future__ import annotations

import random
from pathlib import Path

import numpy as np
import pytest

import evalgen.cluster.clustering as clustering_module
from conftest import make_record
from evalgen.cluster import HashingEmbedder, cluster_records
from evalgen.ingest import GenericMapping, load_generic_jsonl

FIXTURE = Path(__file__).resolve().parent.parent / "data" / "fixtures" / "cluster_demo.jsonl"

MAPPING = GenericMapping(
    input_key="q",
    output_key="a",
    timestamp_key="meta.ts",
    id_key="meta.id",
    task_key="meta.conv",
    metadata_keys=("channel", "lang"),
)


def fixture_records() -> list:
    records, report = load_generic_jsonl(FIXTURE, MAPPING)
    assert report.lines_rejected == 0
    return records


def test_empty_input_yields_empty_report() -> None:
    embedder = HashingEmbedder(dim=64)
    report = cluster_records([], embedder=embedder, min_cluster_size=5)
    assert report.records_in == 0
    assert report.clusters == ()
    assert report.noise_record_ids == ()


def test_fewer_records_than_min_cluster_size_all_noise_without_hdbscan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("HDBSCAN must not be instantiated below min_cluster_size")

    monkeypatch.setattr(clustering_module, "HDBSCAN", _boom)
    records = [make_record(line_no=i, input_text=f"lonely {i}") for i in range(1, 4)]
    embedder = HashingEmbedder(dim=64)
    report = cluster_records(records, embedder=embedder, min_cluster_size=5)
    assert report.records_in == 3
    assert report.clusters == ()
    assert sorted(report.noise_record_ids) == sorted(r.record_id for r in records)


def test_negative_labels_are_noise_not_only_minus_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    labels = np.array([0, 0, -1, -2, 1, 1])

    class _StubHDBSCAN:
        def __init__(self, **kwargs: object) -> None:
            pass

        def fit_predict(self, matrix: np.ndarray) -> np.ndarray:
            assert matrix.shape[0] == 6
            return labels

    monkeypatch.setattr(clustering_module, "HDBSCAN", _StubHDBSCAN)
    records = [make_record(line_no=i, input_text=f"text {i}") for i in range(1, 7)]
    embedder = HashingEmbedder(dim=64)
    report = cluster_records(records, embedder=embedder, min_cluster_size=2)

    ordered_ids = [r.record_id for r in records]  # already in record_sort_key order
    assert sorted(report.noise_record_ids) == sorted([ordered_ids[2], ordered_ids[3]])
    assert {c.size for c in report.clusters} == {2}
    assert len(report.clusters) == 2


def test_shuffled_input_yields_identical_report_bytes() -> None:
    records = fixture_records()
    embedder = HashingEmbedder(dim=512)
    baseline = cluster_records(records, embedder=embedder, min_cluster_size=5)
    for seed in (5, 23):
        shuffled = list(records)
        random.Random(seed).shuffle(shuffled)
        report = cluster_records(shuffled, embedder=embedder, min_cluster_size=5)
        assert report.model_dump_json() == baseline.model_dump_json()


def test_double_run_is_byte_identical() -> None:
    records = fixture_records()
    embedder = HashingEmbedder(dim=512)
    a = cluster_records(records, embedder=embedder, min_cluster_size=5)
    b = cluster_records(records, embedder=embedder, min_cluster_size=5)
    assert a.model_dump_json() == b.model_dump_json()


def test_fixture_forms_clusters_and_noise() -> None:
    # The committed fixture is designed to show real structure (ADR-0002 rule 9):
    # several intent clusters plus outlier noise — never an all-noise or all-in-one map.
    records = fixture_records()
    embedder = HashingEmbedder(dim=512)
    report = cluster_records(records, embedder=embedder, min_cluster_size=5)
    assert len(report.clusters) >= 3
    assert len(report.noise_record_ids) >= 1
    assert sum(c.size for c in report.clusters) + len(report.noise_record_ids) == len(records)


def test_report_metric_documents_the_l2norm_trick() -> None:
    records = fixture_records()[:6]
    embedder = HashingEmbedder(dim=64)
    report = cluster_records(records, embedder=embedder, min_cluster_size=5)
    assert report.metric == "euclidean_l2norm"
    assert report.embedder == embedder.fingerprint
