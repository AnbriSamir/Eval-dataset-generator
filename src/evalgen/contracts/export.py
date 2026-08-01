"""Export contracts: self-verifying golden lines, the κ-gate decision, and the
two-section provenance manifest (ADR-0005).

Three load-bearing choices live here:

1. **A forged IDENTITY refuses to exist — scoped precisely (ADR-0005 Amendment
   (b)).** ``GoldenRecord`` recomputes ``record_id`` AND ``content_hash`` from its
   own origin + texts on every construction — including ``model_validate_json`` on
   a line read back from disk — so a line whose identity or texts were tampered is
   a ``ValidationError``, never a silent corruption (the ``LogRecord``/
   ``AgreementReport`` tamper-evidence discipline, applied to the final product).
   The label fields (``task_type``, ``outcome``, ``judge_confidence``,
   ``judge_rationale``) and ``metadata`` are the judge's OPINION — not derivable
   from the line, so no per-line validator can cover them. Their integrity rests
   on the file-level fence: ``meta.json`` binds ``golden_jsonl_sha256`` to the
   exact bytes, and ``/repro-audit`` regenerates + byte-diffs; a label-flipped
   line validates alone but its file can never match the manifest it shipped with.
2. **Contamination is unrepresentable, not merely filtered.** ``ExportOutcome``
   refuses to construct (or deserialize) any outcome where an exported record's
   canonical ``content_hash`` intersects the judge fingerprint's few-shot hashes —
   ``export ∩ few-shots = ∅`` is a contract, not a code path (ADR-0005 options §3).
3. **A meta.json that disagrees with itself refuses to deserialize.**
   ``ExportManifest`` re-runs the gate from its own embedded reports (via the ONE
   shared ``expected_gate_facts`` derivation ``export/gate.py`` also uses), enforces
   the ingest→export funnel across the embedded report chain, echoes every knob
   against the ``SettingsSnapshot``, and makes the ADR-0004 Amendment (a) copy duty
   structural: the human-labels digest cannot diverge from the embedded report's.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from enum import StrEnum
from typing import Final, Literal, NamedTuple

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evalgen.contracts.agreement import AgreementReport, KappaStatus
from evalgen.contracts.clustering import ClusteringReport, SamplingReport
from evalgen.contracts.dedup import DedupReport
from evalgen.contracts.embeddings import EmbedderFingerprint
from evalgen.contracts.labeling import MAX_RATIONALE_LEN, JudgeFingerprint, LabelingReport
from evalgen.contracts.records import CANONICAL_SEP, RecordOrigin, SourceKind, derive_record_id
from evalgen.contracts.reports import IngestReport
from evalgen.contracts.taxonomy import JudgeConfidence, OutcomeLabel, TaskTypeLabel

#: Bumped only by ADR — a consumer checking this int knows the line/manifest schema.
EXPORT_FORMAT_VERSION: Final = 1

_SHA256_RE = re.compile(r"[0-9a-f]{64}")


def _require_sha256_hex(field_name: str, value: str) -> str:
    if _SHA256_RE.fullmatch(value) is None:
        raise ValueError(
            f"{field_name} must be 64 lowercase hex chars (a full sha256 hexdigest), "
            f"got {value!r}"
        )
    return value


def _require_basename(field_name: str, value: str) -> str:
    if "/" in value or "\\" in value:
        raise ValueError(
            f"{field_name} must be a basename, got {value!r} — paths leak the "
            "environment (ADR-0001 PII rule)"
        )
    return value


class RecordProvenance(BaseModel):
    """Where one golden line came from: source span, coverage stratum, dedup identity.

    ``content_hash`` is the exact-dedup identity (full sha256 over
    ``input ␟ output``) — every consumer can re-run the contamination check
    independently against a few-shot store's hashes (ADR-0005 options §1).
    """

    model_config = ConfigDict(frozen=True)

    source_kind: SourceKind
    source_name: str = Field(min_length=1)
    line_no: int = Field(ge=1)
    span_id: str | None = None
    task_id: str | None = None
    #: Source-derived or None — NEVER wall clock (byte-identical exports).
    timestamp: datetime | None = None
    #: ``"cl-…"`` or ``NOISE_CLUSTER_ID`` — the coverage dimension.
    cluster_id: str = Field(min_length=1)
    content_hash: str

    @field_validator("source_name")
    @classmethod
    def _source_name_is_a_basename(cls, value: str) -> str:
        return _require_basename("source_name", value)

    @field_validator("content_hash")
    @classmethod
    def _content_hash_is_sha256(cls, value: str) -> str:
        return _require_sha256_hex("content_hash", value)


class GoldenRecord(BaseModel):
    """One line of golden.jsonl — identity + texts self-verify: a line forged on
    ``record_id``, origin or texts refuses to exist. The label fields are NOT
    self-verifiable (they are the judge's opinion, not derivable from the line);
    their integrity is owned by ``golden_jsonl_sha256`` + ``/repro-audit``
    (ADR-0005 Amendment (b) — the claim is scoped, never overclaimed).

    The labels are THE judge's (calibrated by the κ stamped on the export's face);
    human labels never ride a line (ADR-0005 options §1 — one label semantics, the
    calibration set stays un-broadcast). ``judge_confidence``/``judge_rationale``
    are transparency diagnostics — never a filter key: the κ was measured
    unfiltered, and filtering by the judge's own confidence is self-grading.
    """

    model_config = ConfigDict(frozen=True)

    record_id: str = Field(min_length=1)
    taxonomy_id: str = Field(min_length=1)
    task_type: TaskTypeLabel
    outcome: OutcomeLabel
    #: The model that ACTUALLY served this label (per ADR-0003, from ``Judgment``).
    judge_model_id: str = Field(min_length=1)
    judge_confidence: JudgeConfidence
    judge_rationale: str = Field(min_length=1, max_length=MAX_RATIONALE_LEN)
    #: Post-redaction by construction (ADR-0001: no other kind of text exists here).
    input_text: str = Field(min_length=1)
    output_text: str = Field(min_length=1)
    metadata: dict[str, str] = Field(default_factory=dict)
    provenance: RecordProvenance

    @model_validator(mode="after")
    def _line_must_verify_itself(self) -> GoldenRecord:
        """Recompute record_id AND content_hash from the line's own origin + texts.

        Scope (ADR-0005 Amendment (b)): this covers identity and texts ONLY —
        label fields cannot be recomputed from anything and are fenced at the
        file level (``golden_jsonl_sha256`` + regeneration), not here.
        """
        origin = RecordOrigin(
            source_kind=self.provenance.source_kind,
            source_name=self.provenance.source_name,
            line_no=self.provenance.line_no,
            span_id=self.provenance.span_id,
            task_id=self.provenance.task_id,
        )
        expected_id = derive_record_id(origin, self.input_text, self.output_text)
        if self.record_id != expected_id:
            raise ValueError(
                f"record_id {self.record_id!r} does not match the line's own origin and "
                f"texts (expected {expected_id!r}) — a forged golden line refuses to "
                "exist (ADR-0005 rule 1)"
            )
        joined = self.input_text + CANONICAL_SEP + self.output_text
        expected_hash = hashlib.sha256(joined.encode("utf-8")).hexdigest()
        if self.provenance.content_hash != expected_hash:
            raise ValueError(
                f"provenance.content_hash {self.provenance.content_hash!r} does not match "
                f"the line's own texts (expected {expected_hash!r}) — the exact-dedup "
                "identity must be re-derivable from the line (ADR-0005 rule 1)"
            )
        return self


class BlockedCause(StrEnum):
    """Why an export candidate was blocked — typed, counted, never silent."""

    FEWSHOT_COLLISION = "fewshot_collision"


class BlockedCandidate(BaseModel):
    """One blocked candidate: which record, why, and the named evidence."""

    model_config = ConfigDict(frozen=True)

    record_id: str = Field(min_length=1)
    cause: BlockedCause
    detail: str = Field(min_length=1)


class GateCheckName(StrEnum):
    """The five export-gate checks — declaration order IS check order (pinned)."""

    HEADLINE_READY = "headline_ready"
    HEADLINE_STATUS = "headline_status"
    INSTRUMENT_BINDING = "instrument_binding"
    GROUND_TRUTH_BOUND = "ground_truth_bound"
    KAPPA_THRESHOLD = "kappa_threshold"


class GateCheck(BaseModel):
    """One named gate check with its deterministic, path-free detail line."""

    model_config = ConfigDict(frozen=True)

    name: GateCheckName
    passed: bool
    detail: str = Field(min_length=1)


class ExportGateOverride(BaseModel):
    """The explicit, singular, loud override — a mandatory reason, no ritual flags."""

    model_config = ConfigDict(frozen=True)

    reason: str = Field(min_length=1, max_length=500)


class ExportGateVerdict(StrEnum):
    """Closed verdict vocabulary — an override is a distinct, visible state."""

    PASSED = "passed"
    PASSED_WITH_OVERRIDE = "passed_with_override"
    BLOCKED = "blocked"


class ExportGateDecision(BaseModel):
    """The gate's full decision — self-coherent: a verdict that disagrees with its
    own checks, a straddle flag that lies, or a no-op override refuses to exist
    (ADR-0005 options §4)."""

    model_config = ConfigDict(frozen=True)

    min_export_kappa: float = Field(ge=-1.0, le=1.0)
    #: Exactly the five ``GateCheckName``s, declared order.
    checks: tuple[GateCheck, ...]
    #: The REPORT-STORED (6-decimal-rounded) headline κ — the printed number is the
    #: gated number (ADR-0005 options §4).
    kappa: float | None
    ci_lower: float | None
    ci_upper: float | None
    ci_straddles_threshold: bool
    override: ExportGateOverride | None = None
    verdict: ExportGateVerdict

    @model_validator(mode="after")
    def _decision_must_cohere(self) -> ExportGateDecision:
        names = tuple(check.name for check in self.checks)
        if names != tuple(GateCheckName):
            raise ValueError(
                f"checks must be exactly {[n.value for n in GateCheckName]!r} in declared "
                f"order, got {[n.value for n in names]!r}"
            )
        by_name = {check.name: check for check in self.checks}
        if (self.ci_lower is None) != (self.ci_upper is None):
            raise ValueError(
                f"ci_lower and ci_upper must both be present or both be None, got "
                f"[{self.ci_lower!r}, {self.ci_upper!r}]"
            )
        if self.kappa is None:
            if by_name[GateCheckName.HEADLINE_READY].passed and (
                by_name[GateCheckName.HEADLINE_STATUS].passed
            ):
                raise ValueError(
                    "kappa is None but headline_ready and headline_status both passed — "
                    "a missing kappa must trace to a missing or undefined headline"
                )
            if self.ci_lower is not None:
                raise ValueError("kappa is None but CI bounds are present")
            if self.ci_straddles_threshold:
                raise ValueError("kappa is None but ci_straddles_threshold is True")
        expected_threshold_pass = self.kappa is not None and self.kappa >= self.min_export_kappa
        if by_name[GateCheckName.KAPPA_THRESHOLD].passed != expected_threshold_pass:
            raise ValueError(
                f"kappa_threshold.passed={by_name[GateCheckName.KAPPA_THRESHOLD].passed} "
                f"but kappa={self.kappa!r} vs min_export_kappa={self.min_export_kappa!r} "
                f"demands {expected_threshold_pass} — the gate cannot be asserted"
            )
        expected_straddle = (
            self.kappa is not None
            and self.ci_lower is not None
            and self.ci_lower < self.min_export_kappa <= self.kappa
        )
        if self.ci_straddles_threshold != expected_straddle:
            raise ValueError(
                f"ci_straddles_threshold={self.ci_straddles_threshold} but "
                f"ci_lower={self.ci_lower!r}, min_export_kappa={self.min_export_kappa!r}, "
                f"kappa={self.kappa!r} demand {expected_straddle} — a straddle is stated, "
                "never asserted (ADR-0004 options §6)"
            )
        failed = {check.name for check in self.checks if not check.passed}
        if not failed:
            if self.override is not None:
                raise ValueError(
                    "override present but every check passed — an override must "
                    "override something (ADR-0005 options §4: no ritual flags)"
                )
            expected_verdict = ExportGateVerdict.PASSED
        elif failed == {GateCheckName.KAPPA_THRESHOLD}:
            expected_verdict = (
                ExportGateVerdict.PASSED_WITH_OVERRIDE
                if self.override is not None
                else ExportGateVerdict.BLOCKED
            )
        else:
            if self.override is not None:
                non_overridable = sorted(n.value for n in failed - {GateCheckName.KAPPA_THRESHOLD})
                raise ValueError(
                    f"override present alongside non-overridable failure(s) "
                    f"{non_overridable!r} — checks 1-4 mean the kappa does not exist or "
                    "does not apply; there is no honest low number to carry"
                )
            expected_verdict = ExportGateVerdict.BLOCKED
        if self.verdict is not expected_verdict:
            raise ValueError(
                f"verdict {self.verdict.value!r} disagrees with the checks "
                f"(failed={sorted(n.value for n in failed)!r}, "
                f"override={'present' if self.override else 'absent'}) — expected "
                f"{expected_verdict.value!r}"
            )
        return self


class ExportReport(BaseModel):
    """The export accounting: candidates = exported + blocked-by-cause, exactly.

    A report only exists for a run that exported: ``verdict`` is never ``blocked``
    (a blocked run raises ``ExportBlockedError`` and writes nothing) and
    ``exported >= 1`` (an empty export is ``NothingToExportError`` upstream).
    """

    model_config = ConfigDict(frozen=True)

    judge: JudgeFingerprint
    gate: ExportGateDecision
    candidates_in: int = Field(ge=1)
    exported: int = Field(ge=1)
    blocked: tuple[BlockedCandidate, ...] = ()

    @model_validator(mode="after")
    def _sums_and_verdict_must_hold(self) -> ExportReport:
        if self.candidates_in != self.exported + len(self.blocked):
            raise ValueError(
                f"candidates_in ({self.candidates_in}) != exported + blocked "
                f"({self.exported} + {len(self.blocked)} = "
                f"{self.exported + len(self.blocked)}) — every candidate lands in "
                "exactly one bucket (ADR-0005 decision driver: nothing dropped in silence)"
            )
        ids = [entry.record_id for entry in self.blocked]
        if ids != sorted(ids) or len(set(ids)) != len(ids):
            raise ValueError("blocked entries must be sorted by record_id ascending and unique")
        if self.gate.verdict is ExportGateVerdict.BLOCKED:
            raise ValueError(
                "an ExportReport cannot carry a 'blocked' verdict — a blocked run "
                "raises ExportBlockedError and produces no report (ADR-0005 rule 3)"
            )
        return self


class ExportOutcome(BaseModel):
    """The public seam's return value (``DedupOutcome``/``LabelingOutcome`` twin):
    golden records in canonical order + the report, cross-checked — and the
    ∅-intersection contract: a contaminated outcome is unrepresentable."""

    model_config = ConfigDict(frozen=True)

    golden_records: tuple[GoldenRecord, ...]
    report: ExportReport

    @model_validator(mode="after")
    def _records_must_match_report(self) -> ExportOutcome:
        if len(self.golden_records) != self.report.exported:
            raise ValueError(
                f"{len(self.golden_records)} golden records but report.exported = "
                f"{self.report.exported}"
            )
        ids = [record.record_id for record in self.golden_records]
        if len(set(ids)) != len(ids):
            raise ValueError("golden records contain duplicate record_ids")
        keys = [
            (record.provenance.source_name, record.provenance.line_no, record.record_id)
            for record in self.golden_records
        ]
        if any(not earlier < later for earlier, later in zip(keys, keys[1:], strict=False)):
            raise ValueError(
                "golden records must be strictly ascending by (source_name, line_no, "
                "record_id) — THE canonical total order, re-derived from each line's own "
                "provenance (ADR-0005 options §1)"
            )
        blocked_ids = {entry.record_id for entry in self.report.blocked}
        overlap = sorted(set(ids) & blocked_ids)
        if overlap:
            raise ValueError(
                f"record_id(s) {overlap} are exported AND blocked — a candidate lands "
                "in exactly one bucket"
            )
        for record in self.golden_records:
            if record.taxonomy_id != self.report.judge.taxonomy_id:
                raise ValueError(
                    f"record {record.record_id!r} carries taxonomy_id "
                    f"{record.taxonomy_id!r} but the judge fingerprint says "
                    f"{self.report.judge.taxonomy_id!r}"
                )
        few_shot_hashes = set(self.report.judge.few_shot_content_hashes)
        contaminated = sorted(
            record.record_id
            for record in self.golden_records
            if record.provenance.content_hash in few_shot_hashes
        )
        if contaminated:
            raise ValueError(
                f"record_id(s) {contaminated} carry a content_hash present in the judge's "
                "few-shot set — export ∩ few-shots must be empty; a contaminated export "
                "is unrepresentable (ADR-0005 options §3)"
            )
        return self


class InputFileRole(StrEnum):
    """What a hashed input file fed into the run — typed, never guessed."""

    SOURCE_LOG = "source_log"
    FEW_SHOT_STORE = "few_shot_store"
    HUMAN_LABELS = "human_labels"


class InputFileDigest(BaseModel):
    """sha256 of the RAW BYTES of one file the run actually read (basename only)."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    sha256: str
    role: InputFileRole

    @field_validator("name")
    @classmethod
    def _name_is_a_basename(cls, value: str) -> str:
        return _require_basename("name", value)

    @field_validator("sha256")
    @classmethod
    def _sha256_is_hex(cls, value: str) -> str:
        return _require_sha256_hex("sha256", value)


