"""FakeJudge: content-derived, deterministic, total (ADR-0003 rule 5).

The pinned verdicts are a REGRESSION pin, platform-stable because the fake derives from
sha256 (never Python's salted ``hash()``): if any of them moves, the fake's mapping
changed and every committed artifact that embeds fake labels (the demo golden) is stale.
"""

from __future__ import annotations

from pathlib import Path

from evalgen.contracts import (
    TAXONOMY_V1,
    JudgeConfidence,
    OutcomeLabel,
    TaskTypeLabel,
)
from evalgen.ingest import sanitize_text
from evalgen.label import FAKE_JUDGE_MODEL_ID, FakeJudge, load_few_shots
from evalgen.label.prompt import prompt_sha256, render_system_prompt

FEWSHOTS = Path(__file__).resolve().parents[1] / "data" / "fewshots" / "judge_v1.jsonl"


def test_same_exchange_same_judgment_across_instances() -> None:
    a = FakeJudge(taxonomy=TAXONOMY_V1)
    b = FakeJudge(taxonomy=TAXONOMY_V1)
    exchange = ("Quels sont les horaires ?", "Ouvert 24h/24.")
    assert a.judge(*exchange) == b.judge(*exchange)
    assert a.judge(*exchange) == a.judge(*exchange)


def test_pinned_verdicts_for_named_exchanges() -> None:
    judge = FakeJudge(taxonomy=TAXONOMY_V1)

    j1 = judge.judge("Quels sont les horaires du peage ?", "Ouvert 24h/24.")
    assert j1.verdict.task_type is TaskTypeLabel.PROCEDURAL_REQUEST
    assert j1.verdict.outcome is OutcomeLabel.CORRECT
    assert j1.verdict.confidence is JudgeConfidence.MEDIUM
    assert j1.verdict.rationale == (
        "synthetic fake-judge verdict (digest 7e743dfd) — offline demo/tests only"
    )

    j2 = judge.judge("Comment activer mon badge ?", "Passez sous un portique.")
    assert j2.verdict.task_type is TaskTypeLabel.OTHER
    assert j2.verdict.outcome is OutcomeLabel.INCORRECT
    assert j2.verdict.confidence is JudgeConfidence.HIGH

    j3 = judge.judge("L'export echoue avec une erreur 500.", "Videz le cache et relancez.")
    assert j3.verdict.task_type is TaskTypeLabel.FACTUAL_QUERY
    assert j3.verdict.outcome is OutcomeLabel.PARTIALLY_CORRECT
    assert j3.verdict.confidence is JudgeConfidence.MEDIUM


def test_model_id_is_the_fake_constant_on_every_judgment() -> None:
    judgment = FakeJudge(taxonomy=TAXONOMY_V1).judge("q", "a")
    assert judgment.model_id == FAKE_JUDGE_MODEL_ID == "fake-judge-v1"


def test_fingerprint_names_the_instrument_and_pins_the_production_prompt() -> None:
    shots = load_few_shots(FEWSHOTS, sanitizer=sanitize_text)
    judge = FakeJudge(taxonomy=TAXONOMY_V1, few_shots=shots)
    fp = judge.fingerprint
    assert fp.judge_name == "fake"
    assert fp.model_id == FAKE_JUDGE_MODEL_ID
    assert fp.taxonomy_id == TAXONOMY_V1.taxonomy_id
    # Built with the SAME render/hash functions as the real judge — the demo golden
    # thereby pins the production prompt template byte-for-byte, offline.
    assert fp.prompt_sha256 == prompt_sha256(render_system_prompt(TAXONOMY_V1, shots))
    assert fp.few_shot_ids == tuple(sorted(s.few_shot_id for s in shots))
    assert fp.few_shot_content_hashes == tuple(sorted(s.content_hash for s in shots))


def test_satisfies_the_judge_protocol_surface() -> None:
    # Structural (no runtime_checkable — the Embedder precedent): the whole surface is
    # the fingerprint property + judge(str, str).
    judge = FakeJudge(taxonomy=TAXONOMY_V1)
    assert callable(judge.judge)
    assert judge.fingerprint.judge_name == "fake"
