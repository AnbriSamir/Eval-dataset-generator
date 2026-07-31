"""The judge seam and the self-validating labeling contracts (ADR-0003 rules 2–3, 7–8).

Four load-bearing choices live here:

1. **The Judge Protocol is the narrowest possible channel** — two strings in, a
   ``Judgment`` out. The signature cannot transport a human label, a metadata hint, or a
   cluster assignment: blindness by type, not by discipline (ADR-0003 rule 3).
2. **``JudgeVerdict`` IS the ``output_format`` schema.** Its ``StrEnum`` fields compile
   to closed JSON-schema enums the API cannot violate — an out-of-taxonomy label is
   unrepresentable at the boundary (rule 2).
3. **Every non-label outcome is a typed, counted, id-traceable entry.** The five-bucket
   ``LabelingReport`` refuses to exist unless every sum holds — a silently swallowed
   refusal would bias κ (refusals correlate with hard cases; dropping them is
   unintentional cherry-picking, ADR-0003 failure mode 3).
4. **The anti-leak key is content, not id.** ``FewShotExample.content_hash`` is the
   EXACT exact-dedup identity function (full SHA-256 over ``input ␟ output``, ADR-0002
   rule 2) — one identity function serves the labeling-time gate (engine skip), the
   Phase 5 export gate (export ∩ few-shots = ∅), and the Phase 4 κ-join corollary.
"""

from __future__ import annotations

import hashlib
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evalgen.contracts.records import CANONICAL_SEP
from evalgen.contracts.taxonomy import JudgeConfidence, OutcomeLabel, TaskTypeLabel

#: Bounds the free-text rationale (never enters a κ; drill-down evidence only).
MAX_RATIONALE_LEN = 2000

#: Failure details are truncated (SDK error strings carry request ids, not record text —
#: and everything the judge saw/echoed is post-redaction by ADR-0001 construction, so no
#: re-scrub is needed; truncation bounds them regardless).
MAX_FAILURE_DETAIL_LEN = 200

_FEW_SHOT_ID_PREFIX = "fs-"
_FEW_SHOT_ID_HEX_LEN = 16
_SHA256_HEX_LEN = 64


class JudgeVerdict(BaseModel):
    """THE structured-output schema sent to the API (``output_format=JudgeVerdict``).

    ``min/max_length`` on ``rationale`` are stripped server-side by the SDK and
    validated client-side — a violating response surfaces as a typed parse failure,
    never a silently accepted label.
    """

    model_config = ConfigDict(frozen=True)

    task_type: TaskTypeLabel
    outcome: OutcomeLabel
    confidence: JudgeConfidence
    rationale: str = Field(min_length=1, max_length=MAX_RATIONALE_LEN)


class Judgment(BaseModel):
    """What ``Judge.judge`` returns: the verdict + the model that ACTUALLY served it.

    ``model_id`` comes from the response envelope (``response.model``) or the fake's
    constant — never from the caller's config echo. The fingerprint records the
    *requested* model; a discrepancy is visible, not hidden (ADR-0003 rule 3).
    """

    model_config = ConfigDict(frozen=True)

    verdict: JudgeVerdict
    model_id: str = Field(min_length=1)


def derive_few_shot_id(input_text: str, output_text: str, verdict: JudgeVerdict) -> str:
    """``"fs-" + sha256(input ␟ output ␟ task_type ␟ outcome ␟ confidence ␟ rationale)[:16]``.

    Content-derived (house style: ``rec-``, ``cl-``, ``tax-``): the id covers the gold
    verdict too, so editing an example's label is a *different* example — provenance
    (``JudgeFingerprint.few_shot_ids``) can never silently point at edited guidance.
    """
    parts = [
        input_text,
        output_text,
        verdict.task_type.value,
        verdict.outcome.value,
        verdict.confidence.value,
        verdict.rationale,
    ]
    digest = hashlib.sha256(CANONICAL_SEP.join(parts).encode("utf-8")).hexdigest()
    return _FEW_SHOT_ID_PREFIX + digest[:_FEW_SHOT_ID_HEX_LEN]


