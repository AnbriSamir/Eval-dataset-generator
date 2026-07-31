"""The threshold-measurement protocol (ADR-0002 rule 4): sweep labeled pairs, argmax F1.

Threshold lifecycle: ``Settings.near_dup_threshold`` stays 0.92 (a default region
bracketed by the measured gap — hardest distinct pair 0.8647, easiest true duplicate
0.9321) until this protocol runs on REAL labeled pairs; the commit that changes it must
cite its ``ThresholdCalibrationReport``. ``meta.json`` (Phase 5) records the active
threshold + embedder fingerprint on every export, so every number names the embedder
that measured it.

Module-boundary note: ``main()`` imports ``cluster.embeddings`` and ``config`` INSIDE
the function — the ONE sanctioned exception to "dedup imports only contracts", confined
to the CLI composition path (the boundary test greps module-top imports only). Library
code (``calibrate_threshold``) takes the embedder injected, like everything else.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from evalgen.contracts.calibration import (
    LabeledPair,
    ThresholdCalibrationReport,
    ThresholdCandidate,
)
from evalgen.contracts.dedup import SIMILARITY_DECIMALS
from evalgen.contracts.embeddings import Embedder

_DEFAULT_FIXTURE = Path("data") / "fixtures" / "neardup_pairs.jsonl"


def load_labeled_pairs(path: str | Path) -> tuple[LabeledPair, ...]:
    """Strict loader for the calibration fixture: every line must validate.

    This is a committed fixture, not hostile input — a bad line is a repo bug, not a
    data condition, so the loader raises on the first invalid line WITH its line
    number instead of report-bucketing it.
    """
    pairs: list[LabeledPair] = []
    text = Path(path).read_text(encoding="utf-8")
    for line_no, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            pairs.append(LabeledPair.model_validate_json(line))
        except ValidationError as exc:
            raise ValueError(f"invalid LabeledPair at {Path(path).name}:{line_no} — {exc}") from exc
    return tuple(pairs)


def calibrate_threshold(
    pairs: Sequence[LabeledPair], *, embedder: Embedder
) -> ThresholdCalibrationReport:
    """Run the sweep: pair cosines → midpoint candidates → duplicate-class P/R/F1.

    Candidates are the midpoints between adjacent DISTINCT values of the sorted
    similarity list (the standard ROC sweep — no arbitrary grid), rounded to report
    precision and de-duplicated BEFORE scoring: two midpoints closer than 1e-6 encode
    the same decision boundary at report precision, and metrics are computed against
    the rounded threshold itself — every report row is exactly reproducible by
    replaying ``sim >= threshold`` with the printed value. Chosen = max F1, ties to
    the HIGHEST threshold (prefer keeping data).

    Raises ``ValueError`` (naming the actual problem) when the pairs yield fewer than
    two distinct similarity values — there is no boundary to sweep between fewer than
    two points, and the report model requires at least one candidate.
    """
    embeddings_a = embedder.embed([p.text_a for p in pairs])
    embeddings_b = embedder.embed([p.text_b for p in pairs])
    # Embedder contract: unit rows — the row dot IS the cosine.
    similarities = [float((embeddings_a[i] * embeddings_b[i]).sum()) for i in range(len(pairs))]
    labels = [p.label == "duplicate" for p in pairs]

    distinct_values = sorted(set(similarities))
    if len(distinct_values) < 2:
        raise ValueError(
            f"calibration needs at least two distinct similarity values, got "
            f"{len(distinct_values)} from {len(pairs)} pair(s) — every pair scored "
            "identically under this embedder, so there is no boundary to sweep"
        )
    candidates_raw = sorted(
        {
            round((distinct_values[i] + distinct_values[i + 1]) / 2.0, SIMILARITY_DECIMALS)
            for i in range(len(distinct_values) - 1)
        }
    )

    candidates: list[ThresholdCandidate] = []
    scored = list(zip(similarities, labels, strict=True))
    for candidate in candidates_raw:
        tp = sum(1 for sim, dup in scored if dup and sim >= candidate)
        fp = sum(1 for sim, dup in scored if not dup and sim >= candidate)
        fn = sum(1 for sim, dup in scored if dup and sim < candidate)
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        candidates.append(
            ThresholdCandidate(
                threshold=candidate,
                precision=round(precision, SIMILARITY_DECIMALS),
                recall=round(recall, SIMILARITY_DECIMALS),
                f1=round(f1, SIMILARITY_DECIMALS),
            )
        )

    best = max(candidates, key=lambda c: (c.f1, c.threshold))
    return ThresholdCalibrationReport(
        embedder=embedder.fingerprint,
        pairs_duplicate=sum(labels),
        pairs_distinct=len(labels) - sum(labels),
        candidates=tuple(candidates),
        chosen_threshold=best.threshold,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """``python -m evalgen.dedup.calibrate [path]`` — offline, deterministic, exit 0."""
    # Composition-layer imports (see module docstring) — deliberately not module-top.
    from evalgen.cluster.embeddings import HashingEmbedder
    from evalgen.config import get_settings

    args = list(sys.argv[1:] if argv is None else argv)
    path = Path(args[0]) if args else _DEFAULT_FIXTURE
    embedder = HashingEmbedder(dim=get_settings().hash_embedding_dim)
    report = calibrate_threshold(load_labeled_pairs(path), embedder=embedder)
    print(report.model_dump_json(indent=2))
    print(
        f"chosen_threshold={report.chosen_threshold} over "
        f"{report.pairs_duplicate} duplicate + {report.pairs_distinct} distinct pairs "
        f"({len(report.candidates)} candidates swept)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
