"""The export contracts battery (ADR-0005 rule 1).

Three walls, each proven by forgery: a golden line that lies about its own id or
hash refuses to exist; a gate decision that disagrees with its own checks (or
carries a no-op / out-of-scope override) refuses to exist; a manifest whose
embedded reports do not form one coherent run — broken funnel, drifted knob echo,
fingerprint chain break, M-1 digest divergence, gate-section forgery — refuses to
deserialize. The happy paths round-trip through ``model_validate_json`` so every
wall is proven on the disk-read path too.
"""

from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from conftest import make_record, make_stub_fingerprint
from evalgen.config import Settings
from evalgen.contracts import (
    EXPORT_FORMAT_VERSION,
    TAXONOMY_V1,
    AgreementReport,
    BlockedCandidate,
    BlockedCause,
    ClusteringReport,
    ExportGateDecision,
    ExportGateOverride,
    ExportGateVerdict,
    ExportManifest,
    ExportOutcome,
    ExportReport,
    GateCheck,
    GateCheckName,
    GoldenRecord,
    IngestReport,
    InputFileDigest,
    InputFileRole,
    JudgeConfidence,
    JudgeFingerprint,
    LabelingReport,
    LogRecord,
    OutcomeLabel,
    RecordProvenance,
    SamplingReport,
    SettingsSnapshot,
    StratumSample,
    TaskTypeLabel,
)

# ------------------------------------------------------------- GoldenRecord helpers


def _content_hash(record: LogRecord) -> str:
    return hashlib.sha256(record.canonical_text.encode("utf-8")).hexdigest()


def golden_record_kwargs(
    *,
    input_text: str = "Quelle est la couleur du ciel au-dessus de l'A6 ?",
    output_text: str = "Bleu, sauf météo contraire.",
    source_name: str = "records_test.jsonl",
    line_no: int = 7,
    cluster_id: str = "noise",
) -> dict:
    """Valid GoldenRecord kwargs built through the PRODUCTION record constructor."""
    record = make_record(
        source_name=source_name, line_no=line_no, input_text=input_text, output_text=output_text
    )
    return {
        "record_id": record.record_id,
        "taxonomy_id": TAXONOMY_V1.taxonomy_id,
        "task_type": TaskTypeLabel.FACTUAL_QUERY,
        "outcome": OutcomeLabel.CORRECT,
        "judge_model_id": "fake-judge-v1",
        "judge_confidence": JudgeConfidence.HIGH,
        "judge_rationale": "test rationale — contracts battery",
        "input_text": record.input_text,
        "output_text": record.output_text,
        "metadata": record.metadata,
        "provenance": RecordProvenance(
            source_kind=record.origin.source_kind,
            source_name=record.origin.source_name,
            line_no=record.origin.line_no,
            span_id=record.origin.span_id,
            task_id=record.origin.task_id,
            timestamp=record.timestamp,
            cluster_id=cluster_id,
            content_hash=_content_hash(record),
        ),
    }


def _reprovenance(provenance: RecordProvenance, **overrides) -> RecordProvenance:
    return RecordProvenance(**{**dict(provenance), **overrides})


