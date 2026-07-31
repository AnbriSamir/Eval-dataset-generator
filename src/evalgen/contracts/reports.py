"""The self-validating ingestion report: nothing is ever silently dropped (ADR-0001 rule 4).

A malformed line that vanishes without a count makes the exported dataset's denominator
a lie — "n records from source X" becomes unverifiable and the provenance story
collapses. So every source line lands in exactly one of three buckets:

- **normalized** — became a ``LogRecord``;
- **rejected** — data we could not read (typed :class:`RejectReason`);
- **skipped** — data we read and *chose* not to take, by declared policy
  (typed :class:`SkipReason`).

The distinction matters downstream: a rising reject rate means the source broke; a
rising skip rate means the policy filters more — conflating them would hide both.

``IngestReport`` lives in ``contracts`` because export's ``meta.json`` will embed it as
provenance (Phase 5). Its ``model_validator`` refuses to construct a report whose
buckets do not sum to ``lines_read`` — a report that doesn't add up cannot exist, even
when deserialized from disk.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from evalgen.contracts.records import SourceKind

#: Reject samples are capped so a wholly-corrupt multi-gigabyte file cannot balloon the
#: report; first-in-file order keeps the sample deterministic (no reservoir randomness).
MAX_REJECT_SAMPLES = 20

#: Sample details are truncated (after scrubbing — order matters, truncation must never
#: cut a secret back into visibility) so parse errors stay readable, not multi-KB dumps.
MAX_REJECT_DETAIL_LEN = 200


class RejectReason(StrEnum):
    """Why a line could not be read at all (the source's fault, not policy)."""

    #: The line is not valid UTF-8 (files are decoded per line — one bad byte rejects
    #: one line, never crashes the file).
    INVALID_ENCODING = "invalid_encoding"
    #: The line is not parseable JSON.
    INVALID_JSON = "invalid_json"
    #: Parsed, but not an object / fails source-schema validation (e.g. a TraceSpan
    #: with an unknown status).
    SCHEMA_MISMATCH = "schema_mismatch"
    #: A key required by the configured mapping is absent from the object.
    MISSING_FIELD = "missing_field"


class SkipReason(StrEnum):
    """Why a readable line was deliberately not taken (declared policy, not failure)."""

    BLANK_LINE = "blank_line"
    #: TraceSpan action outside the candidate allowlist (control-flow spans carry no
    #: judgeable exchange — volume is not coverage).
    ACTION_NOT_CANDIDATE = "action_not_candidate"
    #: TraceSpan status is error/blocked — a failure-mode dataset is a different
    #: taxonomy and a deliberate future flag, not an accidental mixture.
    STATUS_NOT_OK = "status_not_ok"
    #: No non-empty input/output pair extractable — real-world empty, not malformed.
    NO_EXCHANGE = "no_exchange"


class RejectSample(BaseModel):
    """One rejected line, kept as evidence: where, why, and a *scrubbed* detail.

    ``detail`` is scrubbed by ingest before this model is built — parse-error messages
    embed the raw line (pydantic's ``input_value=...``), so the redactor must run on
    them too. Raw line content is never stored.
    """

    model_config = ConfigDict(frozen=True)

    line_no: int = Field(ge=1)
    reason: RejectReason
    detail: str = Field(max_length=MAX_REJECT_DETAIL_LEN)


class IngestReport(BaseModel):
    """Accounting for one ingested source; refuses to validate if it doesn't add up."""

    model_config = ConfigDict(frozen=True)

    source_kind: SourceKind
    #: Logical name (basename by default) — same PII rule as ``RecordOrigin``.
    source_name: str = Field(min_length=1)

    lines_read: int = Field(ge=0)
    records_normalized: int = Field(ge=0)
    lines_rejected: int = Field(ge=0)
    lines_skipped: int = Field(ge=0)

    #: Per-reason counters — only reasons that occurred appear (deterministic order:
    #: enum declaration order, fixed by the builders).
    rejects_by_reason: dict[RejectReason, int] = Field(default_factory=dict)
    skips_by_reason: dict[SkipReason, int] = Field(default_factory=dict)

    #: Up to MAX_REJECT_SAMPLES rejected lines, first-in-file order, details scrubbed.
    reject_samples: tuple[RejectSample, ...] = Field(default=(), max_length=MAX_REJECT_SAMPLES)

    #: Lines whose configured timestamp could not be parsed — a bad clock demotes
    #: ``timestamp`` to ``None`` (a warning), it does not drop data.
    timestamps_unparsed: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _buckets_must_sum(self) -> IngestReport:
        """The three-bucket invariant: a report that cannot account for every single
        line refuses to exist. This is the structural guarantee behind "nothing is
        silently dropped" — it holds even for reports deserialized from meta.json."""
        total = self.records_normalized + self.lines_rejected + self.lines_skipped
        if self.lines_read != total:
            raise ValueError(
                f"lines_read ({self.lines_read}) != normalized + rejected + skipped "
                f"({self.records_normalized} + {self.lines_rejected} + {self.lines_skipped}"
                f" = {total}) — every line must land in exactly one bucket"
            )
        if sum(self.rejects_by_reason.values()) != self.lines_rejected:
            raise ValueError(
                f"rejects_by_reason sums to {sum(self.rejects_by_reason.values())}, "
                f"expected lines_rejected = {self.lines_rejected}"
            )
        if sum(self.skips_by_reason.values()) != self.lines_skipped:
            raise ValueError(
                f"skips_by_reason sums to {sum(self.skips_by_reason.values())}, "
                f"expected lines_skipped = {self.lines_skipped}"
            )
        if len(self.reject_samples) > self.lines_rejected:
            raise ValueError(
                f"{len(self.reject_samples)} reject samples but only "
                f"{self.lines_rejected} rejected lines"
            )
        return self
