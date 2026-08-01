"""Shared Phase 2/3 test plumbing: stub embedder, stub judges, a record factory —
plus (Phase 5) the session-cached export-demo artifacts.

``StubEmbedder`` returns pre-baked vectors keyed by EXACT text — near-dup boundary and
chain fixtures are thereby exact (injected dots), not hash-approximate. ``StubJudge`` /
``RaisingJudge`` are the judge-side equivalents (ADR-0003 rule 5: the FakeJudge never
raises — failure paths live in test-local stubs, never in production trigger tokens).
All satisfy their Protocols; tests inject them wherever production code takes the seam.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import datetime

import numpy as np
import pytest

from evalgen.contracts import (
    TAXONOMY_V1,
    EmbedderFingerprint,
    JudgeConfidence,
    JudgeFingerprint,
    JudgeVerdict,
    Judgment,
    LogRecord,
    OutcomeLabel,
    SourceKind,
    TaskTypeLabel,
)
from evalgen.ingest import build_record


class StubEmbedder:
    """Test-only Embedder: returns pre-baked unit vectors keyed by exact text."""

    def __init__(self, mapping: dict[str, tuple[float, ...]], dim: int) -> None:
        self._mapping = mapping
        self._dim = dim

    @property
    def fingerprint(self) -> EmbedderFingerprint:
        return EmbedderFingerprint(
            name="stub", dim=self._dim, analyzer="stub", ngram_min=1, ngram_max=1
        )

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        if not texts:
            return np.empty((0, self._dim), dtype=np.float64)
        return np.array([self._mapping[text] for text in texts], dtype=np.float64)


def make_record(
    *,
    source_name: str = "stub.jsonl",
    line_no: int = 1,
    input_text: str,
    output_text: str = "ok",
    timestamp: datetime | None = None,
) -> LogRecord:
    """Build a LogRecord through the production constructor (tests never hand-forge)."""
    return build_record(
        source_kind=SourceKind.GENERIC_JSONL,
        source_name=source_name,
        line_no=line_no,
        input_text=input_text,
        output_text=output_text,
        timestamp=timestamp,
    )


# --------------------------------------------------------------- Phase 3: judge stubs

#: A fixed, valid verdict for stub judges — content is irrelevant to engine tests.
STUB_VERDICT = JudgeVerdict(
    task_type=TaskTypeLabel.FACTUAL_QUERY,
    outcome=OutcomeLabel.CORRECT,
    confidence=JudgeConfidence.HIGH,
    rationale="stub verdict — test scaffolding only",
)


def make_stub_fingerprint(*, few_shot_content_hashes: tuple[str, ...] = ()) -> JudgeFingerprint:
    """A valid stub JudgeFingerprint; hashes get matching synthetic sorted ids."""
    return JudgeFingerprint(
        judge_name="stub",
        model_id="stub-model-requested",
        taxonomy_id=TAXONOMY_V1.taxonomy_id,
        prompt_sha256=hashlib.sha256(b"stub-prompt").hexdigest(),
        few_shot_ids=tuple(f"fs-{i:016x}" for i in range(len(few_shot_content_hashes))),
        few_shot_content_hashes=tuple(sorted(few_shot_content_hashes)),
    )


class StubJudge:
    """Test-only Judge: fixed verdict, per-input scripted errors, call recording."""

    def __init__(
        self,
        *,
        verdict: JudgeVerdict = STUB_VERDICT,
        errors: dict[str, Exception] | None = None,
        model_id: str = "stub-model-served",
        few_shot_content_hashes: tuple[str, ...] = (),
    ) -> None:
        self._verdict = verdict
        self._errors = dict(errors or {})
        self._model_id = model_id
        self._fingerprint = make_stub_fingerprint(few_shot_content_hashes=few_shot_content_hashes)
        self.calls: list[tuple[str, str]] = []

    @property
    def fingerprint(self) -> JudgeFingerprint:
        return self._fingerprint

    def judge(self, input_text: str, output_text: str) -> Judgment:
        self.calls.append((input_text, output_text))
        error = self._errors.get(input_text)
        if error is not None:
            raise error
        return Judgment(verdict=self._verdict, model_id=self._model_id)


class RaisingJudge:
    """Test-only Judge that raises the given exception on every call."""

    def __init__(self, error: Exception) -> None:
        self._error = error
        self._fingerprint = make_stub_fingerprint()

    @property
    def fingerprint(self) -> JudgeFingerprint:
        return self._fingerprint

    def judge(self, input_text: str, output_text: str) -> Judgment:
        raise self._error


# ------------------------------------------------- Phase 5: export-demo artifacts


@pytest.fixture(scope="session")
def demo_export_artifacts():
    """The demo pipeline's (ExportOutcome, ExportManifest), built ONCE per session.

    The export contract / serialize / demo batteries all need a fully coherent
    manifest; rebuilding the pipeline (incl. the 10k-resample bootstrap) per test
    would dominate the suite. Deterministic except the quarantined volatile section.
    """
    from evalgen.export_demo import build_export_artifacts

    return build_export_artifacts()