class SettingsSnapshot(BaseModel):
    """Typed copy of every active knob — an EXACT field-name mirror of
    ``config.Settings``, pinned by test so a new knob cannot silently skip
    provenance (ADR-0005 options §2)."""

    model_config = ConfigDict(frozen=True)

    judge_model: str
    judge_model_fast: str
    seed: int
    near_dup_threshold: float = Field(ge=-1.0, le=1.0)
    min_cluster_size: int = Field(ge=2)
    hash_embedding_dim: int
    sample_size: int = Field(ge=1)
    max_labels_per_run: int = Field(ge=1)
    min_human_labels: int = Field(ge=1)
    bootstrap_resamples: int = Field(ge=1)
    min_class_support: int = Field(ge=1)
    min_export_kappa: float = Field(ge=-1.0, le=1.0)


class VolatileProvenance(BaseModel):
    """The quarantined volatile section — collected by the composition layer only;
    no pure export function ever reads a clock, git, or the environment.
    ``git_commit=None`` renders as "unrecorded" (the ADR-0004 M-1 honesty style)."""

    model_config = ConfigDict(frozen=True)

    git_commit: str | None = None
    #: UTC ISO-8601, composition-formatted.
    generated_at: str | None = None
    environment: dict[str, str] = Field(default_factory=dict)