class FewShotExample(BaseModel):
    """One committed, contract-validated gold example; tampering refuses to load."""

    model_config = ConfigDict(frozen=True)

    few_shot_id: str
    input_text: str = Field(min_length=1)
    output_text: str = Field(min_length=1)
    verdict: JudgeVerdict
    #: Free provenance note (e.g. "planted collision twin") — not part of the id.
    note: str = ""

    @model_validator(mode="after")
    def _id_must_match_content(self) -> FewShotExample:
        expected = derive_few_shot_id(self.input_text, self.output_text, self.verdict)
        if self.few_shot_id != expected:
            raise ValueError(
                f"few_shot_id {self.few_shot_id!r} does not match content "
                f"(expected {expected!r}) — few-shot ids are content-derived (ADR-0003 rule 8)"
            )
        return self

    @property
    def content_hash(self) -> str:
        """Full SHA-256 hex over ``input ␟ output`` — the EXACT exact-dedup identity
        function over ``canonical_text`` (ADR-0002 rule 2). This is the leakage-gate
        join key: labeling-time skip now, ``export ∩ few-shots = ∅`` at Phase 5."""
        joined = self.input_text + CANONICAL_SEP + self.output_text
        return hashlib.sha256(joined.encode("utf-8")).hexdigest()


class JudgeFingerprint(BaseModel):
    """The measuring instrument's identity (``EmbedderFingerprint`` twin, ADR-0002
    rule 5): every ``LabelingReport`` names the judge, the requested model, the
    taxonomy, the prompt hash, and the few-shot set that produced its labels."""

    model_config = ConfigDict(frozen=True)

    #: "anthropic" | "fake" (tests may use "stub").
    judge_name: str = Field(min_length=1)
    #: The REQUESTED model — the per-label actually-served model travels on ``Judgment``.
    model_id: str = Field(min_length=1)
    taxonomy_id: str = Field(min_length=1)
    prompt_sha256: str
    few_shot_ids: tuple[str, ...] = ()
    few_shot_content_hashes: tuple[str, ...] = ()

    @field_validator("prompt_sha256")
    @classmethod
    def _must_be_full_sha256_hex(cls, value: str) -> str:
        if len(value) != _SHA256_HEX_LEN or any(c not in "0123456789abcdef" for c in value):
            raise ValueError(
                f"prompt_sha256 must be {_SHA256_HEX_LEN} lowercase hex chars, got {value!r}"
            )
        return value

    @model_validator(mode="after")
    def _few_shot_sets_must_be_sound(self) -> JudgeFingerprint:
        for name, values in (
            ("few_shot_ids", self.few_shot_ids),
            ("few_shot_content_hashes", self.few_shot_content_hashes),
        ):
            items = list(values)
            if items != sorted(items) or len(set(items)) != len(items):
                raise ValueError(f"{name} must be sorted ascending and unique")
        if len(self.few_shot_ids) != len(self.few_shot_content_hashes):
            raise ValueError(
                f"{len(self.few_shot_ids)} few_shot_ids but "
                f"{len(self.few_shot_content_hashes)} few_shot_content_hashes — "
                "every few-shot contributes exactly one id and one content hash"
            )
        return self


class Judge(Protocol):
    """Contract every judge implements (``AnthropicJudge``, ``FakeJudge``, test stubs).

    ``judge`` may raise the ``JudgeError`` subclasses from ``evalgen.label.errors``
    (documented here; not expressible in a Protocol). The narrow signature IS the
    blindness guarantee: two strings in, a ``Judgment`` out — no channel through which
    a human label, metadata hint, or cluster assignment can flow (ADR-0003 rule 3).
    """

    @property
    def fingerprint(self) -> JudgeFingerprint: ...

    def judge(self, input_text: str, output_text: str) -> Judgment: ...


class TextSanitizer(Protocol):
    """The redaction seam the few-shot loader requires (red-team F-1, ADR-0003 rule 8
    amendment).

    Structurally satisfied by ``ingest.redaction.sanitize_text`` without ``label/``
    ever importing ``ingest`` — the same injected-Protocol pattern as ``Embedder`` and
    ``Judge``: the composition layer passes the production sanitizer. The loader
    asserts the sanitizer is a NO-OP on every string field of every few-shot; an
    example carrying redactable content refuses to load. Rationale: records are
    redacted at ingestion (ADR-0001) but few-shots enter through their own door — an
    un-redacted few-shot would (a) ship its secret verbatim in the system prompt to
    the external judge API and (b) evade the collision + export gates, whose hashes
    are computed over REDACTED record text (``hash(raw) != hash(redacted)``).
    """

    def __call__(self, text: str, /) -> str: ...