class TestGoldenRecord:
    def test_valid_record_round_trips_through_json(self) -> None:
        record = GoldenRecord(**golden_record_kwargs())
        assert GoldenRecord.model_validate_json(record.model_dump_json()) == record

    def test_forged_record_id_refuses(self) -> None:
        kwargs = golden_record_kwargs()
        kwargs["record_id"] = "rec-" + "0" * 16
        with pytest.raises(ValidationError, match="forged golden line"):
            GoldenRecord(**kwargs)

    def test_forged_content_hash_refuses(self) -> None:
        kwargs = golden_record_kwargs()
        kwargs["provenance"] = _reprovenance(kwargs["provenance"], content_hash="0" * 64)
        with pytest.raises(ValidationError, match="exact-dedup identity"):
            GoldenRecord(**kwargs)

    def test_edited_input_text_refuses(self) -> None:
        # Editing the text breaks BOTH derivations; the id check fires first.
        kwargs = golden_record_kwargs()
        kwargs["input_text"] = kwargs["input_text"] + " (édité)"
        with pytest.raises(ValidationError, match="forged golden line"):
            GoldenRecord(**kwargs)

    @pytest.mark.parametrize("bad_name", ["data/x.jsonl", "C:\\logs\\x.jsonl"])
    def test_source_name_with_a_path_separator_refuses(self, bad_name: str) -> None:
        kwargs = golden_record_kwargs()
        with pytest.raises(ValidationError, match="basename"):
            _reprovenance(kwargs["provenance"], source_name=bad_name)

    @pytest.mark.parametrize("bad_hash", ["0" * 63, "G" * 64, "0" * 64 + "0"])
    def test_bad_content_hash_format_refuses(self, bad_hash: str) -> None:
        kwargs = golden_record_kwargs()
        with pytest.raises(ValidationError, match="64 lowercase hex"):
            _reprovenance(kwargs["provenance"], content_hash=bad_hash)

    def test_minor1_label_flip_validates_the_scoped_line_boundary(self) -> None:
        # Red-team MINOR-1, boundary PINNED (ADR-0005 Amendment (b)): the line
        # validator covers identity + texts ONLY. A flipped label VALIDATES here —
        # labels are the judge's opinion, not derivable from the line, so no
        # per-line check can exist. Their fence is file-level (golden_jsonl_sha256
        # + /repro-audit regeneration) — proven to fire on this exact payload in
        # test_export_serialize.py::TestRedTeamMinor1Payload.
        kwargs = golden_record_kwargs()
        assert kwargs["outcome"] is OutcomeLabel.CORRECT
        kwargs["outcome"] = OutcomeLabel.INCORRECT
        flipped = GoldenRecord(**kwargs)  # no refusal — and that is the stated scope
        assert GoldenRecord.model_validate_json(flipped.model_dump_json()) == flipped


# ------------------------------------------------------- ExportGateDecision helpers


def make_checks(*flags: bool) -> tuple[GateCheck, ...]:
    return tuple(
        GateCheck(name=name, passed=passed, detail="hand-built check — contracts battery")
        for name, passed in zip(GateCheckName, flags, strict=True)
    )


OVERRIDE = ExportGateOverride(reason="test override — carries the honest low kappa")


def overridden_decision_kwargs(**overrides) -> dict:
    """The demo shape: kappa fails, override present → passed_with_override."""
    kwargs = {
        "min_export_kappa": 0.6,
        "checks": make_checks(True, True, True, True, False),
        "kappa": 0.513109,
        "ci_lower": 0.286421,
        "ci_upper": 0.707241,
        "ci_straddles_threshold": False,
        "override": OVERRIDE,
        "verdict": ExportGateVerdict.PASSED_WITH_OVERRIDE,
    }
    kwargs.update(overrides)
    return kwargs


def passing_decision_kwargs(**overrides) -> dict:
    """All five pass; the CI straddles the threshold (stated, never blocking)."""
    kwargs = {
        "min_export_kappa": 0.6,
        "checks": make_checks(True, True, True, True, True),
        "kappa": 0.65,
        "ci_lower": 0.55,
        "ci_upper": 0.75,
        "ci_straddles_threshold": True,
        "override": None,
        "verdict": ExportGateVerdict.PASSED,
    }
    kwargs.update(overrides)
    return kwargs


PASSING_DECISION = ExportGateDecision(**passing_decision_kwargs())


