"""The assembly battery (ADR-0005 rule 3): the deliberately-leaked candidate lands
in ``blocked`` with the colliding CONTENT HASH named (never a few-shot id — the
fingerprint's tuples are independently sorted, red-team MAJOR-1); a blocked gate
raises before any write; unresolvable inputs are caller bugs (typed), never
statistics; and the output order is canonical whatever order the resolve pool
arrives in. ``TestRedTeamMajor1Payload`` replays the red team's own payload on the
real committed store.
"""

from __future__ import annotations

import hashlib
import random
from pathlib import Path

import pytest

from conftest import STUB_VERDICT, make_record, make_stub_fingerprint
from evalgen.contracts import (
    NOISE_CLUSTER_ID,
    TAXONOMY_V1,
    BlockedCause,
    ClusteringReport,
    EmbedderFingerprint,
    ExportGateDecision,
    ExportGateVerdict,
    GateCheck,
    GateCheckName,
    JudgeFingerprint,
    LabeledExample,
    LabelingOutcome,
    LabelingReport,
    LogRecord,
)
from evalgen.export import assemble_export, write_export
from evalgen.export.errors import ExportBlockedError, ExportInputError, NothingToExportError
from evalgen.ingest import sanitize_text
from evalgen.label import FakeJudge, load_few_shots

#: The real committed store — the red-team MAJOR-1 payload runs on it verbatim.
STORE_PATH = Path(__file__).resolve().parents[1] / "data" / "fewshots" / "judge_v1.jsonl"

RECORDS = tuple(
    make_record(
        source_name="assemble_test.jsonl",
        line_no=i + 1,
        input_text=f"Question numéro {i + 1} sur le trafic ?",
        output_text=f"Réponse numéro {i + 1}.",
    )
    for i in range(3)
)


def _hash(record: LogRecord) -> str:
    return hashlib.sha256(record.canonical_text.encode("utf-8")).hexdigest()


#: The planted leak: record #2's canonical hash sits in the judge's few-shot set.
LEAKED = RECORDS[1]


def make_decision(verdict: ExportGateVerdict = ExportGateVerdict.PASSED) -> ExportGateDecision:
    failed_threshold = verdict is ExportGateVerdict.BLOCKED
    return ExportGateDecision(
        min_export_kappa=0.6,
        checks=tuple(
            GateCheck(
                name=name,
                passed=not (failed_threshold and name is GateCheckName.KAPPA_THRESHOLD),
                detail="hand-built check — assemble battery",
            )
            for name in GateCheckName
        ),
        kappa=0.5 if failed_threshold else 0.65,
        ci_lower=None,
        ci_upper=None,
        ci_straddles_threshold=False,
        override=None,
        verdict=verdict,
    )


def make_labeling(
    fingerprint: JudgeFingerprint, records: tuple[LogRecord, ...] = RECORDS
) -> LabelingOutcome:
    examples = tuple(
        LabeledExample(
            record_id=record.record_id,
            taxonomy_id=fingerprint.taxonomy_id,
            model_id="stub-model-served",
            verdict=STUB_VERDICT,
        )
        for record in sorted(records, key=lambda r: r.record_id)
    )
    report = LabelingReport(
        judge=fingerprint,
        max_labels=10,
        records_in=len(records),
        labeled=len(records),
        refused=0,
        failed=0,
        skipped_budget=0,
        skipped_fewshot_collision=0,
    )
    return LabelingOutcome(labeled_examples=examples, report=report)


def make_clustering(records: tuple[LogRecord, ...] = RECORDS) -> ClusteringReport:
    return ClusteringReport(
        embedder=EmbedderFingerprint(name="stub", dim=8, analyzer="stub", ngram_min=1, ngram_max=1),
        min_cluster_size=2,
        metric="euclidean_l2norm",
        records_in=len(records),
        clusters=(),
        noise_record_ids=tuple(sorted(record.record_id for record in records)),
    )


