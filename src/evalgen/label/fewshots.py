"""The few-shot store loader: validated, sorted, tamper-evident, REDACTION-CLEAN
(ADR-0003 rule 8, amended after the Phase 3 red-team pass — F-1).

Every line must be a valid ``FewShotExample`` — the model's self-verifying id means a
tampered line refuses to load. Duplicate ids and duplicate content hashes refuse too:
a duplicated content hash would make the leakage gate ambiguous about WHICH example a
colliding record matched, and two ids over the same exchange with different verdicts
would be contradictory guidance.

Redaction-clean is enforced structurally, not by review: the loader takes an injected
``TextSanitizer`` (the ``Embedder``/``Judge`` seam pattern — ``label/`` never imports
``ingest``; the composition layer passes ``ingest.redaction.sanitize_text``) and
REFUSES any example whose string fields the sanitizer would rewrite. Without this
check, a PII/secret-bearing few-shot would (a) ship its secret verbatim in the system
prompt to the external judge API — bypassing the ADR-0001 ingestion boundary — and
(b) evade the labeling-time collision gate and the Phase 5 export gate, because its
``content_hash`` covers RAW text while both gates hash the record's REDACTED
``canonical_text`` (``hash(raw) != hash(redacted)`` — proven by the red team).
"""

from __future__ import annotations

from pathlib import Path

from evalgen.contracts import FewShotExample, TextSanitizer


def load_few_shots(path: Path, *, sanitizer: TextSanitizer) -> tuple[FewShotExample, ...]:
    """Load a committed few-shot JSONL; return the examples sorted by ``few_shot_id``.

    ``sanitizer`` must be the production redaction function (injected at the
    composition layer); every string field of every example must pass through it
    unchanged, or the store refuses to load — a few-shot that would be redacted has no
    business existing (its raw text would reach the API and its hash would miss the
    leakage gates). Blank lines are ignored; an empty file yields ``()`` (zero-shot is
    legal). Any invalid line raises with its line number — a partially-loaded store is
    worse than a refused one.
    """
    shots: list[FewShotExample] = []
    seen_ids: set[str] = set()
    seen_hashes: set[str] = set()
    text = path.read_text(encoding="utf-8")
    for line_no, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            shot = FewShotExample.model_validate_json(line)
        except ValueError as exc:
            raise ValueError(
                f"{path.name} line {line_no}: invalid few-shot example — {exc}"
            ) from exc
        for field_name, value in (
            ("input_text", shot.input_text),
            ("output_text", shot.output_text),
            ("verdict.rationale", shot.verdict.rationale),
            ("note", shot.note),
        ):
            cleaned = sanitizer(value)
            if cleaned != value:
                # The RAW value is deliberately not echoed — an exception message is
                # a leak channel too; the sanitized form identifies the line safely.
                raise ValueError(
                    f"{path.name} line {line_no}: {field_name} is not redaction-clean — "
                    f"the sanitizer would rewrite it to {cleaned!r}. A redactable "
                    "few-shot would ship verbatim to the judge API and its raw content "
                    "hash would evade the collision/export gates, which hash REDACTED "
                    "record text (ADR-0003 rule 8 amendment)"
                )
        if shot.few_shot_id in seen_ids:
            raise ValueError(
                f"{path.name} line {line_no}: duplicate few_shot_id {shot.few_shot_id!r}"
            )
        content_hash = shot.content_hash
        if content_hash in seen_hashes:
            raise ValueError(
                f"{path.name} line {line_no}: duplicate content hash — two few-shots over "
                "the same exchange would make the leakage gate ambiguous"
            )
        seen_ids.add(shot.few_shot_id)
        seen_hashes.add(content_hash)
        shots.append(shot)
    return tuple(sorted(shots, key=lambda s: s.few_shot_id))
