"""Generic JSONL adapter — any log source, via an EXPLICIT field mapping.

The mapping is declared up front (``GenericMapping``), never inferred: an adapter that
sniffs the schema from the first line of a file produces silently inconsistent records
on heterogeneous JSONL — the exact "guess-and-pray key probing" the ingest boundary
forbids. Absence of a required mapped key is a typed ``missing_field`` rejection, not a
fallback to some other key.

There is deliberately no "take everything" mode: fields survive into ``metadata`` only
through the opt-in ``metadata_keys`` tuple. Every field that outlives ingestion is a
declared decision — that is what keeps the redaction surface auditable.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from evalgen.contracts.records import LogRecord, SourceKind
from evalgen.contracts.reports import IngestReport, RejectReason, SkipReason
from evalgen.ingest.normalize import ReportBuilder, build_record
from evalgen.ingest.reader import read_source_lines
from evalgen.ingest.redaction import sanitize_text

#: Sentinel distinguishing "key absent from the object" (→ missing_field reject for
#: required keys) from "key present with a JSON null" (→ real-world empty, a skip).
_MISSING = object()


class GenericMapping(BaseModel):
    """Explicit source-shape declaration: which keys mean what.

    Keys are dot-paths into nested objects (``meta.ts``). Only ``input_key`` and
    ``output_key`` are required — a line missing them is rejected; the optional keys
    simply yield nothing when absent (they are opt-in extras, not promises).
    """

    model_config = ConfigDict(frozen=True)

    input_key: str = Field(min_length=1)
    output_key: str = Field(min_length=1)
    #: ISO-8601 strings only; anything else counts as ``timestamps_unparsed`` and the
    #: record ships with ``timestamp=None`` — a bad clock never drops data, and we
    #: never guess at epoch-seconds vs epoch-millis heuristics.
    timestamp_key: str | None = None
    #: Native event id / grouping id of the source — land in ``origin.span_id`` /
    #: ``origin.task_id`` so provenance can point back into the original system.
    id_key: str | None = None
    task_key: str | None = None
    #: Explicit opt-in survivors, stored under their dot-path name in ``metadata``.
    metadata_keys: tuple[str, ...] = ()


def _resolve(obj: dict[str, Any], dotted: str) -> object:
    """Walk a dot-path; return ``_MISSING`` when any segment is absent/not an object."""
    current: object = obj
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return _MISSING
        current = current[part]
    return current


def _as_text(value: object) -> str | None:
    """Coerce a resolved value to exchange text: strings pass, scalars stringify
    deterministically, structures/null yield None (a dict is not an exchange text —
    if a source nests its text, the mapping should dot-path INTO the structure)."""
    if isinstance(value, str):
        return value
    if isinstance(value, bool | int | float):
        return str(value)
    return None


def _exchange_text(value: object) -> str | None:
    """Coerce + sanitize a candidate exchange side; None when nothing judgeable remains.

    Candidacy MUST be decided on the SANITIZED text (same discipline as the TraceSpan
    adapter's ``_extract_text``): raw ``str.strip()`` does not see invisible format
    characters (``"\\u200b".isspace()`` is ``False``), so a field made only of them
    would pass a raw non-empty check, then normalize to ``""`` inside ``build_record``
    — whose loader-bug ``ValueError`` would abort the WHOLE file. One crafted (or
    accidental) invisible-only line must cost exactly one ``no_exchange`` skip, never
    the other N-1 records. ``build_record`` re-sanitizes idempotently — the double
    scrub is the documented price of loaders that pre-sanitize for candidacy.
    """
    text = _as_text(value)
    if text is None:
        return None
    clean = sanitize_text(text)
    return clean if clean.strip() else None


def load_generic_jsonl(
    path: str | Path,
    mapping: GenericMapping,
    *,
    source_name: str | None = None,
) -> tuple[list[LogRecord], IngestReport]:
    """Ingest an arbitrary JSONL source through an explicit mapping.

    Same guarantees as the TraceSpan adapter: basename as the default logical
    ``source_name`` (absolute paths are PII), file-order determinism, every line in
    exactly one report bucket, and all text funneled through ``build_record`` (the
    single normalize → redact → derive-id → freeze path).
    """
    file_path = Path(path)
    logical_name = source_name if source_name is not None else file_path.name
    builder = ReportBuilder(source_kind=SourceKind.GENERIC_JSONL, source_name=logical_name)
    records: list[LogRecord] = []

    for line in read_source_lines(file_path):
        if line.text is None:
            builder.reject(line.line_no, RejectReason.INVALID_ENCODING, line.decode_error or "")
            continue
        if not line.text.strip():
            builder.skip(SkipReason.BLANK_LINE)
            continue
        try:
            raw = json.loads(line.text)
        except json.JSONDecodeError as exc:
            builder.reject(line.line_no, RejectReason.INVALID_JSON, str(exc))
            continue
        if not isinstance(raw, dict):
            builder.reject(
                line.line_no,
                RejectReason.SCHEMA_MISMATCH,
                f"expected a JSON object, got {type(raw).__name__}",
            )
            continue

        # Required keys: absence is the source breaking its declared shape (reject);
        # presence with null/empty text is real-world emptiness (skip, by policy).
        required = (mapping.input_key, mapping.output_key)
        missing = [key for key in required if _resolve(raw, key) is _MISSING]
        if missing:
            builder.reject(
                line.line_no,
                RejectReason.MISSING_FIELD,
                f"mapped key(s) absent: {', '.join(missing)}",
            )
            continue
        input_text = _exchange_text(_resolve(raw, mapping.input_key))
        output_text = _exchange_text(_resolve(raw, mapping.output_key))
        if input_text is None or output_text is None:
            builder.skip(SkipReason.NO_EXCHANGE)
            continue

        timestamp: datetime | None = None
        if mapping.timestamp_key is not None:
            ts_value = _resolve(raw, mapping.timestamp_key)
            if isinstance(ts_value, str):
                try:
                    timestamp = datetime.fromisoformat(ts_value)
                except ValueError:
                    builder.timestamp_unparsed()
            elif ts_value is not _MISSING and ts_value is not None:
                # Numbers/structures: ISO-8601 only, by contract — no epoch guessing.
                builder.timestamp_unparsed()
            elif ts_value is _MISSING or ts_value is None:
                # The mapping promised a timestamp this line doesn't honor — warn,
                # don't drop: a missing clock must never bias the sampled distribution.
                builder.timestamp_unparsed()

        span_id = _as_text(_resolve(raw, mapping.id_key)) if mapping.id_key else None
        task_id = _as_text(_resolve(raw, mapping.task_key)) if mapping.task_key else None

        metadata: dict[str, object] = {}
        for key in mapping.metadata_keys:
            value = _as_text(_resolve(raw, key))
            if value is not None:
                metadata[key] = value

        records.append(
            build_record(
                source_kind=SourceKind.GENERIC_JSONL,
                source_name=logical_name,
                line_no=line.line_no,
                input_text=input_text,
                output_text=output_text,
                span_id=span_id,
                task_id=task_id,
                timestamp=timestamp,
                metadata=metadata,
            )
        )
        builder.normalized()

    return records, builder.build()