class TestExportGateDecision:
    def test_kappa_fail_with_override_is_valid_and_round_trips(self) -> None:
        decision = ExportGateDecision(**overridden_decision_kwargs())
        assert decision.verdict is ExportGateVerdict.PASSED_WITH_OVERRIDE
        assert ExportGateDecision.model_validate_json(decision.model_dump_json()) == decision

    def test_kappa_fail_without_override_is_blocked(self) -> None:
        decision = ExportGateDecision(
            **overridden_decision_kwargs(override=None, verdict=ExportGateVerdict.BLOCKED)
        )
        assert decision.verdict is ExportGateVerdict.BLOCKED

    def test_all_pass_plus_override_refuses(self) -> None:
        with pytest.raises(ValidationError, match="must override something"):
            ExportGateDecision(**passing_decision_kwargs(override=OVERRIDE))

    def test_kappa_fail_no_override_claiming_passed_refuses(self) -> None:
        with pytest.raises(ValidationError, match="disagrees with the checks"):
            ExportGateDecision(
                **overridden_decision_kwargs(override=None, verdict=ExportGateVerdict.PASSED)
            )

    def test_override_alongside_non_overridable_failure_refuses(self) -> None:
        with pytest.raises(ValidationError, match="non-overridable"):
            ExportGateDecision(
                **overridden_decision_kwargs(
                    checks=make_checks(True, True, True, False, False),
                )
            )

    def test_straddle_asserted_when_kappa_fails_refuses(self) -> None:
        with pytest.raises(ValidationError, match="straddle"):
            ExportGateDecision(**overridden_decision_kwargs(ci_straddles_threshold=True))

    def test_straddle_denied_when_interval_straddles_refuses(self) -> None:
        with pytest.raises(ValidationError, match="straddle"):
            ExportGateDecision(**passing_decision_kwargs(ci_straddles_threshold=False))

    def test_swapped_check_order_refuses(self) -> None:
        checks = make_checks(True, True, True, True, False)
        with pytest.raises(ValidationError, match="declared\\s+order"):
            ExportGateDecision(
                **overridden_decision_kwargs(checks=(checks[1], checks[0]) + checks[2:])
            )

    def test_dropped_check_refuses(self) -> None:
        checks = make_checks(True, True, True, True, False)
        with pytest.raises(ValidationError, match="declared\\s+order"):
            ExportGateDecision(**overridden_decision_kwargs(checks=checks[:4]))

    def test_one_ulp_kappa_claiming_pass_refuses(self) -> None:
        # kappa=0.599999 with kappa_threshold.passed=True: the printed number is the
        # gated number — the flag cannot be asserted past it.
        with pytest.raises(ValidationError, match="cannot be asserted"):
            ExportGateDecision(
                **passing_decision_kwargs(
                    kappa=0.599999, ci_lower=None, ci_upper=None, ci_straddles_threshold=False
                )
            )

    def test_kappa_exactly_at_threshold_passes(self) -> None:
        decision = ExportGateDecision(
            **passing_decision_kwargs(
                kappa=0.6, ci_lower=None, ci_upper=None, ci_straddles_threshold=False
            )
        )
        assert decision.checks[-1].passed is True

    def test_missing_kappa_with_healthy_headline_checks_refuses(self) -> None:
        with pytest.raises(ValidationError, match="missing or undefined headline"):
            ExportGateDecision(
                **overridden_decision_kwargs(
                    checks=make_checks(True, True, True, True, False),
                    kappa=None,
                    ci_lower=None,
                    ci_upper=None,
                )
            )

    def test_missing_kappa_with_ci_bounds_refuses(self) -> None:
        with pytest.raises(ValidationError, match="CI bounds are present"):
            ExportGateDecision(
                **overridden_decision_kwargs(
                    checks=make_checks(False, False, True, True, False), kappa=None
                )
            )

    def test_half_present_ci_refuses(self) -> None:
        with pytest.raises(ValidationError, match="both be present or both be None"):
            ExportGateDecision(**overridden_decision_kwargs(ci_upper=None))


# --------------------------------------------------------------------- ExportReport


def report_kwargs_export(**overrides) -> dict:
    kwargs = {
        "judge": make_stub_fingerprint(),
        "gate": PASSING_DECISION,
        "candidates_in": 3,
        "exported": 2,
        "blocked": (
            BlockedCandidate(
                record_id="rec-aaaaaaaaaaaaaaaa",
                cause=BlockedCause.FEWSHOT_COLLISION,
                detail="content_hash matches few-shot fs-0000000000000000",
            ),
        ),
    }
    kwargs.update(overrides)
    return kwargs


class TestExportReport:
    def test_valid_report_constructs(self) -> None:
        report = ExportReport(**report_kwargs_export())
        assert report.candidates_in == report.exported + len(report.blocked)

    def test_wrong_sum_refuses(self) -> None:
        with pytest.raises(ValidationError, match="every candidate lands"):
            ExportReport(**report_kwargs_export(candidates_in=4))

    def test_unsorted_blocked_refuses(self) -> None:
        blocked = (
            BlockedCandidate(
                record_id="rec-bbbbbbbbbbbbbbbb",
                cause=BlockedCause.FEWSHOT_COLLISION,
                detail="content_hash matches few-shot fs-0000000000000000",
            ),
            BlockedCandidate(
                record_id="rec-aaaaaaaaaaaaaaaa",
                cause=BlockedCause.FEWSHOT_COLLISION,
                detail="content_hash matches few-shot fs-0000000000000001",
            ),
        )
        with pytest.raises(ValidationError, match="sorted by record_id"):
            ExportReport(**report_kwargs_export(candidates_in=4, blocked=blocked))

    def test_duplicate_blocked_refuses(self) -> None:
        entry = BlockedCandidate(
            record_id="rec-aaaaaaaaaaaaaaaa",
            cause=BlockedCause.FEWSHOT_COLLISION,
            detail="content_hash matches few-shot fs-0000000000000000",
        )
        with pytest.raises(ValidationError, match="unique"):
            ExportReport(**report_kwargs_export(candidates_in=4, blocked=(entry, entry)))

    def test_blocked_verdict_refuses(self) -> None:
        blocked_decision = ExportGateDecision(
            **overridden_decision_kwargs(override=None, verdict=ExportGateVerdict.BLOCKED)
        )
        with pytest.raises(ValidationError, match="cannot carry a 'blocked' verdict"):
            ExportReport(**report_kwargs_export(gate=blocked_decision))

    def test_zero_exported_refuses(self) -> None:
        with pytest.raises(ValidationError):
            ExportReport(**report_kwargs_export(exported=0, candidates_in=1))


