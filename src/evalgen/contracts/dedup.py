"""Self-validating dedup contracts: every drop is a typed, traceable entry (ADR-0002).

A record that vanishes between ingest and export without a typed, counted trace is the
dedup-stage version of ADR-0001's denominator lie. So the report refuses to exist
unless every sum holds — on construction AND on deserialization from disk (same
tamper-evidence as ``LogRecord``): ``records_in == records_out + id_collapsed +
exact_dropped + near_dropped``, entry tuples match their counters, every ``via_chain``
flag agrees with its similarity, and no surviving representative appears among the
dropped. ``DedupOutcome`` closes the cross-model gap from the other side: kept records
and the report's entries are checked against each other (no dropped record among kept,
no ghost survivor referenced).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evalgen.contracts.embeddings import EmbedderFingerprint
from evalgen.contracts.records import LogRecord, record_sort_key

#: Report-side rounding of similarities (serialization stability); decisions always
#: use full float64 — the rounding never moves a drop/keep decision (guarded in
#: ``dedup/neardup.py``: a similarity within 5e-7 of the threshold crashes loudly).
SIMILARITY_DECIMALS = 6

_SHA256_HEX_LEN = 64


class ExactDupEntry(BaseModel):
    """One exact-dup drop: which record, kept in favor of whom, under which hash.

    ``content_hash`` is publishable — it hashes post-redaction text only (ADR-0001
    guarantees no other kind of ``canonical_text`` exists).
    """

    model_config = ConfigDict(frozen=True)

    dropped_record_id: str = Field(min_length=1)
    kept_record_id: str = Field(min_length=1)
    content_hash: str

    @field_validator("content_hash")
    @classmethod
    def _must_be_full_sha256_hex(cls, value: str) -> str:
        if len(value) != _SHA256_HEX_LEN or any(c not in "0123456789abcdef" for c in value):
            raise ValueError(
                f"content_hash must be {_SHA256_HEX_LEN} lowercase hex chars "
                f"(full SHA-256 over canonical_text), got {value!r}"
            )
        return value

    @model_validator(mode="after")
    def _no_self_drop(self) -> ExactDupEntry:
        if self.dropped_record_id == self.kept_record_id:
            raise ValueError(f"entry drops {self.dropped_record_id!r} in favor of itself")
        return self


class NearDupEntry(BaseModel):
    """One near-dup drop: cosine to the KEPT representative + the chain-collapse flag.

    ``via_chain`` is True iff this record sits below the threshold w.r.t. its own
    survivor — it was dropped because a *chain* of pairwise matches merged their
    component (ADR-0002 rule 3: chain collapses are accepted but never hidden).
    """

    model_config = ConfigDict(frozen=True)

    dropped_record_id: str = Field(min_length=1)
    kept_record_id: str = Field(min_length=1)
    similarity: float = Field(ge=-1.0, le=1.0)
    via_chain: bool

    @model_validator(mode="after")
    def _no_self_drop(self) -> NearDupEntry:
        if self.dropped_record_id == self.kept_record_id:
            raise ValueError(f"entry drops {self.dropped_record_id!r} in favor of itself")
        return self


class DedupReport(BaseModel):
    """Accounting for one dedup pass; refuses to validate if anything doesn't add up."""

    model_config = ConfigDict(frozen=True)

    threshold: float = Field(ge=-1.0, le=1.0)
    embedder: EmbedderFingerprint

    records_in: int = Field(ge=0)
    records_out: int = Field(ge=0)
    #: Duplicate ``record_id``s collapsed before exact dedup — re-ingesting the same
    #: file twice is idempotent and *visible* (ADR-0002 rule 2). No per-entry trace:
    #: a duplicated id means identical origin+exchange by construction.
    id_collapsed: int = Field(ge=0)
    exact_dropped: int = Field(ge=0)
    near_dropped: int = Field(ge=0)
    near_dropped_via_chain: int = Field(ge=0)

    exact_entries: tuple[ExactDupEntry, ...] = ()
    near_entries: tuple[NearDupEntry, ...] = ()

    @model_validator(mode="after")
    def _must_add_up(self) -> DedupReport:
        total = self.records_out + self.id_collapsed + self.exact_dropped + self.near_dropped
        if self.records_in != total:
            raise ValueError(
                f"records_in ({self.records_in}) != out + id_collapsed + exact + near "
                f"({self.records_out} + {self.id_collapsed} + {self.exact_dropped} + "
                f"{self.near_dropped} = {total}) — every record must be accounted for"
            )
        if len(self.exact_entries) != self.exact_dropped:
            raise ValueError(
                f"{len(self.exact_entries)} exact entries but exact_dropped = "
                f"{self.exact_dropped} — every exact drop needs its traceable entry"
            )
        if len(self.near_entries) != self.near_dropped:
            raise ValueError(
                f"{len(self.near_entries)} near entries but near_dropped = "
                f"{self.near_dropped} — every near drop needs its traceable entry"
            )
        flagged = sum(e.via_chain for e in self.near_entries)
        if self.near_dropped_via_chain != flagged:
            raise ValueError(
                f"near_dropped_via_chain = {self.near_dropped_via_chain} but "
                f"{flagged} entries carry via_chain=True"
            )
        for entry in self.near_entries:
            # Tolerance-free on purpose: neardup.py computes via_chain from the FULL-
            # precision similarity and refuses to round across the threshold, so the
            # stored (rounded) similarity always lands on the same side.
            if entry.via_chain != (entry.similarity < self.threshold):
                raise ValueError(
                    f"entry for {entry.dropped_record_id!r}: via_chain={entry.via_chain} "
                    f"disagrees with similarity {entry.similarity} vs threshold "
                    f"{self.threshold} — the chain flag may never lie"
                )
        dropped = [e.dropped_record_id for e in self.exact_entries] + [
            e.dropped_record_id for e in self.near_entries
        ]
        dropped_set = set(dropped)
        if len(dropped_set) != len(dropped):
            raise ValueError("a record_id appears in more than one drop entry")
        kept_ids = {e.kept_record_id for e in self.exact_entries} | {
            e.kept_record_id for e in self.near_entries
        }
        overlap = sorted(kept_ids & dropped_set)
        if overlap:
            raise ValueError(
                f"kept_record_id(s) {overlap} also appear among dropped ids — "
                "a survivor cannot itself have been dropped"
            )
        for name, entries in (
            ("exact_entries", self.exact_entries),
            ("near_entries", self.near_entries),
        ):
            ids = [e.dropped_record_id for e in entries]
            if ids != sorted(ids):
                raise ValueError(f"{name} must be sorted by dropped_record_id ascending")
        return self


