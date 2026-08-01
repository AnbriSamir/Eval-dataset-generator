"""Assembly: labeled candidates → golden records + blocked-by-cause (ADR-0005 rule 3).

Assembly cannot run without a non-blocked gate decision — callers cannot forget the
gate because this function refuses a ``blocked`` verdict before touching a single
candidate (and nothing is ever written on that path: writing happens in the
composition layer, strictly after assembly).

The contamination guard here is defense in depth (ADR-0005 options §3): the
labeling engine already skipped judge-seen records, so on an honest run the
blocked bucket is structurally 0 — a nonzero count is a five-alarm signal
preserved as data, never a silent drop. The check runs against the hashes the
judge ACTUALLY saw (``labeling.report.judge.few_shot_content_hashes``), not the
store on disk (drift is the fingerprint chain's job, not this gate's).

A blocked collision's evidence is the colliding CONTENT HASH, never a few-shot
id (ADR-0005 Amendment (a), red-team MAJOR-1): the fingerprint's ``few_shot_ids``
and ``few_shot_content_hashes`` are two INDEPENDENTLY sorted tuples (id digests
vs content digests — unrelated orders), so no index pairing exists and the true
owner is unrecoverable from the fingerprint alone. The hash IS the evidence the
fingerprint can prove; the owner is recoverable exactly by hashing the store
(``FewShotExample.content_hash``).
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

from evalgen.contracts import (
    NOISE_CLUSTER_ID,
    BlockedCandidate,
    BlockedCause,
    ClusteringReport,
    ExportGateDecision,
    ExportGateVerdict,
    ExportOutcome,
    ExportReport,
    GoldenRecord,
    LabelingOutcome,
    LogRecord,
    RecordProvenance,
)
from evalgen.export.errors import ExportBlockedError, ExportInputError, NothingToExportError


def assemble_export(
    records: Sequence[LogRecord],
    labeling: LabelingOutcome,
    clustering: ClusteringReport,
    decision: ExportGateDecision,
) -> ExportOutcome:
    """Build the ``ExportOutcome`` from the run's labeled examples.

    ``records`` is the resolve pool (the dedup-kept records); every labeled
    example must resolve to a record AND a stratum — a miss is an
    ``ExportInputError`` (caller bug, never a statistic). Candidates whose
    canonical ``content_hash`` appears in the judge's few-shot set land in
    ``blocked`` with the colliding content hash named (set membership is the
    evidence — the fingerprint carries no id↔hash pairing); zero survivors is
    ``NothingToExportError``; a ``blocked`` decision is ``ExportBlockedError``.
    """
    if decision.verdict is ExportGateVerdict.BLOCKED:
        raise ExportBlockedError(decision)

    by_id = {record.record_id: record for record in records}
    stratum_of: dict[str, str] = {}
    for cluster in clustering.clusters:
        for record_id in cluster.record_ids:
            stratum_of[record_id] = cluster.cluster_id
    for record_id in clustering.noise_record_ids:
        stratum_of[record_id] = NOISE_CLUSTER_ID

    fingerprint = labeling.report.judge
    # Membership only — NEVER zip the fingerprint's two tuples into a hash→id map:
    # they are sorted independently (id digests vs content digests), so same-index
    # pairing attributes hashes to the WRONG few-shot (red-team MAJOR-1, proven
    # 5/5 mispaired on the committed store). The set membership is the evidence.
    few_shot_hashes = frozenset(fingerprint.few_shot_content_hashes)
    n_hashes = len(fingerprint.few_shot_content_hashes)
    hash_noun = "hash" if n_hashes == 1 else "hashes"

    golden: list[GoldenRecord] = []
    blocked: list[BlockedCandidate] = []
    for example in labeling.labeled_examples:
        record = by_id.get(example.record_id)
        if record is None:
            raise ExportInputError(
                f"labeled example {example.record_id!r} resolves to no record in the "
                "provided pool — assembly needs the run's own dedup-kept records"
            )
        stratum = stratum_of.get(example.record_id)
        if stratum is None:
            raise ExportInputError(
                f"labeled example {example.record_id!r} has no stratum in the "
                "clustering report — assembly needs the run's own coverage map"
            )
        content_hash = hashlib.sha256(record.canonical_text.encode("utf-8")).hexdigest()
        if content_hash in few_shot_hashes:
            blocked.append(
                BlockedCandidate(
                    record_id=example.record_id,
                    cause=BlockedCause.FEWSHOT_COLLISION,
                    detail=(
                        f"content_hash {content_hash} is in the judge's few-shot set "
                        f"({n_hashes} {hash_noun}) — owner recoverable by hashing the store"
                    ),
                )
            )
            continue
        golden.append(
            GoldenRecord(
                record_id=record.record_id,
                taxonomy_id=example.taxonomy_id,
                task_type=example.verdict.task_type,
                outcome=example.verdict.outcome,
                judge_model_id=example.model_id,
                judge_confidence=example.verdict.confidence,
                judge_rationale=example.verdict.rationale,
                input_text=record.input_text,
                output_text=record.output_text,
                metadata=record.metadata,
                provenance=RecordProvenance(
                    source_kind=record.origin.source_kind,
                    source_name=record.origin.source_name,
                    line_no=record.origin.line_no,
                    span_id=record.origin.span_id,
                    task_id=record.origin.task_id,
                    timestamp=record.timestamp,
                    cluster_id=stratum,
                    content_hash=content_hash,
                ),
            )
        )
    if not golden:
        raise NothingToExportError(
            f"all {len(blocked)} candidate(s) were blocked — an empty golden.jsonl is "
            "not an export"
        )
    golden.sort(key=lambda r: (r.provenance.source_name, r.provenance.line_no, r.record_id))
    blocked.sort(key=lambda b: b.record_id)
    report = ExportReport(
        judge=fingerprint,
        gate=decision,
        candidates_in=len(labeling.labeled_examples),
        exported=len(golden),
        blocked=tuple(blocked),
    )
    return ExportOutcome(golden_records=tuple(golden), report=report)