# -------------------------------------------------------------------- ExportOutcome


def _golden(line_no: int, text: str) -> GoldenRecord:
    return GoldenRecord(**golden_record_kwargs(line_no=line_no, input_text=text))


class TestExportOutcome:
    def _records(self) -> tuple[GoldenRecord, GoldenRecord]:
        return (_golden(1, "Première question ?"), _golden(2, "Deuxième question ?"))

    def _report(self, exported: int = 2, **overrides) -> ExportReport:
        kwargs = report_kwargs_export(candidates_in=exported, exported=exported, blocked=())
        kwargs.update(overrides)
        return ExportReport(**kwargs)

    def test_valid_outcome_round_trips(self) -> None:
        outcome = ExportOutcome(golden_records=self._records(), report=self._report())
        assert ExportOutcome.model_validate_json(outcome.model_dump_json()) == outcome

    def test_count_mismatch_refuses(self) -> None:
        with pytest.raises(ValidationError, match="report.exported"):
            ExportOutcome(golden_records=self._records()[:1], report=self._report())

    def test_unsorted_records_refuse(self) -> None:
        first, second = self._records()
        with pytest.raises(ValidationError, match="strictly ascending"):
            ExportOutcome(golden_records=(second, first), report=self._report())

    def test_duplicate_record_ids_refuse(self) -> None:
        first, _ = self._records()
        with pytest.raises(ValidationError, match="duplicate record_ids"):
            ExportOutcome(golden_records=(first, first), report=self._report())

    def test_taxonomy_drift_refuses(self) -> None:
        kwargs = golden_record_kwargs(line_no=1, input_text="Première question ?")
        kwargs["taxonomy_id"] = "tax-000000000000"
        drifted = GoldenRecord(**kwargs)
        with pytest.raises(ValidationError, match="taxonomy_id"):
            ExportOutcome(golden_records=(drifted,), report=self._report(exported=1))

    def test_exported_record_overlapping_blocked_refuses(self) -> None:
        first, second = self._records()
        report = ExportReport(
            **report_kwargs_export(
                candidates_in=3,
                exported=2,
                blocked=(
                    BlockedCandidate(
                        record_id=first.record_id,
                        cause=BlockedCause.FEWSHOT_COLLISION,
                        detail="content_hash matches few-shot fs-0000000000000000",
                    ),
                ),
            )
        )
        with pytest.raises(ValidationError, match="exported AND blocked"):
            ExportOutcome(golden_records=(first, second), report=report)

    def test_contaminated_outcome_is_unrepresentable(self) -> None:
        # THE deliberately-leaked payload: an exported record whose content_hash sits
        # in the judge's few-shot set refuses to exist — even hand-forged.
        first, second = self._records()
        leaked_fingerprint = make_stub_fingerprint(
            few_shot_content_hashes=(first.provenance.content_hash,)
        )
        report = self._report(judge=leaked_fingerprint)
        with pytest.raises(ValidationError, match="unrepresentable"):
            ExportOutcome(golden_records=(first, second), report=report)


# ---------------------------------------------------------------- SettingsSnapshot


class TestSettingsSnapshot:
    def test_field_set_mirrors_settings_exactly(self) -> None:
        # A new knob added to Settings without a SettingsSnapshot mirror would skip
        # provenance silently — this pin makes that a loud failure (ADR-0005 §2).
        assert set(SettingsSnapshot.model_fields) == set(Settings.model_fields)

    def test_export_format_version_is_pinned(self) -> None:
        assert EXPORT_FORMAT_VERSION == 1


# ------------------------------------------------------------------- ExportManifest


def _remanifest(manifest: ExportManifest, **overrides) -> ExportManifest:
    return ExportManifest(**{**dict(manifest), **overrides})