class TestHappyPathAndContamination:
    def test_leaked_candidate_is_blocked_and_named(self) -> None:
        fingerprint = make_stub_fingerprint(few_shot_content_hashes=(_hash(LEAKED),))
        outcome = assemble_export(
            RECORDS, make_labeling(fingerprint), make_clustering(), make_decision()
        )
        assert outcome.report.candidates_in == 3
        assert outcome.report.exported == 2
        assert len(outcome.report.blocked) == 1
        blocked = outcome.report.blocked[0]
        assert blocked.record_id == LEAKED.record_id
        assert blocked.cause is BlockedCause.FEWSHOT_COLLISION
        assert blocked.detail == (
            f"content_hash {_hash(LEAKED)} is in the judge's few-shot set "
            "(1 hash) — owner recoverable by hashing the store"
        )
        assert LEAKED.record_id not in {r.record_id for r in outcome.golden_records}

    def test_golden_fields_come_from_record_and_verdict(self) -> None:
        fingerprint = make_stub_fingerprint()
        outcome = assemble_export(
            RECORDS, make_labeling(fingerprint), make_clustering(), make_decision()
        )
        assert outcome.report.exported == 3
        record = outcome.golden_records[0]
        assert record.judge_model_id == "stub-model-served"  # the ACTUALLY-served model
        assert record.provenance.cluster_id == NOISE_CLUSTER_ID
        assert record.task_type is STUB_VERDICT.task_type
        assert record.outcome is STUB_VERDICT.outcome

    def test_order_is_canonical_under_input_shuffle(self) -> None:
        fingerprint = make_stub_fingerprint(few_shot_content_hashes=(_hash(LEAKED),))
        labeling, clustering = make_labeling(fingerprint), make_clustering()
        baseline = assemble_export(RECORDS, labeling, clustering, make_decision())
        shuffled = list(RECORDS)
        random.Random(7).shuffle(shuffled)
        assert assemble_export(shuffled, labeling, clustering, make_decision()) == baseline
        keys = [
            (r.provenance.source_name, r.provenance.line_no, r.record_id)
            for r in baseline.golden_records
        ]
        assert keys == sorted(keys)


class TestBlockedGate:
    def test_blocked_decision_raises_carrying_the_decision(self) -> None:
        decision = make_decision(ExportGateVerdict.BLOCKED)
        with pytest.raises(ExportBlockedError) as excinfo:
            assemble_export(
                RECORDS, make_labeling(make_stub_fingerprint()), make_clustering(), decision
            )
        assert excinfo.value.decision == decision
        assert "kappa_threshold" in str(excinfo.value)

    def test_blocked_gate_writes_nothing_in_the_composition_pattern(self, tmp_path) -> None:
        # The composition pattern: assemble FIRST, write AFTER — a blocked gate
        # therefore cannot leave partial artifacts (ADR-0005 rule 3).
        with pytest.raises(ExportBlockedError):
            outcome = assemble_export(
                RECORDS,
                make_labeling(make_stub_fingerprint()),
                make_clustering(),
                make_decision(ExportGateVerdict.BLOCKED),
            )
            write_export(tmp_path, golden_text=str(outcome), meta_text="{}")  # never reached
        assert list(tmp_path.iterdir()) == []


class TestCallerBugs:
    def test_unresolvable_record_id_raises(self) -> None:
        pool_missing_one = (RECORDS[0], RECORDS[2])
        with pytest.raises(ExportInputError, match="resolves to no record"):
            assemble_export(
                pool_missing_one,
                make_labeling(make_stub_fingerprint()),
                make_clustering(),
                make_decision(),
            )

    def test_missing_stratum_raises(self) -> None:
        clustering_missing_one = make_clustering(records=(RECORDS[0], RECORDS[2]))
        with pytest.raises(ExportInputError, match="no stratum"):
            assemble_export(
                RECORDS,
                make_labeling(make_stub_fingerprint()),
                clustering_missing_one,
                make_decision(),
            )

    def test_all_candidates_blocked_raises(self) -> None:
        fingerprint = make_stub_fingerprint(
            few_shot_content_hashes=tuple(sorted(_hash(record) for record in RECORDS))
        )
        with pytest.raises(NothingToExportError, match="empty golden.jsonl"):
            assemble_export(RECORDS, make_labeling(fingerprint), make_clustering(), make_decision())


