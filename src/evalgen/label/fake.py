"""The deterministic fake judge: content-derived, offline, total (ADR-0003 rule 5).

The verdict is a pure function of the exchange — sha256, never Python's salted
``hash()``, never RNG state — so the same record gets the same label across instances,
runs, and machines. Its labels are NOISE by construction (hash-derived, semantically
arbitrary); the demo says so on its face, and κ machinery in Phase 4 gets *plausible*
fixtures from hand-built label sets, never from this class.

The fingerprint is built with the same ``render_system_prompt``/``prompt_sha256`` as
the real judge, so the offline demo golden pins the production prompt template
byte-for-byte without any network.

The FakeJudge NEVER raises: failure paths are exercised by test-local stub judges
(conftest, the ``StubEmbedder`` precedent) — trigger tokens in production code would be
test scaffolding shipped to users.
"""

from __future__ import annotations

import hashlib

from evalgen.contracts import (
    CANONICAL_SEP,
    FewShotExample,
    JudgeConfidence,
    JudgeFingerprint,
    JudgeVerdict,
    Judgment,
    LabelTaxonomy,
    OutcomeLabel,
    TaskTypeLabel,
)
from evalgen.label.prompt import prompt_sha256, render_system_prompt

FAKE_JUDGE_MODEL_ID = "fake-judge-v1"


class FakeJudge:
    """Deterministic offline Judge — same exchange in, same ``Judgment`` out, always."""

    def __init__(
        self, *, taxonomy: LabelTaxonomy, few_shots: tuple[FewShotExample, ...] = ()
    ) -> None:
        system_prompt = render_system_prompt(taxonomy, few_shots)
        self._fingerprint = JudgeFingerprint(
            judge_name="fake",
            model_id=FAKE_JUDGE_MODEL_ID,
            taxonomy_id=taxonomy.taxonomy_id,
            prompt_sha256=prompt_sha256(system_prompt),
            few_shot_ids=tuple(sorted(s.few_shot_id for s in few_shots)),
            few_shot_content_hashes=tuple(sorted(s.content_hash for s in few_shots)),
        )

    @property
    def fingerprint(self) -> JudgeFingerprint:
        return self._fingerprint

    def judge(self, input_text: str, output_text: str) -> Judgment:
        """Select one member per axis from the exchange's digest bytes (modulo)."""
        joined = CANONICAL_SEP.join([FAKE_JUDGE_MODEL_ID, input_text, output_text])
        digest = hashlib.sha256(joined.encode("utf-8")).digest()
        task_type = list(TaskTypeLabel)[digest[0] % len(TaskTypeLabel)]
        outcome = list(OutcomeLabel)[digest[1] % len(OutcomeLabel)]
        confidence = list(JudgeConfidence)[digest[2] % len(JudgeConfidence)]
        rationale = (
            f"synthetic fake-judge verdict (digest {digest.hex()[:8]}) — " "offline demo/tests only"
        )
        verdict = JudgeVerdict(
            task_type=task_type, outcome=outcome, confidence=confidence, rationale=rationale
        )
        return Judgment(verdict=verdict, model_id=FAKE_JUDGE_MODEL_ID)
