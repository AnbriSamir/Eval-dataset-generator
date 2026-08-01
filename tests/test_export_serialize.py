"""The canonical-recipe battery (ADR-0005 rule 4): the golden.jsonl line recipe is
pinned to EXACT bytes (accented French text, sorted keys, compact separators,
``ensure_ascii=False``, trailing LF); the meta.json recipe is pinned (sorted keys,
indent=2, trailing LF, ``"volatile": null`` when absent); and
``canonical_deterministic_bytes`` is proven to be the writer's own recipe AND
invariant to every volatile value — the ``/repro-audit`` contract.
"""

from __future__ import annotations

import hashlib
import json

from conftest import make_stub_fingerprint
from evalgen.contracts import (
    CANONICAL_SEP,
    TAXONOMY_V1,
    ExportManifest,
    ExportOutcome,
    ExportReport,
    GoldenRecord,
    JudgeConfidence,
    OutcomeLabel,
    RecordOrigin,
    RecordProvenance,
    SourceKind,
    TaskTypeLabel,
    VolatileProvenance,
    derive_record_id,
)
from evalgen.export import (
    canonical_deterministic_bytes,
    render_golden_jsonl,
    render_meta_json,
    sha256_hex,
)
from test_contracts_export import PASSING_DECISION

INPUT = "Quelle est la capacité maximale de l’autoroute A6 ?"
OUTPUT = "Environ 2 000 véhicules par heure et par voie."

#: The EXACT expected line for the record below — the recipe's spec, byte for byte:
#: sorted keys, compact separators, ensure_ascii=False (accents survive as UTF-8),
#: enums as values, None as null. Any recipe drift breaks this literal.
EXPECTED_LINE = (
    '{"input_text":"Quelle est la capacité maximale de l’autoroute A6 ?",'
    '"judge_confidence":"high","judge_model_id":"fake-judge-v1",'
    '"judge_rationale":"vérifié — débit théorique par voie",'
    '"metadata":{"lang":"fr","unité":"véh/h"},"outcome":"correct",'
    '"output_text":"Environ 2 000 véhicules par heure et par voie.",'
    '"provenance":{"cluster_id":"noise",'
    '"content_hash":"77f5e74046ab80cddc027664d5c318aad941b35088220426ba5b44d060363934",'
    '"line_no":3,"source_kind":"generic_jsonl","source_name":"serialize_test.jsonl",'
    '"span_id":"evt-42","task_id":null,"timestamp":null},'
    '"record_id":"rec-50a7ae85b288984c","task_type":"factual_query",'
    '"taxonomy_id":"tax-d9ca3b87b403"}'
)


def _hand_built_record() -> GoldenRecord:
    origin = RecordOrigin(
        source_kind=SourceKind.GENERIC_JSONL,
        source_name="serialize_test.jsonl",
        line_no=3,
        span_id="evt-42",
        task_id=None,
    )
    content_hash = hashlib.sha256((INPUT + CANONICAL_SEP + OUTPUT).encode("utf-8")).hexdigest()
    return GoldenRecord(
        record_id=derive_record_id(origin, INPUT, OUTPUT),
        taxonomy_id=TAXONOMY_V1.taxonomy_id,
        task_type=TaskTypeLabel.FACTUAL_QUERY,
        outcome=OutcomeLabel.CORRECT,
        judge_model_id="fake-judge-v1",
        judge_confidence=JudgeConfidence.HIGH,
        judge_rationale="vérifié — débit théorique par voie",
        input_text=INPUT,
        output_text=OUTPUT,
        metadata={"lang": "fr", "unité": "véh/h"},
        provenance=RecordProvenance(
            source_kind=SourceKind.GENERIC_JSONL,
            source_name="serialize_test.jsonl",
            line_no=3,
            span_id="evt-42",
            task_id=None,
            timestamp=None,
            cluster_id="noise",
            content_hash=content_hash,
        ),
    )


def _mini_outcome() -> ExportOutcome:
    record = _hand_built_record()
    report = ExportReport(
        judge=make_stub_fingerprint(),
        gate=PASSING_DECISION,
        candidates_in=1,
        exported=1,
        blocked=(),
    )
    return ExportOutcome(golden_records=(record,), report=report)


class TestGoldenJsonlRecipe:
    def test_line_recipe_is_pinned_byte_for_byte(self) -> None:
        assert render_golden_jsonl(_mini_outcome()) == EXPECTED_LINE + "\n"

    def test_accents_survive_as_utf8_never_escapes(self) -> None:
        text = render_golden_jsonl(_mini_outcome())
        assert "capacité" in text and "véhicules" in text
        assert "\\u" not in text  # ensure_ascii=False: no ASCII escape sequences

    def test_file_shape_n_lines_final_newline_lf_only(self, demo_export_artifacts) -> None:
        outcome, _ = demo_export_artifacts
        text = render_golden_jsonl(outcome)
        assert text.endswith("\n") and not text.endswith("\n\n")
        assert "\r" not in text
        lines = text.splitlines()
        assert len(lines) == outcome.report.exported
        for line in lines:
            assert json.loads(line)  # every line is standalone JSON

    def test_every_line_revalidates_as_a_golden_record(self, demo_export_artifacts) -> None:
        outcome, _ = demo_export_artifacts
        for line in render_golden_jsonl(outcome).splitlines():
            GoldenRecord.model_validate_json(line)  # self-verifying on the read path