class TestRedTeamMajor1Payload:
    """Red-team MAJOR-1 replayed (redteam.md, payload ``attack_p1_p2``): the
    fingerprint's ``few_shot_ids`` and ``few_shot_content_hashes`` are sorted
    INDEPENDENTLY (id digests vs content digests), so a same-index zip attributes
    hashes to the wrong few-shot — 5/5 mispaired on the committed store. The fix
    (ADR-0005 Amendment (a)): the blocked detail names the colliding CONTENT HASH
    — the evidence the fingerprint can actually prove — and never a few-shot id.
    """

    def test_fingerprint_tuples_are_not_parallel_on_the_committed_store(self) -> None:
        # The red team's mispairing proof, replayed on the real 5-item store via
        # the real fingerprint constructor (FakeJudge sorts exactly like the
        # production AnthropicJudge).
        shots = load_few_shots(STORE_PATH, sanitizer=sanitize_text)
        fingerprint = FakeJudge(taxonomy=TAXONOMY_V1, few_shots=shots).fingerprint
        true_owner = {shot.content_hash: shot.few_shot_id for shot in shots}
        zip_map = dict(
            zip(fingerprint.few_shot_content_hashes, fingerprint.few_shot_ids, strict=True)
        )
        mispaired = [h for h, named in zip_map.items() if named != true_owner[h]]
        assert mispaired, (
            "the committed store no longer demonstrates the MAJOR-1 tuple mispairing — "
            "re-derive the red-team payload before touching this battery"
        )

    def test_blocked_detail_names_the_true_hash_and_never_a_wrong_id(self) -> None:
        # End-to-end on the two committed shots whose 2-element zip pairing is
        # inverted for BOTH members: the candidate copies the owner's content; the
        # pre-fix zip map named the OTHER shot's id (the red-team lie). The pair is
        # derived from the committed store so a corpus change cannot silently defang
        # the payload — the search asserts such a pair still exists.
        shots = load_few_shots(STORE_PATH, sanitizer=sanitize_text)
        owner = wrongly_named = None
        for candidate_owner in shots:
            for other in shots:
                if other is candidate_owner:
                    continue
                pair = (candidate_owner, other)
                pair_zip = dict(
                    zip(
                        sorted(shot.content_hash for shot in pair),
                        sorted(shot.few_shot_id for shot in pair),
                        strict=True,
                    )
                )
                if pair_zip[candidate_owner.content_hash] == other.few_shot_id:
                    owner, wrongly_named = candidate_owner, other
                    break
            if owner is not None:
                break
        assert owner is not None and wrongly_named is not None, (
            "the committed store no longer has a 2-shot pair whose zip pairing inverts — "
            "re-derive the red-team MAJOR-1 payload before touching this battery"
        )
        pair = (owner, wrongly_named)
        pair_zip = dict(
            zip(
                sorted(shot.content_hash for shot in pair),
                sorted(shot.few_shot_id for shot in pair),
                strict=True,
            )
        )
        # Precondition of the payload: the naive pairing really names the wrong owner.
        assert pair_zip[owner.content_hash] == wrongly_named.few_shot_id

        leaked = make_record(
            source_name="assemble_test.jsonl",
            line_no=9,
            input_text=owner.input_text,
            output_text=owner.output_text,
        )
        records = (RECORDS[0], leaked)
        fingerprint = JudgeFingerprint(
            judge_name="stub",
            model_id="stub-model-requested",
            taxonomy_id=TAXONOMY_V1.taxonomy_id,
            prompt_sha256=hashlib.sha256(b"stub-prompt").hexdigest(),
            few_shot_ids=tuple(sorted(shot.few_shot_id for shot in pair)),
            few_shot_content_hashes=tuple(sorted(shot.content_hash for shot in pair)),
        )
        outcome = assemble_export(
            records, make_labeling(fingerprint, records), make_clustering(records), make_decision()
        )
        assert [entry.record_id for entry in outcome.report.blocked] == [leaked.record_id]
        detail = outcome.report.blocked[0].detail
        assert _hash(leaked) == owner.content_hash  # the true evidence, exactly
        assert owner.content_hash in detail  # …and it is what the detail names
        assert "fs-" not in detail  # no id is recoverable from the fingerprint; none asserted
        assert wrongly_named.few_shot_id not in detail  # the red-team lie can never recur
        assert "(2 hashes)" in detail  # the set size travels with the evidence
