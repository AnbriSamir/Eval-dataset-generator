"""Offline agreement demo: the Phase 4 machinery proof behind ``make agreement``
(ADR-0004 options §7).

Zero arguments, zero network, byte-identical output every run — pinned by
``tests/golden/agreement_output.txt`` and a double-run test (ADR-0002 rule 9
discipline). Composition layer and sibling of ``demo.py``: imports the pipeline +
``validate``; nothing imports it. It re-runs the fixture pipeline exactly as the
demo does (determinism guarantees the identical ``LabelingOutcome`` — the wiring is
deliberately duplicated: composition layers may repeat wiring, they own no logic,
and ``demo.py``'s bytes and golden stay untouched this phase), joins the FakeJudge's
labels against the committed SYNTHETIC annotation fixture, and renders the report.

The banner is mandatory: the fixture's labels are hand-crafted against hash-derived
FakeJudge verdicts, so the κ printed here is NOISE BY CONSTRUCTION — machinery
proof, never a finding, never quoted in the README. The real number waits for
``data/labels/human_labels.jsonl`` (hook-protected, human-written) via the Phase 5
CLI behind an explicit flag — never autodetection.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from evalgen.cluster import HashingEmbedder, cluster_records, stratified_sample
from evalgen.config import get_settings
from evalgen.contracts import TAXONOMY_V2
from evalgen.dedup import run_dedup
from evalgen.ingest import GenericMapping, load_generic_jsonl, load_tracespan_jsonl, sanitize_text
from evalgen.label import FakeJudge, load_few_shots, run_labeling
from evalgen.validate import compute_agreement, load_human_labels, render_agreement_report

#: Same source-checkout resolution as ``demo.py`` (dev tool by declaration).
_FIXTURES_DIR = Path(__file__).resolve().parents[2] / "data" / "fixtures"
_FEWSHOTS_PATH = Path(__file__).resolve().parents[2] / "data" / "fewshots" / "judge_v1.jsonl"
_ANNOTATIONS_NAME = "annotations_synthetic.jsonl"

#: Identical to ``demo.py``'s mapping — the pipelines must see the same records.
_GENERIC_MAPPING = GenericMapping(
    input_key="q",
    output_key="a",
    timestamp_key="meta.ts",
    id_key="meta.id",
    task_key="meta.conv",
    metadata_keys=("channel", "lang"),
)

_BANNER = (
    "!! SYNTHETIC — annotations_synthetic.jsonl + FakeJudge: machinery proof, NOT a\n"
    "!! measured kappa. The real number waits for data/labels/human_labels.jsonl."
)


def run_agreement_demo() -> str:
    """Run pipeline + agreement on the committed fixtures; return the report text.

    Pure given the fixture files — every knob from ``get_settings()``, every stage
    deterministic (seeded sampling, hash-derived FakeJudge, seeded bootstrap), so
    two calls return byte-identical strings.
    """
    generic_records, _ = load_generic_jsonl(
        _FIXTURES_DIR / "generic_demo.jsonl", mapping=_GENERIC_MAPPING
    )
    cluster_fixture_records, _ = load_generic_jsonl(
        _FIXTURES_DIR / "cluster_demo.jsonl", mapping=_GENERIC_MAPPING
    )
    span_records, _ = load_tracespan_jsonl(_FIXTURES_DIR / "tracespans_demo.jsonl")
    records = generic_records + cluster_fixture_records + span_records

    settings = get_settings()
    embedder = HashingEmbedder(dim=settings.hash_embedding_dim)
    outcome = run_dedup(records, embedder=embedder, threshold=settings.near_dup_threshold)
    clustering = cluster_records(
        outcome.kept, embedder=embedder, min_cluster_size=settings.min_cluster_size
    )
    sampling = stratified_sample(clustering, sample_size=settings.sample_size, seed=settings.seed)

    by_id = {r.record_id: r for r in outcome.kept}
    sampled_ids = [rid for stratum in sampling.strata for rid in stratum.sampled_record_ids]
    sampled_records = [by_id[rid] for rid in sampled_ids]
    judge = FakeJudge(
        taxonomy=TAXONOMY_V2, few_shots=load_few_shots(_FEWSHOTS_PATH, sanitizer=sanitize_text)
    )
    labeling = run_labeling(sampled_records, judge=judge, max_labels=settings.max_labels_per_run)

    annotations_path = _FIXTURES_DIR / _ANNOTATIONS_NAME
    labels = load_human_labels(annotations_path)
    report = compute_agreement(
        labeling,
        labels,
        human_labels_source=_ANNOTATIONS_NAME,
        # The composition layer binds the report to the EXACT ground-truth bytes
        # (ADR-0004 amendment, red-team M-1); .gitattributes forces LF on *.jsonl,
        # so the digest is platform-stable and golden-pinned.
        human_labels_sha256=hashlib.sha256(annotations_path.read_bytes()).hexdigest(),
        min_human_labels=settings.min_human_labels,
        min_class_support=settings.min_class_support,
        bootstrap_resamples=settings.bootstrap_resamples,
        seed=settings.seed,
    )
    return _BANNER + "\n\n" + render_agreement_report(report)


def main() -> int:
    print(run_agreement_demo(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
