"""The frozen ``LogRecord`` atom every downstream phase consumes (ADR-0001).

Whatever shape this file freezes is effectively immutable: dedup hashes it, clustering
embeds it, the judge reads it, exports trace back to it. Three load-bearing choices live
here so no downstream module ever re-decides them:

1. **The canonical texts are decided once.** ``canonical_text`` (dedup) and
   ``cluster_text`` (coverage) are read-only properties — if each consumer re-derived
   "the text of a record", dedup and clustering would silently diverge and no two runs
   would agree on what a duplicate even is.
2. **Ids are content-derived and computed over REDACTED text.** ``record_id`` is a pure
   function of origin + redacted exchange — never uuid4 (two identical runs would
   disagree on every id), never raw text (a published id would embed secret bits and
   rotate whenever the secret does).
3. **A record whose id does not match its content is unrepresentable.** The
   ``model_validator`` recomputes the id on every construction — including
   deserialization from disk — so tampering (or a buggy constructor) is a
   ``ValidationError``, not a silent corruption.

What the validator *cannot* check is that redaction actually ran: contracts must not
know the redaction patterns (module-boundary rule — contracts imports no one). That half
of the invariant is enforced by ``ingest.normalize.build_record`` being the only
production constructor of ``LogRecord``, plus the adversarial test battery.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: Unit separator (same trick as the sibling repo's ``ids.py``): it cannot appear in
#: normal text, so joined parts cannot collide by boundary shift ("ab"+"c" vs "a"+"bc").
CANONICAL_SEP = "\x1f"

_ID_PREFIX = "rec-"
#: 16 hex chars = 64 bits (vs the sibling's 12/48): records number in the millions per
#: corpus, and at 10^6 records the collision odds are ~2.7e-8 at 64 bits vs ~0.2% at 48
#: — cheap insurance on an identifier that is published in dedup reports and exports.
_ID_HEX_LEN = 16


class SourceKind(StrEnum):
    """Where a record came from — part of its identity (ADR-0001 rule 2)."""

    TRACESPAN = "tracespan"
    GENERIC_JSONL = "generic_jsonl"


class RecordOrigin(BaseModel):
    """Provenance of one record: enough to trace it back to its source line.

    ``source_name`` is a LOGICAL name — by default the file's basename, never the
    absolute path: ``C:\\Users\\<name>\\...`` embeds a username and is itself PII.
    """

    model_config = ConfigDict(frozen=True)

    source_kind: SourceKind
    source_name: str = Field(min_length=1)
    #: 1-based line number in the source file (JSONL: one line, one candidate).
    line_no: int = Field(ge=1)
    #: Native id of the source event (``TraceSpan.span_id`` or a generic ``id_key``).
    span_id: str | None = None
    task_id: str | None = None


def derive_record_id(origin: RecordOrigin, input_text: str, output_text: str) -> str:
    """Return the deterministic id for a (redacted) exchange at a given origin.

    ``"rec-" + sha256(source_kind ␟ source_name ␟ line_no ␟ input ␟ output)[:16]``.

    Callers MUST pass post-normalization, post-redaction texts — this function is pure
    and cannot verify that (contracts must not import the redaction patterns); the
    single-constructor discipline in ``ingest`` guarantees it in production.

    Origin is part of identity on purpose: two occurrences of the same exchange on
    different lines get distinct ids, so a dedup report can say "dropped rec-a…, kept
    rec-b…" with real references — while re-ingesting the same file is idempotent.
    Content equality is the dedup hash's job (over ``canonical_text``), not the id's.
    ``timestamp`` and ``metadata`` are deliberately excluded: volatile / auxiliary
    values must not fabricate identity distinctions.
    """
    parts = [
        origin.source_kind.value,
        origin.source_name,
        str(origin.line_no),
        input_text,
        output_text,
    ]
    digest = hashlib.sha256(CANONICAL_SEP.join(parts).encode("utf-8")).hexdigest()
    return _ID_PREFIX + digest[:_ID_HEX_LEN]


def record_sort_key(record: LogRecord) -> tuple[str, int, str]:
    """THE canonical total order of Phase 2+ (ADR-0002 rule 1).

    Used for: dedup survivor choice, near-dup representative choice, embedding-matrix
    row order, report entry order. Content-derived (origin is part of the record) —
    input list order is never load-bearing. Human-readable: "the earliest line of the
    lexicographically-first source" wins.
    """
    return (record.origin.source_name, record.origin.line_no, record.record_id)


class LogRecord(BaseModel):
    """One judgeable input/output exchange, normalized, redacted, frozen.

    The explicit pair (rather than a generic payload dict) makes a nothing-to-judge
    record unrepresentable (``min_length=1``) and gives every consumer one answer:
    dedup hashes ``canonical_text``, clustering embeds ``cluster_text``, the judge
    reads both sides separately.
    """

    model_config = ConfigDict(frozen=True)

    record_id: str
    origin: RecordOrigin
    #: Optional because ``TraceSpan`` carries no timestamp — and NEVER defaulted from
    #: the wall clock: two runs over the same file must produce byte-identical records.
    timestamp: datetime | None = None
    input_text: str = Field(min_length=1)
    output_text: str = Field(min_length=1)
    #: Flat, stringified, already-redacted auxiliary signals (agent, action, model_id,
    #: token counts…). Flat by design: arbitrary nesting is what redaction would have
    #: to chase forever, and what made a generic payload dict a rejected option.
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _id_must_match_content(self) -> LogRecord:
        """A record whose id disagrees with its content refuses to exist.

        Runs on every construction path — including ``model_validate_json`` on data
        read back from disk — so a tampered or hand-forged record is a
        ``ValidationError``, never a silent corruption (tamper detection for free).
        """
        expected = derive_record_id(self.origin, self.input_text, self.output_text)
        if self.record_id != expected:
            raise ValueError(
                f"record_id {self.record_id!r} does not match content "
                f"(expected {expected!r}) — ids are content-derived (ADR-0001 rule 2)"
            )
        return self

    @property
    def canonical_text(self) -> str:
        """THE text dedup hashes (exact and near-dup).

        Input and output joined on the unit separator: an exchange repeated verbatim
        inflates every downstream metric, while the same input with a different output
        is a *distinct* eval case and must survive dedup. The separator prevents
        boundary-shift collisions between the two sides.
        """
        return self.input_text + CANONICAL_SEP + self.output_text

    @property
    def cluster_text(self) -> str:
        """THE text clustering embeds for coverage.

        Coverage means covering the *traffic* distribution, and traffic is defined by
        what came in — not by output phrasing, which would split one intent across
        clusters.
        """
        return self.input_text
