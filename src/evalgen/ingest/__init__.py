"""Log loaders (TraceSpan JSONL from multi-agent-orchestrator + generic adapter),
normalization, and REDACTION at the boundary.

Secrets/PII never persist downstream of this package: every string funnels through
``normalize.build_record`` (normalize → redact → derive-id → freeze), which is the only
production constructor of ``LogRecord`` (ADR-0001).
"""

from evalgen.ingest.generic import GenericMapping, load_generic_jsonl
from evalgen.ingest.normalize import ReportBuilder, build_record
from evalgen.ingest.redaction import normalize_text, sanitize_text, scrub_value
from evalgen.ingest.tracespan import DEFAULT_CANDIDATE_ACTIONS, load_tracespan_jsonl

__all__ = [
    "DEFAULT_CANDIDATE_ACTIONS",
    "GenericMapping",
    "ReportBuilder",
    "build_record",
    "load_generic_jsonl",
    "load_tracespan_jsonl",
    "normalize_text",
    "sanitize_text",
    "scrub_value",
]