def _sorted_inputs(files) -> tuple[InputFileDigest, ...]:
    return tuple(sorted(files, key=lambda f: (f.role.value, f.name)))


class TestExportManifest:
    def test_demo_manifest_validates_and_round_trips(self, demo_export_artifacts) -> None:
        _, manifest = demo_export_artifacts
        assert manifest.export_format_version == EXPORT_FORMAT_VERSION
        rebuilt = ExportManifest.model_validate_json(manifest.model_dump_json())
        assert rebuilt == manifest

    def test_fingerprint_chain_break_refuses(self, demo_export_artifacts) -> None:
        _, manifest = demo_export_artifacts
        drifted_judge = JudgeFingerprint(
            **{**dict(manifest.labeling.judge), "model_id": "fake-judge-v2"}
        )
        drifted_labeling = LabelingReport(**{**dict(manifest.labeling), "judge": drifted_judge})
        with pytest.raises(ValidationError, match="labeling.judge != agreement.judge"):
            _remanifest(manifest, labeling=drifted_labeling)

    def test_m1_digest_mismatch_refuses(self, demo_export_artifacts) -> None:
        _, manifest = demo_export_artifacts
        forged = [
            (
                InputFileDigest(
                    name=f.name, sha256=hashlib.sha256(b"tampered").hexdigest(), role=f.role
                )
                if f.role is InputFileRole.HUMAN_LABELS
                else f
            )
            for f in manifest.input_files
        ]
        with pytest.raises(ValidationError, match="copy duty is structural"):
            _remanifest(manifest, input_files=_sorted_inputs(forged))

    def test_missing_human_labels_input_refuses(self, demo_export_artifacts) -> None:
        _, manifest = demo_export_artifacts
        pruned = [f for f in manifest.input_files if f.role is not InputFileRole.HUMAN_LABELS]
        with pytest.raises(ValidationError, match="exactly one is required"):
            _remanifest(manifest, input_files=_sorted_inputs(pruned))

    def test_duplicate_human_labels_input_refuses(self, demo_export_artifacts) -> None:
        _, manifest = demo_export_artifacts
        extra = InputFileDigest(
            name="annotations_b.jsonl",
            sha256=hashlib.sha256(b"second ground truth?").hexdigest(),
            role=InputFileRole.HUMAN_LABELS,
        )
        with pytest.raises(ValidationError, match="exactly one is required"):
            _remanifest(manifest, input_files=_sorted_inputs((*manifest.input_files, extra)))

    def test_unbound_agreement_refuses(self, demo_export_artifacts) -> None:
        _, manifest = demo_export_artifacts
        unbound = AgreementReport(**{**dict(manifest.agreement), "human_labels_sha256": None})
        with pytest.raises(ValidationError, match="human_labels_sha256 is None"):
            _remanifest(manifest, agreement=unbound)

    def test_unsorted_input_files_refuse(self, demo_export_artifacts) -> None:
        _, manifest = demo_export_artifacts
        shuffled = tuple(reversed(manifest.input_files))
        with pytest.raises(ValidationError, match="sorted by \\(role, name\\)"):
            _remanifest(manifest, input_files=shuffled)

    def test_source_log_without_ingest_report_refuses(self, demo_export_artifacts) -> None:
        _, manifest = demo_export_artifacts
        with pytest.raises(ValidationError, match="ingest"):
            _remanifest(manifest, ingest=manifest.ingest[:2])

    # ------------------------------------------------------------- the funnel wall

    def test_funnel_break_ingest_to_dedup_refuses(self, demo_export_artifacts) -> None:
        _, manifest = demo_export_artifacts
        first = manifest.ingest[0]
        forged = IngestReport(
            **{
                **dict(first),
                "lines_read": first.lines_read + 1,
                "records_normalized": first.records_normalized + 1,
            }
        )
        with pytest.raises(ValidationError, match="funnel break: sum\\(ingest"):
            _remanifest(manifest, ingest=(forged, *manifest.ingest[1:]))

    def test_funnel_break_dedup_to_clustering_refuses(self, demo_export_artifacts) -> None:
        _, manifest = demo_export_artifacts
        clustering = manifest.clustering
        forged = ClusteringReport(
            **{
                **dict(clustering),
                "records_in": clustering.records_in + 1,
                "noise_record_ids": (*clustering.noise_record_ids, "rec-zzzzzzzzzzzzzzzz"),
            }
        )
        with pytest.raises(ValidationError, match="funnel break: dedup.records_out"):
            _remanifest(manifest, clustering=forged)

    def test_funnel_break_clustering_to_sampling_refuses(self, demo_export_artifacts) -> None:
        _, manifest = demo_export_artifacts
        sampling = manifest.sampling
        forged = SamplingReport(
            **{
                **dict(sampling),
                "records_in": sampling.records_in + 1,
                "strata": (
                    *sampling.strata,
                    StratumSample(
                        cluster_id="zz-forged", stratum_size=1, quota=0, sampled_record_ids=()
                    ),
                ),
            }
        )
        with pytest.raises(ValidationError, match="funnel break: clustering.records_in"):
            _remanifest(manifest, sampling=forged)

    def test_funnel_break_sampling_to_labeling_refuses(self, demo_export_artifacts) -> None:
        _, manifest = demo_export_artifacts
        labeling = manifest.labeling
        forged = LabelingReport(
            **{
                **dict(labeling),
                "records_in": labeling.records_in + 1,
                "skipped_budget": labeling.skipped_budget + 1,
                "skipped_budget_record_ids": (
                    *labeling.skipped_budget_record_ids,
                    "rec-zzzzzzzzzzzzzzzz",
                ),
            }
        )
        with pytest.raises(ValidationError, match="funnel break: sampling.total_sampled"):
            _remanifest(manifest, labeling=forged)

    def test_funnel_break_labeling_to_export_refuses(self, demo_export_artifacts) -> None:
        _, manifest = demo_export_artifacts
        export = manifest.export
        forged = ExportReport(
            **{
                **dict(export),
                "candidates_in": export.candidates_in + 1,
                "exported": export.exported + 1,
            }
        )
        with pytest.raises(ValidationError, match="funnel break: labeling.labeled"):
            _remanifest(manifest, export=forged)

    # --------------------------------------------------------------- knob echoes

    @pytest.mark.parametrize(
        ("knob", "forged_value"),
        [
            ("near_dup_threshold", 0.93),
            ("min_cluster_size", 6),
            ("hash_embedding_dim", 256),
            ("seed", 1751),
            ("sample_size", 51),
            ("max_labels_per_run", 501),
            ("min_human_labels", 29),
            ("bootstrap_resamples", 9999),
            ("min_class_support", 4),
            ("min_export_kappa", 0.5),
        ],
    )
    def test_knob_echo_mismatch_refuses(self, demo_export_artifacts, knob, forged_value) -> None:
        _, manifest = demo_export_artifacts
        forged = SettingsSnapshot(**{**dict(manifest.settings), knob: forged_value})
        with pytest.raises(ValidationError, match="knob echo mismatch"):
            _remanifest(manifest, settings=forged)

    # --------------------------------------------------------- gate-section forgery

    def _regate(self, manifest: ExportManifest, **overrides) -> ExportManifest:
        forged_gate = ExportGateDecision(**{**dict(manifest.export.gate), **overrides})
        forged_export = ExportReport(**{**dict(manifest.export), "gate": forged_gate})
        return _remanifest(manifest, export=forged_export)

    def test_gate_kappa_shaved_by_one_millionth_refuses(self, demo_export_artifacts) -> None:
        _, manifest = demo_export_artifacts
        with pytest.raises(ValidationError, match="gate forgery: gate.kappa"):
            self._regate(manifest, kappa=0.51311)

    def test_gate_ci_dropped_refuses(self, demo_export_artifacts) -> None:
        _, manifest = demo_export_artifacts
        with pytest.raises(ValidationError, match="gate forgery: gate CI"):
            self._regate(manifest, ci_lower=None, ci_upper=None)

    def test_gate_ci_widened_refuses(self, demo_export_artifacts) -> None:
        _, manifest = demo_export_artifacts
        with pytest.raises(ValidationError, match="gate forgery: gate CI"):
            self._regate(manifest, ci_upper=0.999999)

    def test_override_cannot_be_laundered_away(self, demo_export_artifacts) -> None:
        # Stripping the override forces the verdict to 'blocked' (decision coherence),
        # and a blocked verdict cannot ride an ExportReport at all.
        _, manifest = demo_export_artifacts
        with pytest.raises(ValidationError, match="blocked"):
            self._regate(manifest, override=None, verdict=ExportGateVerdict.BLOCKED)

    def test_golden_sha256_format_is_enforced(self, demo_export_artifacts) -> None:
        _, manifest = demo_export_artifacts
        with pytest.raises(ValidationError, match="64 lowercase hex"):
            _remanifest(manifest, golden_jsonl_sha256="not-a-digest")
