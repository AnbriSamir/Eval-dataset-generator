"""Shared Pydantic models: LogRecord, dedup/clustering/sampling/calibration reports,
the Embedder Protocol seam — plus (later phases) LabeledExample, LabelTaxonomy,
AgreementReport, ExportManifest.

Imported by everyone, imports no one (pinned by a test).
"""

from evalgen.contracts.calibration import (
    LabeledPair,
    ThresholdCalibrationReport,
    ThresholdCandidate,
)
from evalgen.contracts.clustering import (
    NOISE_CLUSTER_ID,
    Cluster,
    ClusteringReport,
    SamplingReport,
    StratumSample,
    derive_cluster_id,
)
from evalgen.contracts.dedup import (
    SIMILARITY_DECIMALS,
    DedupOutcome,
    DedupReport,
    ExactDupEntry,
    NearDupEntry,
)
from evalgen.contracts.embeddings import Embedder, EmbedderFingerprint
from evalgen.contracts.records import (
    CANONICAL_SEP,
    LogRecord,
    RecordOrigin,
    SourceKind,
    derive_record_id,
    record_sort_key,
)
from evalgen.contracts.reports import (
    MAX_REJECT_DETAIL_LEN,
    MAX_REJECT_SAMPLES,
    IngestReport,
    RejectReason,
    RejectSample,
    SkipReason,
)

__all__ = [
    "CANONICAL_SEP",
    "MAX_REJECT_DETAIL_LEN",
    "MAX_REJECT_SAMPLES",
    "NOISE_CLUSTER_ID",
    "SIMILARITY_DECIMALS",
    "Cluster",
    "ClusteringReport",
    "DedupOutcome",
    "DedupReport",
    "Embedder",
    "EmbedderFingerprint",
    "ExactDupEntry",
    "IngestReport",
    "LabeledPair",
    "LogRecord",
    "NearDupEntry",
    "RecordOrigin",
    "RejectReason",
    "RejectSample",
    "SamplingReport",
    "SkipReason",
    "SourceKind",
    "StratumSample",
    "ThresholdCalibrationReport",
    "ThresholdCandidate",
    "derive_cluster_id",
    "derive_record_id",
    "record_sort_key",
]
