"""Shared Pydantic models: LogRecord, Cluster, LabeledExample, LabelTaxonomy,
AgreementReport, ExportManifest.

Imported by everyone, imports no one (pinned by a test).
"""

from evalgen.contracts.records import (
    CANONICAL_SEP,
    LogRecord,
    RecordOrigin,
    SourceKind,
    derive_record_id,
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
    "IngestReport",
    "LogRecord",
    "RecordOrigin",
    "RejectReason",
    "RejectSample",
    "SkipReason",
    "SourceKind",
    "derive_record_id",
]
