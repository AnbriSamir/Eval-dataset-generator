"""TraceSpan JSONL adapter — first-class input from ``multi-agent-orchestrator``.

The sibling repo emits frozen Pydantic ``TraceSpan`` lines (span_id, task_id, agent,
action, status, cost fields, free-form payload). We validate against a LOCAL structural
mirror of that schema rather than importing across repos: a cross-repo import would
couple the two repos' release cycles and break this repo's offline test
discipline; a mirror pins the shape we actually depend on, and a sibling schema change
surfaces as an explicit ``schema_mismatch`` count instead of an ImportError.

Candidacy (ADR-0001 options §2) is a conjunction, each miss counted under its own
``SkipReason`` so the report can distinguish "mostly control flow" from "policy filtered
errors" from "exchange keys missing":

1. ``action`` in the candidate allowlist — the sibling's graph emits ``intake``,
   ``select``, memory ``retrieve``, ``synthesize``… spans whose payloads are pure
   bookkeeping; only the decision points (default plan/execute/verdict) can carry a
   judgeable exchange. Volume is not coverage: control-flow spans would flood dedup
   with near-identical strings and distort cluster densities.
2. ``status == "ok"`` — error/blocked spans are a failure-mode taxonomy, a deliberate
   future flag and a *different* dataset, not an accidental mixture.
3. A non-empty input/output pair extractable from ``payload`` by ordered key
   preference — explicit key lists, never guess-and-pray probing over arbitrary keys.
"""

from __future__ import annotations

import json
from collections.abc import Collection
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from evalgen.contracts.records import LogRecord, SourceKind
from evalgen.contracts.reports import IngestReport, RejectReason, SkipReason
from evalgen.ingest.normalize import ReportBuilder, build_record
from evalgen.ingest.reader import read_source_lines
from evalgen.ingest.redaction import sanitize_text

#: The three decision points where the sibling's orchestrator produces judgeable
#: content. Exposed (and overridable per call) rather than hardcoded in the loader —
#: the allowlist must be revisited if the sibling adds exchange-bearing actions, and
#: the CLI will surface it as configuration (ADR-0001 consequence).
DEFAULT_CANDIDATE_ACTIONS: frozenset[str] = frozenset({"plan", "execute", "verdict"})

#: Ordered key preferences for extracting the exchange from ``payload`` — first
#: PRESENT string-valued key decides each side (an empty value then means "this span
#: genuinely has no exchange", it does not fall through to a lesser key).
_INPUT_KEYS: tuple[str, ...] = ("input", "prompt", "task", "question")
_OUTPUT_KEYS: tuple[str, ...] = ("output", "response", "content", "answer", "result")


class SpanStatusMirror(StrEnum):
    """Mirror of the sibling's ``SpanStatus`` (contracts/trace.py)."""

    OK = "ok"
    ERROR = "error"
    BLOCKED = "blocked"


class TraceSpanMirror(BaseModel):
    """Structural mirror of the sibling's ``TraceSpan`` — field-for-field.

    Unknown extra fields are ignored (pydantic default): the sibling adding a field
    must not start rejecting every span; a *changed* or *removed* field we depend on
    still fails validation loudly as ``schema_mismatch``.
    """

    model_config = ConfigDict(frozen=True)

    span_id: str
    parent_span_id: str | None = None
    task_id: str
    agent: str
    action: str
    status: SpanStatusMirror
    model_id: str | None = None
    tokens_in: int | None = Field(default=None, ge=0)
    tokens_out: int | None = Field(default=None, ge=0)
    cost_usd: float | None = Field(default=None, ge=0)
    latency_ms: float | None = Field(default=None, ge=0)
    payload: dict[str, Any] = Field(default_factory=dict)


def _extract_text(payload: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    """Return the sanitized text of the first present str-valued preference key.

    Present-but-not-a-string keys are treated as absent (the sibling's builder
    stringifies every payload value, so a non-str here is another producer's
    structure, not text). Present-but-empty (after sanitization) returns None —
    the span has no exchange; we do not scavenge lesser keys for one.
    """
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str):
            text = sanitize_text(value)
            return text if text.strip() else None
    return None


def load_tracespan_jsonl(
    path: str | Path,
    *,
    source_name: str | None = None,
    candidate_actions: Collection[str] | None = None,
) -> tuple[list[LogRecord], IngestReport]:
    """Mine a TraceSpan JSONL file into records + a self-validating report.

    ``source_name`` defaults to the file's BASENAME — the absolute path embeds a
    username and is itself PII (ADR-0001 rule 1). Records come out in file order
    (deterministic; two runs over the same bytes are byte-identical). ``timestamp``
    is always ``None``: TraceSpan carries no clock field and we never invent one.
    """
    file_path = Path(path)
    logical_name = source_name if source_name is not None else file_path.name
    allowlist = frozenset(
        candidate_actions if candidate_actions is not None else DEFAULT_CANDIDATE_ACTIONS
    )
    builder = ReportBuilder(source_kind=SourceKind.TRACESPAN, source_name=logical_name)
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
        try:
            span = TraceSpanMirror.model_validate(raw)
        except ValidationError as exc:
            # str(exc) embeds raw field values — the builder scrubs it before storing.
            builder.reject(line.line_no, RejectReason.SCHEMA_MISMATCH, str(exc))
            continue

        # Candidacy conjunction — each miss lands under its own SkipReason.
        if span.action not in allowlist:
            builder.skip(SkipReason.ACTION_NOT_CANDIDATE)
            continue
        if span.status is not SpanStatusMirror.OK:
            builder.skip(SkipReason.STATUS_NOT_OK)
            continue
        input_text = _extract_text(span.payload, _INPUT_KEYS)
        output_text = _extract_text(span.payload, _OUTPUT_KEYS)
        if input_text is None or output_text is None:
            builder.skip(SkipReason.NO_EXCHANGE)
            continue

        # Flat auxiliary signals only — fixed insertion order, optionals included only
        # when present so absent values don't serialize as the string "None".
        metadata: dict[str, object] = {
            "agent": span.agent,
            "action": span.action,
            "status": span.status.value,
        }
        for key, value in (
            ("model_id", span.model_id),
            ("tokens_in", span.tokens_in),
            ("tokens_out", span.tokens_out),
            ("cost_usd", span.cost_usd),
            ("latency_ms", span.latency_ms),
        ):
            if value is not None:
                metadata[key] = value

        records.append(
            build_record(
                source_kind=SourceKind.TRACESPAN,
                source_name=logical_name,
                line_no=line.line_no,
                input_text=input_text,
                output_text=output_text,
                span_id=span.span_id,
                task_id=span.task_id,
                metadata=metadata,
            )
        )
        builder.normalized()

    return records, builder.build()