class DedupOutcome(BaseModel):
    """The public seam's return value: kept records in canonical order + the report.

    Beyond count/order checks, the outcome CROSS-CHECKS kept against the report
    (ADR-0002 rule 8, amended after the Phase 2 red team): a record cannot be both
    returned and reported dropped, and every ``kept_record_id`` a drop entry names
    must exist among the kept records — a forged or tampered outcome refuses to
    exist on deserialization, same discipline as ``LogRecord``.
    """

    model_config = ConfigDict(frozen=True)

    kept: tuple[LogRecord, ...]
    report: DedupReport

    @model_validator(mode="after")
    def _kept_must_match_report(self) -> DedupOutcome:
        if len(self.kept) != self.report.records_out:
            raise ValueError(
                f"{len(self.kept)} kept records but report.records_out = "
                f"{self.report.records_out}"
            )
        ids = [r.record_id for r in self.kept]
        if len(set(ids)) != len(ids):
            raise ValueError("kept records contain duplicate record_ids")
        keys = [record_sort_key(r) for r in self.kept]
        if keys != sorted(keys):
            raise ValueError(
                "kept records must be sorted by record_sort_key — the canonical "
                "downstream order (ADR-0002 rule 1)"
            )
        kept_ids = set(ids)
        entries = self.report.exact_entries + self.report.near_entries
        both = sorted({e.dropped_record_id for e in entries} & kept_ids)
        if both:
            raise ValueError(
                f"record_id(s) {both} are among kept records AND among the report's "
                "dropped entries — a dropped record cannot be returned"
            )
        ghosts = sorted({e.kept_record_id for e in entries} - kept_ids)
        if ghosts:
            raise ValueError(
                f"kept_record_id(s) {ghosts} are referenced by drop entries but exist "
                "nowhere among kept records — a report may not name ghost survivors"
            )
        return self