class LabeledExample(BaseModel):
    """One auto-labeled record. Text is NOT duplicated here — join to ``LogRecord`` by
    ``record_id`` (one source of truth for content, no drift)."""

    model_config = ConfigDict(frozen=True)

    record_id: str = Field(min_length=1)
    taxonomy_id: str = Field(min_length=1)
    #: The model that ACTUALLY served this label (from ``Judgment``).
    model_id: str = Field(min_length=1)
    verdict: JudgeVerdict


class LabelFailureReason(StrEnum):
    """Why a judge call produced no label (typed — never a silent drop)."""

    REFUSAL = "refusal"
    PARSE_ERROR = "parse_error"
    API_ERROR = "api_error"


class LabelFailureEntry(BaseModel):
    """One refused/failed record: which, why, and a truncated detail."""

    model_config = ConfigDict(frozen=True)

    record_id: str = Field(min_length=1)
    reason: LabelFailureReason
    detail: str = Field(max_length=MAX_FAILURE_DETAIL_LEN)


class LabelingReport(BaseModel):
    """The five-bucket accounting: every input record lands in exactly one of
    labeled / refused / failed / skipped_budget / skipped_fewshot_collision — and a
    report that cannot account for every record refuses to exist (ADR-0003 rule 7)."""

    model_config = ConfigDict(frozen=True)

    judge: JudgeFingerprint
    max_labels: int = Field(ge=1)
    records_in: int = Field(ge=0)
    labeled: int = Field(ge=0)
    refused: int = Field(ge=0)
    #: parse_error + api_error (each traceable in ``failure_entries``).
    failed: int = Field(ge=0)
    skipped_budget: int = Field(ge=0)
    skipped_fewshot_collision: int = Field(ge=0)

    failures_by_reason: dict[LabelFailureReason, int] = Field(default_factory=dict)
    refusal_entries: tuple[LabelFailureEntry, ...] = ()
    failure_entries: tuple[LabelFailureEntry, ...] = ()
    skipped_budget_record_ids: tuple[str, ...] = ()
    fewshot_collision_record_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _five_buckets_must_hold(self) -> LabelingReport:
        total = (
            self.labeled
            + self.refused
            + self.failed
            + self.skipped_budget
            + self.skipped_fewshot_collision
        )
        if self.records_in != total:
            raise ValueError(
                f"records_in ({self.records_in}) != labeled + refused + failed + "
                f"skipped_budget + skipped_fewshot_collision ({self.labeled} + "
                f"{self.refused} + {self.failed} + {self.skipped_budget} + "
                f"{self.skipped_fewshot_collision} = {total}) — every record must land "
                "in exactly one bucket"
            )
        if len(self.refusal_entries) != self.refused:
            raise ValueError(
                f"{len(self.refusal_entries)} refusal entries but refused = "
                f"{self.refused} — every refusal needs its traceable entry"
            )
        for entry in self.refusal_entries:
            if entry.reason is not LabelFailureReason.REFUSAL:
                raise ValueError(
                    f"refusal entry for {entry.record_id!r} carries reason "
                    f"{entry.reason.value!r} — refusal entries must all be 'refusal'"
                )
        if len(self.failure_entries) != self.failed:
            raise ValueError(
                f"{len(self.failure_entries)} failure entries but failed = "
                f"{self.failed} — every failure needs its traceable entry"
            )
        failure_kinds = (LabelFailureReason.PARSE_ERROR, LabelFailureReason.API_ERROR)
        for entry in self.failure_entries:
            if entry.reason not in failure_kinds:
                raise ValueError(
                    f"failure entry for {entry.record_id!r} carries reason "
                    f"{entry.reason.value!r} — failures are parse_error or api_error only"
                )
        if sum(self.failures_by_reason.values()) != self.failed:
            raise ValueError(
                f"failures_by_reason sums to {sum(self.failures_by_reason.values())}, "
                f"expected failed = {self.failed}"
            )
        for reason, count in self.failures_by_reason.items():
            if reason not in failure_kinds:
                raise ValueError(
                    f"failures_by_reason carries key {reason.value!r} — refusals are a "
                    "separate bucket, only parse_error/api_error belong here"
                )
            matching = sum(1 for e in self.failure_entries if e.reason is reason)
            if count != matching:
                raise ValueError(
                    f"failures_by_reason[{reason.value!r}] = {count} but "
                    f"{matching} failure entries carry that reason"
                )
        if self.labeled + self.refused + self.failed > self.max_labels:
            raise ValueError(
                f"labeled + refused + failed = "
                f"{self.labeled + self.refused + self.failed} exceeds max_labels "
                f"({self.max_labels}) — the budget counts judge CALLS and can never be "
                "exceeded (ADR-0003 rule 6)"
            )
        for name, ids, counter in (
            ("skipped_budget_record_ids", self.skipped_budget_record_ids, self.skipped_budget),
            (
                "fewshot_collision_record_ids",
                self.fewshot_collision_record_ids,
                self.skipped_fewshot_collision,
            ),
        ):
            items = list(ids)
            if items != sorted(items) or len(set(items)) != len(items):
                raise ValueError(f"{name} must be sorted ascending and unique")
            if len(items) != counter:
                raise ValueError(f"{len(items)} ids in {name} but its counter = {counter}")
        for name, entries in (
            ("refusal_entries", self.refusal_entries),
            ("failure_entries", self.failure_entries),
        ):
            entry_ids = [e.record_id for e in entries]
            if entry_ids != sorted(entry_ids):
                raise ValueError(f"{name} must be sorted by record_id ascending")
        buckets = {
            "refusal_entries": {e.record_id for e in self.refusal_entries},
            "failure_entries": {e.record_id for e in self.failure_entries},
            "skipped_budget_record_ids": set(self.skipped_budget_record_ids),
            "fewshot_collision_record_ids": set(self.fewshot_collision_record_ids),
        }
        named = list(buckets.items())
        for i, (name_a, ids_a) in enumerate(named):
            for name_b, ids_b in named[i + 1 :]:
                overlap = sorted(ids_a & ids_b)
                if overlap:
                    raise ValueError(
                        f"record_id(s) {overlap} appear in both {name_a} and {name_b} — "
                        "the buckets must be pairwise disjoint"
                    )
        return self