class GateFacts(NamedTuple):
    """The agreement-derived half of a gate decision — shared by ``export/gate.py``
    (which attaches detail strings) and the ``ExportManifest`` validator (which
    refuses any divergence): ONE implementation, no drift surface."""

    #: One flag per ``GateCheckName``, declaration order.
    passed: tuple[bool, bool, bool, bool, bool]
    kappa: float | None
    ci_lower: float | None
    ci_upper: float | None
    ci_straddles_threshold: bool


def expected_gate_facts(
    agreement: AgreementReport,
    labeling_fingerprint: JudgeFingerprint,
    min_export_kappa: float,
) -> GateFacts:
    """Derive the five expected pass flags + κ/CI/straddle from the reports alone.

    The κ compared is the REPORT-STORED (rounded) headline κ; ``>=`` passes at the
    boundary; a headline whose status is not ``ok`` blocks exactly like a missing
    one (ADR-0004 Amendment (d)); the straddle is stated, never blocking.
    """
    headline = agreement.headline
    ready = agreement.headline_ready
    status_ok = headline is not None and headline.status is KappaStatus.OK
    binding = agreement.judge == labeling_fingerprint
    bound = agreement.human_labels_sha256 is not None
    kappa = headline.kappa if headline is not None and status_ok else None
    ci_lower: float | None = None
    ci_upper: float | None = None
    if (
        kappa is not None
        and headline is not None
        and headline.ci95 is not None
        and headline.ci95.lower is not None
        and headline.ci95.upper is not None
    ):
        ci_lower = headline.ci95.lower
        ci_upper = headline.ci95.upper
    threshold_pass = kappa is not None and kappa >= min_export_kappa
    straddle = kappa is not None and ci_lower is not None and ci_lower < min_export_kappa <= kappa
    return GateFacts(
        passed=(ready, status_ok, binding, bound, threshold_pass),
        kappa=kappa,
        ci_lower=ci_lower,
        ci_upper=ci_upper,
        ci_straddles_threshold=straddle,
    )


