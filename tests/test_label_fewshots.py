"""The few-shot store: tamper-evident loading + the committed v1 store's guarantees.

The committed-store tests pin the planted collision twin (ADR-0003 rule 8): EXACTLY one
few-shot shares its canonical-text hash with the demo's sampled set, so the demo's
``fewshot_collisions=1`` is a designed property, not an accident.

The redaction-clean battery (rule 8 amendment) replays the red-team F-1 payload: a
few-shot carrying the fixture's planted email hashes over RAW text while the mined
record hashes over REDACTED text — the gates would never fire. The loader now refuses
such a store outright, with the production sanitizer injected through the
``TextSanitizer`` seam (``label/`` still never imports ``ingest``).
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from evalgen.contracts import (
    CANONICAL_SEP,
    FewShotExample,
    JudgeConfidence,
    JudgeVerdict,
    OutcomeLabel,
    TaskTypeLabel,
    derive_few_shot_id,
)
from evalgen.demo import _GENERIC_MAPPING  # the demo's own mapping — never a re-typed copy
from evalgen.ingest import load_generic_jsonl, load_tracespan_jsonl, sanitize_text
from evalgen.label import load_few_shots

REPO = Path(__file__).resolve().parents[1]
FEWSHOTS = REPO / "data" / "fewshots" / "judge_v1.jsonl"
FIXTURES = REPO / "data" / "fixtures"
GOLDEN = Path(__file__).resolve().parent / "golden" / "demo_output.txt"


def verdict(rationale: str = "gold rationale") -> JudgeVerdict:
    return JudgeVerdict(
        task_type=TaskTypeLabel.FACTUAL_QUERY,
        outcome=OutcomeLabel.CORRECT,
        confidence=JudgeConfidence.HIGH,
        rationale=rationale,
    )


def shot_line(
    input_text: str, output_text: str, rationale: str = "gold rationale", note: str = ""
) -> str:
    v = verdict(rationale)
    return FewShotExample(
        few_shot_id=derive_few_shot_id(input_text, output_text, v),
        input_text=input_text,
        output_text=output_text,
        verdict=v,
        note=note,
    ).model_dump_json()


# ------------------------------------------------------------------- the loader


def test_valid_file_loads_sorted_by_id(tmp_path: Path) -> None:
    lines = [shot_line("q1?", "a1."), shot_line("q2?", "a2."), shot_line("q3?", "a3.")]
    path = tmp_path / "shots.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    shots = load_few_shots(path, sanitizer=sanitize_text)
    assert len(shots) == 3
    ids = [s.few_shot_id for s in shots]
    assert ids == sorted(ids)


def test_blank_lines_are_ignored_and_empty_file_is_zero_shot(tmp_path: Path) -> None:
    path = tmp_path / "shots.jsonl"
    path.write_text("\n\n" + shot_line("q?", "a.") + "\n\n", encoding="utf-8")
    assert len(load_few_shots(path, sanitizer=sanitize_text)) == 1
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    assert load_few_shots(empty, sanitizer=sanitize_text) == ()


def test_tampered_id_refuses_to_load(tmp_path: Path) -> None:
    line = shot_line("q?", "a.")
    tampered = re.sub(r'"fs-[0-9a-f]{16}"', '"fs-0000000000000000"', line)
    assert tampered != line
    path = tmp_path / "shots.jsonl"
    path.write_text(tampered + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="line 1"):
        load_few_shots(path, sanitizer=sanitize_text)


def test_duplicate_id_refuses(tmp_path: Path) -> None:
    line = shot_line("q?", "a.")
    path = tmp_path / "shots.jsonl"
    path.write_text(line + "\n" + line + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate few_shot_id"):
        load_few_shots(path, sanitizer=sanitize_text)


def test_duplicate_content_hash_refuses(tmp_path: Path) -> None:
    # Same exchange, different rationale -> different few_shot_id, SAME content hash:
    # the leakage gate would not know which example a colliding record matched.
    path = tmp_path / "shots.jsonl"
    path.write_text(
        shot_line("q?", "a.", "one rationale") + "\n" + shot_line("q?", "a.", "another") + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate content hash"):
        load_few_shots(path, sanitizer=sanitize_text)


# ------------------------------- the redaction-clean gate (red-team F-1, replayed)

#: The red-team probe: the fixture's planted email inside a handwritten few-shot.
#: (`cluster_demo.jsonl` plants marc.leroy@… precisely so leaks are detectable.)
REDTEAM_RAW_INPUT = "Contactez-moi a marc.leroy@autoroutes-nord.fr pour le badge ?"
#: The redacted form the red team reported, pinned byte-for-byte.
REDTEAM_REDACTED_INPUT = "Contactez-moi a [REDACTED:email] pour le badge ?"
REDTEAM_OUTPUT = "Adressez la demande de badge au guichet télépéage de votre agence."


def test_redteam_payload_hashes_prove_the_gate_would_miss() -> None:
    # The asymmetry the red team proved (F-1): the few-shot hashes RAW text, the
    # mined record hashes REDACTED text — collision/export gates compare the two.
    assert sanitize_text(REDTEAM_RAW_INPUT) == REDTEAM_REDACTED_INPUT
    raw_hash = hashlib.sha256(
        (REDTEAM_RAW_INPUT + CANONICAL_SEP + REDTEAM_OUTPUT).encode("utf-8")
    ).hexdigest()
    redacted_hash = hashlib.sha256(
        (REDTEAM_REDACTED_INPUT + CANONICAL_SEP + REDTEAM_OUTPUT).encode("utf-8")
    ).hexdigest()
    assert raw_hash != redacted_hash  # the gate would NOT fire — hence the loader gate


def test_redactable_few_shot_refuses_to_load_and_never_echoes_the_secret(
    tmp_path: Path,
) -> None:
    # F-1 regression: the red-team payload as a committed-store line. Before the fix
    # this loaded silently; the secret would ship verbatim to the API and evade both
    # content-hash gates. Now the store refuses to load — structurally, not by review.
    path = tmp_path / "shots.jsonl"
    path.write_text(shot_line(REDTEAM_RAW_INPUT, REDTEAM_OUTPUT) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not redaction-clean") as excinfo:
        load_few_shots(path, sanitizer=sanitize_text)
    message = str(excinfo.value)
    assert "line 1" in message
    assert "input_text" in message
    # The exception is a leak channel too: the raw secret must never be echoed.
    assert "marc.leroy@autoroutes-nord.fr" not in message
    assert "[REDACTED:email]" in message


def test_every_string_field_is_redaction_checked(tmp_path: Path) -> None:
    # The secret does not have to sit in the exchange: the rationale is rendered into
    # the system prompt too, and the note is committed to the repo. All four string
    # fields must be sanitize-neutral.
    cases = {
        "output_text": shot_line("q?", "reponse: marc.leroy@autoroutes-nord.fr"),
        "verdict.rationale": shot_line("q?", "a.", "gold — per marc.leroy@autoroutes-nord.fr"),
        "note": shot_line("q?", "a.", note="twin of marc.leroy@autoroutes-nord.fr's report"),
    }
    for field_name, line in cases.items():
        path = tmp_path / "shots.jsonl"
        path.write_text(line + "\n", encoding="utf-8")
        with pytest.raises(ValueError, match="not redaction-clean") as excinfo:
            load_few_shots(path, sanitizer=sanitize_text)
        assert field_name in str(excinfo.value)
        assert "marc.leroy" not in str(excinfo.value)


def test_the_gate_is_the_injected_sanitizers_judgment(tmp_path: Path) -> None:
    # The seam is honest: the loader enforces whatever sanitizer the composition layer
    # injects (the demo injects the production one — that composition is itself the
    # reviewed, golden-pinned path). A sanitizer that rewrites everything refuses even
    # a clean store; the check is the sanitizer's no-op property, not a pattern list
    # baked into label/.
    path = tmp_path / "shots.jsonl"
    path.write_text(shot_line("q?", "a.") + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not redaction-clean"):
        load_few_shots(path, sanitizer=lambda text: text.upper())


# ------------------------------------------------------------ the committed store


def test_committed_store_loads_clean_and_covers_every_outcome() -> None:
    shots = load_few_shots(FEWSHOTS, sanitizer=sanitize_text)
    assert len(shots) == 5
    outcomes = {s.verdict.outcome for s in shots}
    assert outcomes == set(OutcomeLabel)  # one gold example per outcome class (rule 8)


def test_committed_store_is_redaction_clean_under_the_production_sanitizer() -> None:
    # Loading with the PRODUCTION sanitizer (exactly what the demo injects) IS the
    # guarantee: every string field of every committed example is sanitize-neutral,
    # so few-shot content hashes live in the same (redacted, normalized) text space
    # as record canonical hashes — the gate identity holds byte-for-byte.
    for shot in load_few_shots(FEWSHOTS, sanitizer=sanitize_text):
        for value in (shot.input_text, shot.output_text, shot.verdict.rationale, shot.note):
            assert sanitize_text(value) == value


def test_exactly_one_committed_shot_collides_with_the_demo_sampled_set() -> None:
    # The planted twin — pins the demo's fewshot_collisions at exactly 1.
    generic, _ = load_generic_jsonl(FIXTURES / "generic_demo.jsonl", mapping=_GENERIC_MAPPING)
    cluster, _ = load_generic_jsonl(FIXTURES / "cluster_demo.jsonl", mapping=_GENERIC_MAPPING)
    spans, _ = load_tracespan_jsonl(FIXTURES / "tracespans_demo.jsonl")
    by_id = {r.record_id: r for r in generic + cluster + spans}

    golden = GOLDEN.read_text(encoding="utf-8")
    sample_section = golden.split("[4/5] sample", 1)[1].split("[5/5] label", 1)[0]
    sampled_ids = re.findall(r"rec-[0-9a-f]{16}", sample_section)
    assert len(sampled_ids) == 50

    sampled_hashes = {
        hashlib.sha256(by_id[rid].canonical_text.encode("utf-8")).hexdigest() for rid in sampled_ids
    }
    shots = load_few_shots(FEWSHOTS, sanitizer=sanitize_text)
    colliding = [s for s in shots if s.content_hash in sampled_hashes]
    assert len(colliding) == 1
    assert "planted collision twin" in colliding[0].note


def test_committed_ids_are_traceable_content_derived_values() -> None:
    for shot in load_few_shots(FEWSHOTS, sanitizer=sanitize_text):
        assert shot.few_shot_id == derive_few_shot_id(
            shot.input_text, shot.output_text, shot.verdict
        )
        joined = shot.input_text + CANONICAL_SEP + shot.output_text
        assert shot.content_hash == hashlib.sha256(joined.encode("utf-8")).hexdigest()