class LabelingOutcome(BaseModel):
    """The public seam's return value (``DedupOutcome`` twin): labeled examples in
    canonical order + the report, cross-checked against each other — a forged outcome
    refuses to deserialize."""

    model_config = ConfigDict(frozen=True)

    labeled_examples: tuple[LabeledExample, ...]
    report: LabelingReport

    @model_validator(mode="after")
    def _labeled_must_match_report(self) -> LabelingOutcome:
        if len(self.labeled_examples) != self.report.labeled:
            raise ValueError(
                f"{len(self.labeled_examples)} labeled examples but report.labeled = "
                f"{self.report.labeled}"
            )
        ids = [e.record_id for e in self.labeled_examples]
        if ids != sorted(ids) or len(set(ids)) != len(ids):
            raise ValueError("labeled examples must be sorted by record_id ascending and unique")
        labeled_ids = set(ids)
        for name, bucket in (
            ("refusal_entries", {e.record_id for e in self.report.refusal_entries}),
            ("failure_entries", {e.record_id for e in self.report.failure_entries}),
            ("skipped_budget_record_ids", set(self.report.skipped_budget_record_ids)),
            (
                "fewshot_collision_record_ids",
                set(self.report.fewshot_collision_record_ids),
            ),
        ):
            overlap = sorted(labeled_ids & bucket)
            if overlap:
                raise ValueError(
                    f"record_id(s) {overlap} are labeled AND appear in the report's "
                    f"{name} — a record lands in exactly one bucket"
                )
        for example in self.labeled_examples:
            if example.taxonomy_id != self.report.judge.taxonomy_id:
                raise ValueError(
                    f"example {example.record_id!r} carries taxonomy_id "
                    f"{example.taxonomy_id!r} but the judge fingerprint says "
                    f"{self.report.judge.taxonomy_id!r} — labels from a different "
                    "questionnaire cannot ride this report"
                )
        return self