def _expected_verdict(facts: GateFacts, override: ExportGateOverride | None) -> ExportGateVerdict:
    """Verdict logic shared with the decision's own validator — pinned in one place."""
    failed = {name for name, ok in zip(GateCheckName, facts.passed, strict=True) if not ok}
    if not failed:
        return ExportGateVerdict.PASSED
    if failed == {GateCheckName.KAPPA_THRESHOLD} and override is not None:
        return ExportGateVerdict.PASSED_WITH_OVERRIDE
    return ExportGateVerdict.BLOCKED


class ExportManifest(BaseModel):
    """THE meta.json content: settings snapshot, input digests, the entire embedded
    report chain, and the gate — cross-validated into one coherent run.

    A manifest whose reports do not form one coherent run — a broken funnel, a
    drifted knob echo, a fingerprint chain break, a human-labels digest that
    diverges from the embedded report's, or a gate section that lies about its own
    reports — refuses to deserialize (ADR-0005 options §2/§4).
    """

    model_config = ConfigDict(frozen=True)

    export_format_version: Literal[1] = EXPORT_FORMAT_VERSION
    settings: SettingsSnapshot
    input_files: tuple[InputFileDigest, ...] = Field(min_length=1)
    embedder: EmbedderFingerprint
    #: One per source, in load order.
    ingest: tuple[IngestReport, ...] = Field(min_length=1)
    dedup: DedupReport
    clustering: ClusteringReport
    sampling: SamplingReport
    labeling: LabelingReport
    #: The ENTIRE report — every embedded validator re-runs on deserialization, so
    #: an edited κ or doctored confusion matrix in meta.json refuses to parse.
    agreement: AgreementReport
    export: ExportReport
    #: meta.json names the exact bytes of the dataset it certifies (M-1 pattern).
    golden_jsonl_sha256: str
    volatile: VolatileProvenance | None = None

    @field_validator("golden_jsonl_sha256")
    @classmethod
    def _golden_sha256_is_hex(cls, value: str) -> str:
        return _require_sha256_hex("golden_jsonl_sha256", value)

    @model_validator(mode="after")
    def _manifest_must_cohere(self) -> ExportManifest:
        self._check_fingerprint_chain()
        self._check_input_files()
        self._check_funnel()
        self._check_knob_echoes()
        self._check_gate_recomputation()
        return self

    def _check_fingerprint_chain(self) -> None:
        """One instrument end to end: labeling == agreement == export fingerprints."""
        if self.labeling.judge != self.agreement.judge:
            raise ValueError(
                "labeling.judge != agreement.judge — a kappa measured on instrument A "
                "cannot certify labels from instrument B (fingerprint chain, ADR-0005 "
                "options §4 check 3)"
            )
        if self.labeling.judge != self.export.judge:
            raise ValueError(
                "labeling.judge != export.judge — the export must carry the fingerprint "
                "of the run that produced its labels (fingerprint chain)"
            )

    def _check_input_files(self) -> None:
        """The M-1 copy duty, structural — plus role counts and deterministic order."""
        keys = [(digest.role.value, digest.name) for digest in self.input_files]
        if keys != sorted(keys):
            raise ValueError("input_files must be sorted by (role, name)")
        names = [digest.name for digest in self.input_files]
        if len(set(names)) != len(names):
            raise ValueError("input_files names must be unique")
        by_role: dict[InputFileRole, list[InputFileDigest]] = {role: [] for role in InputFileRole}
        for digest in self.input_files:
            by_role[digest.role].append(digest)
        if len(by_role[InputFileRole.HUMAN_LABELS]) != 1:
            raise ValueError(
                f"{len(by_role[InputFileRole.HUMAN_LABELS])} human_labels input files — "
                "exactly one is required (the ground truth the kappa binds to)"
            )
        if len(by_role[InputFileRole.FEW_SHOT_STORE]) != 1:
            raise ValueError(
                f"{len(by_role[InputFileRole.FEW_SHOT_STORE])} few_shot_store input "
                "files — exactly one is required"
            )
        if not by_role[InputFileRole.SOURCE_LOG]:
            raise ValueError("at least one source_log input file is required")
        human_labels = by_role[InputFileRole.HUMAN_LABELS][0]
        if self.agreement.human_labels_sha256 is None:
            raise ValueError(
                "agreement.human_labels_sha256 is None — an export-grade manifest must "
                "embed a ground-truth-bound report (ADR-0004 Amendment (a))"
            )
        if self.agreement.human_labels_sha256 != human_labels.sha256:
            raise ValueError(
                f"agreement.human_labels_sha256 ({self.agreement.human_labels_sha256}) != "
                f"the human_labels input file digest ({human_labels.sha256}) — the "
                "ADR-0004 Amendment (a) copy duty is structural: an inconsistent copy is "
                "unrepresentable"
            )
        source_names = {digest.name for digest in by_role[InputFileRole.SOURCE_LOG]}
        ingest_names = {report.source_name for report in self.ingest}
        if source_names != ingest_names:
            raise ValueError(
                f"source_log input files {sorted(source_names)!r} != ingest report "
                f"sources {sorted(ingest_names)!r} — every hashed source must have its "
                "ingest accounting and vice versa"
            )

    def _check_funnel(self) -> None:
        """lines read → … → exported, enforced across the embedded reports."""
        normalized = sum(report.records_normalized for report in self.ingest)
        if normalized != self.dedup.records_in:
            raise ValueError(
                f"funnel break: sum(ingest.records_normalized) = {normalized} != "
                f"dedup.records_in = {self.dedup.records_in}"
            )
        if self.dedup.records_out != self.clustering.records_in:
            raise ValueError(
                f"funnel break: dedup.records_out = {self.dedup.records_out} != "
                f"clustering.records_in = {self.clustering.records_in}"
            )
        if self.clustering.records_in != self.sampling.records_in:
            raise ValueError(
                f"funnel break: clustering.records_in = {self.clustering.records_in} != "
                f"sampling.records_in = {self.sampling.records_in}"
            )
        if self.sampling.total_sampled != self.labeling.records_in:
            raise ValueError(
                f"funnel break: sampling.total_sampled = {self.sampling.total_sampled} != "
                f"labeling.records_in = {self.labeling.records_in}"
            )
        if self.labeling.labeled != self.export.candidates_in:
            raise ValueError(
                f"funnel break: labeling.labeled = {self.labeling.labeled} != "
                f"export.candidates_in = {self.export.candidates_in}"
            )

    def _check_knob_echoes(self) -> None:
        """Every knob echoed by an embedded report must equal the snapshot's claim."""
        echoes: tuple[tuple[str, object, object], ...] = (
            ("dedup.threshold", self.dedup.threshold, self.settings.near_dup_threshold),
            (
                "clustering.min_cluster_size",
                self.clustering.min_cluster_size,
                self.settings.min_cluster_size,
            ),
            ("embedder.dim", self.embedder.dim, self.settings.hash_embedding_dim),
            ("sampling.seed", self.sampling.seed, self.settings.seed),
            (
                "sampling.sample_size_requested",
                self.sampling.sample_size_requested,
                self.settings.sample_size,
            ),
            ("labeling.max_labels", self.labeling.max_labels, self.settings.max_labels_per_run),
            (
                "agreement.min_human_labels",
                self.agreement.min_human_labels,
                self.settings.min_human_labels,
            ),
            (
                "agreement.bootstrap_resamples",
                self.agreement.bootstrap_resamples,
                self.settings.bootstrap_resamples,
            ),
            (
                "agreement.min_class_support",
                self.agreement.min_class_support,
                self.settings.min_class_support,
            ),
            ("agreement.seed", self.agreement.seed, self.settings.seed),
            (
                "export.gate.min_export_kappa",
                self.export.gate.min_export_kappa,
                self.settings.min_export_kappa,
            ),
        )
        for name, reported, claimed in echoes:
            if reported != claimed:
                raise ValueError(
                    f"knob echo mismatch: {name} = {reported!r} but the settings "
                    f"snapshot claims {claimed!r} — the reports and the snapshot must "
                    "describe one run"
                )
        if self.dedup.embedder != self.embedder or self.clustering.embedder != self.embedder:
            raise ValueError(
                "dedup.embedder / clustering.embedder differ from the manifest's "
                "embedder fingerprint — every number must name the embedder that "
                "measured it (ADR-0002 rule 5)"
            )

    def _check_gate_recomputation(self) -> None:
        """Re-derive the gate from the embedded reports; refuse any divergence."""
        facts = expected_gate_facts(
            self.agreement, self.labeling.judge, self.settings.min_export_kappa
        )
        gate = self.export.gate
        for check, expected in zip(gate.checks, facts.passed, strict=True):
            if check.passed != expected:
                raise ValueError(
                    f"gate forgery: check {check.name.value!r} says passed="
                    f"{check.passed} but the embedded reports say {expected} — the "
                    "manifest recomputes its own gate (ADR-0005 options §4)"
                )
        if gate.kappa != facts.kappa:
            raise ValueError(
                f"gate forgery: gate.kappa = {gate.kappa!r} but the embedded agreement "
                f"report says {facts.kappa!r}"
            )
        if gate.ci_lower != facts.ci_lower or gate.ci_upper != facts.ci_upper:
            raise ValueError(
                f"gate forgery: gate CI [{gate.ci_lower!r}, {gate.ci_upper!r}] but the "
                f"embedded agreement report says [{facts.ci_lower!r}, {facts.ci_upper!r}]"
            )
        if gate.ci_straddles_threshold != facts.ci_straddles_threshold:
            raise ValueError(
                f"gate forgery: ci_straddles_threshold = {gate.ci_straddles_threshold} "
                f"but the embedded reports say {facts.ci_straddles_threshold}"
            )
        expected_verdict = _expected_verdict(facts, gate.override)
        if gate.verdict is not expected_verdict:
            raise ValueError(
                f"gate forgery: verdict {gate.verdict.value!r} but the embedded reports "
                f"demand {expected_verdict.value!r}"
            )