class TestMetaJsonRecipe:
    def test_two_sections_sorted_keys_indent_two_trailing_newline(
        self, demo_export_artifacts
    ) -> None:
        _, manifest = demo_export_artifacts
        text = render_meta_json(manifest)
        assert text.startswith('{\n  "deterministic": {')
        assert text.endswith("\n")
        parsed = json.loads(text)
        assert set(parsed) == {"deterministic", "volatile"}
        assert parsed["volatile"] is not None  # the demo records real volatile values

    def test_absent_volatile_renders_null(self, demo_export_artifacts) -> None:
        _, manifest = demo_export_artifacts
        stripped = ExportManifest(**{**dict(manifest), "volatile": None})
        text = render_meta_json(stripped)
        assert '"volatile": null' in text
        assert json.loads(text)["volatile"] is None

    def test_canonical_bytes_equal_the_deterministic_section(self, demo_export_artifacts) -> None:
        _, manifest = demo_export_artifacts
        text = render_meta_json(manifest)
        payload = json.loads(canonical_deterministic_bytes(text).decode("utf-8"))
        assert payload == json.loads(text)["deterministic"]

    def test_canonical_bytes_are_invariant_to_volatile_values(self, demo_export_artifacts) -> None:
        # THE /repro-audit property: two manifests differing ONLY in volatile
        # produce identical deterministic bytes — no field-exception list needed.
        _, manifest = demo_export_artifacts
        other = ExportManifest(
            **{
                **dict(manifest),
                "volatile": VolatileProvenance(
                    git_commit=None,
                    generated_at="1999-12-31T23:59:59+00:00",
                    environment={"python": "0.0.0"},
                ),
            }
        )
        assert canonical_deterministic_bytes(render_meta_json(manifest)) == (
            canonical_deterministic_bytes(render_meta_json(other))
        )


class TestDeterminismAndDigests:
    def test_double_run_byte_identity_of_all_three_renderers(self, demo_export_artifacts) -> None:
        outcome, manifest = demo_export_artifacts
        assert render_golden_jsonl(outcome) == render_golden_jsonl(outcome)
        assert render_meta_json(manifest) == render_meta_json(manifest)
        meta = render_meta_json(manifest)
        assert canonical_deterministic_bytes(meta) == canonical_deterministic_bytes(meta)

    def test_sha256_hex_encodes_str_as_utf8(self) -> None:
        assert sha256_hex("péage") == sha256_hex("péage".encode())
        assert sha256_hex(b"") == hashlib.sha256(b"").hexdigest()

    def test_manifest_names_the_exact_golden_bytes(self, demo_export_artifacts) -> None:
        outcome, manifest = demo_export_artifacts
        assert manifest.golden_jsonl_sha256 == sha256_hex(render_golden_jsonl(outcome))


def _flip_first_line_outcome(golden_text: str) -> tuple[str, str]:
    """The red-team forgery: flip line 0's ``outcome`` label, keep everything else
    byte-identical (same canonical line recipe). Returns (flipped_file, flipped_line)."""
    lines = golden_text.splitlines()
    payload = json.loads(lines[0])
    payload["outcome"] = "correct" if payload["outcome"] != "correct" else "incorrect"
    flipped_line = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "\n".join([flipped_line, *lines[1:]]) + "\n", flipped_line


class TestRedTeamMinor1Payload:
    """Red-team MINOR-1 replayed (redteam.md, payloads P2/P3): a label-flipped
    golden line validates at the LINE level (labels are opinions — the scoped
    boundary, ADR-0005 Amendment (b)), but the file-level fence fires on this
    exact payload: the forged file's digest can never equal the digest the
    manifest binds, and regeneration reproduces the bound digest exactly."""

    def test_flipped_line_validates_but_the_file_fence_fires(self, demo_export_artifacts) -> None:
        outcome, manifest = demo_export_artifacts
        golden_text = render_golden_jsonl(outcome)
        flipped_text, flipped_line = _flip_first_line_outcome(golden_text)
        # P2, pinned: the flipped LINE parses cleanly — no per-line refusal exists
        # for a label, by construction (the judge's opinion is not derivable).
        GoldenRecord.model_validate_json(flipped_line)
        # The designed fence catches the payload: meta.json binds the TRUE bytes…
        assert sha256_hex(golden_text) == manifest.golden_jsonl_sha256
        # …and the forged file can never match the manifest it would ship with.
        assert sha256_hex(flipped_text) != manifest.golden_jsonl_sha256
        # /repro-audit's regeneration reproduces the BOUND digest, not the forged one.
        assert sha256_hex(render_golden_jsonl(outcome)) == manifest.golden_jsonl_sha256

    def test_recomputed_sha_manifest_parses_the_stated_residual(
        self, demo_export_artifacts
    ) -> None:
        # P3, pinned as the STATED residual (ADR-0005 Amendment (b)): a manifest
        # rebuilt around the forged file's recomputed digest deserializes — the
        # validator format-checks the hex and never re-reads golden.jsonl (no I/O
        # in contracts). The catch for a coherent forged PAIR is /repro-audit's
        # byte-diff against regeneration, proven in the test above.
        outcome, manifest = demo_export_artifacts
        flipped_text, _ = _flip_first_line_outcome(render_golden_jsonl(outcome))
        forged = ExportManifest(
            **{**dict(manifest), "golden_jsonl_sha256": sha256_hex(flipped_text)}
        )
        assert forged.golden_jsonl_sha256 != manifest.golden_jsonl_sha256
