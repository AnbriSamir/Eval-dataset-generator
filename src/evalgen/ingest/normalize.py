"""The single production constructor of ``LogRecord`` + the report accumulator.

``build_record`` is the ONE place where the load-bearing order lives:

    normalize -> redact -> derive record_id -> freeze

Both adapters funnel through it, so the invariant "no unredacted text ever reaches a
hash, an embedding, the judge, or disk" is enforced by construction — not by every
loader remembering to be careful. The contracts-side ``model_validator`` proves
id == content; THIS module is the half it cannot prove (that redaction ran), which is
why nothing else in production code may instantiate ``LogRecord`` directly.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from evalgen.contracts.records import LogRecord, RecordOrigin, SourceKind, derive_record_id
from evalgen.contracts.reports import (
    MAX_REJECT_DETAIL_LEN,
    MAX_REJECT_SAMPLES,
    IngestReport,
    RejectReason,
    RejectSample,
    SkipReason,
)
from evalgen.ingest.redaction import sanitize_text


def build_record(
    *,
    source_kind: SourceKind,
    source_name: str,
    line_no: int,
    input_text: str,
    output_text: str,
    span_id: str | None = None,
    task_id: str | None = None,
    timestamp: datetime | None = None,
    metadata: Mapping[str, object] | None = None,
) -> LogRecord:
    """Normalize + redact every string, THEN derive the id, THEN freeze the record.

    Accepts raw or already-sanitized text interchangeably: ``sanitize_text`` is
    idempotent (pinned by a test), so loaders that pre-sanitized for their candidacy
    check pay a re-scrub instead of risking a loader that forgets. ``source_name``,
    ``span_id`` and ``task_id`` are sanitized too — a caller passing an absolute path
    or a PII-bearing native id must not be able to smuggle it into provenance.

    Metadata values are stringified then sanitized (keys included) and stored in
    sorted-key order, so the serialized record is byte-identical regardless of the
    caller's insertion order (dict-order nondeterminism must never reach disk).

    Raises ``ValueError`` on an exchange that is empty after sanitization: loaders
    must have skipped it as ``no_exchange`` — reaching here empty is a loader bug,
    not a data condition.
    """
    clean_input = sanitize_text(input_text)
    clean_output = sanitize_text(output_text)
    if not clean_input.strip() or not clean_output.strip():
        raise ValueError(
            "build_record received an empty exchange — loaders must skip these as "
            "no_exchange before construction (ADR-0001 rule 4)"
        )
    origin = RecordOrigin(
        source_kind=source_kind,
        source_name=sanitize_text(source_name),
        line_no=line_no,
        span_id=sanitize_text(span_id) if span_id is not None else None,
        task_id=sanitize_text(task_id) if task_id is not None else None,
    )
    clean_metadata = {
        sanitize_text(str(key)): sanitize_text(str(value))
        for key, value in (metadata or {}).items()
    }
    return LogRecord(
        record_id=derive_record_id(origin, clean_input, clean_output),
        origin=origin,
        timestamp=timestamp,
        input_text=clean_input,
        output_text=clean_output,
        metadata=dict(sorted(clean_metadata.items())),
    )


class ReportBuilder:
    """Mutable accumulator behind the frozen ``IngestReport``.

    Every line must land through exactly one of ``normalized`` / ``reject`` / ``skip``
    — ``lines_read`` is the sum of those calls, so the three-bucket invariant holds by
    construction and the report's own validator re-checks it at the seam (defense in
    depth: a future refactor that double-counts a line fails validation immediately).
    """

    def __init__(self, *, source_kind: SourceKind, source_name: str) -> None:
        self._source_kind = source_kind
        self._source_name = sanitize_text(source_name)
        self._normalized = 0
        self._rejects: dict[RejectReason, int] = {}
        self._skips: dict[SkipReason, int] = {}
        self._samples: list[RejectSample] = []
        self._timestamps_unparsed = 0

    def normalized(self) -> None:
        self._normalized += 1

    def reject(self, line_no: int, reason: RejectReason, detail: str) -> None:
        """Count a rejection and keep evidence (first MAX_REJECT_SAMPLES, file order).

        The detail is scrubbed BEFORE truncation — parse-error messages embed the raw
        line (pydantic prints ``input_value=...``), and truncating first could slice a
        secret in a way the patterns no longer recognize.
        """
        self._rejects[reason] = self._rejects.get(reason, 0) + 1
        if len(self._samples) < MAX_REJECT_SAMPLES:
            self._samples.append(
                RejectSample(
                    line_no=line_no,
                    reason=reason,
                    detail=sanitize_text(detail)[:MAX_REJECT_DETAIL_LEN],
                )
            )

    def skip(self, reason: SkipReason) -> None:
        self._skips[reason] = self._skips.get(reason, 0) + 1

    def timestamp_unparsed(self) -> None:
        self._timestamps_unparsed += 1

    def build(self) -> IngestReport:
        """Freeze the accounting. Per-reason dicts are emitted in enum declaration
        order so two runs over the same file serialize byte-identically."""
        rejected = sum(self._rejects.values())
        skipped = sum(self._skips.values())
        return IngestReport(
            source_kind=self._source_kind,
            source_name=self._source_name,
            lines_read=self._normalized + rejected + skipped,
            records_normalized=self._normalized,
            lines_rejected=rejected,
            lines_skipped=skipped,
            rejects_by_reason={r: self._rejects[r] for r in RejectReason if r in self._rejects},
            skips_by_reason={s: self._skips[s] for s in SkipReason if s in self._skips},
            reject_samples=tuple(self._samples),
            timestamps_unparsed=self._timestamps_unparsed,
        )
