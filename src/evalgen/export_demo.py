"""Offline export demo: the Phase 5 machinery proof behind ``make export``
(ADR-0005 options §5).

Zero arguments, zero network; the TEXT output is byte-identical every run — pinned
by ``tests/golden/export_output.txt`` plus the dataset/meta goldens (the report's
two sha256 lines transitively pin all three artifacts). Composition layer and
sibling of ``agreement_demo.py``: imports the pipeline + validate + export; nothing
imports it. The wiring is deliberately duplicated (composition layers repeat
wiring, they own no logic; the demo and agreement goldens stay byte-untouched).

On the committed fixtures the gate GENUINELY BLOCKS (headline κ = 0.565581 < 0.6)
— so this demo exports via the explicit, loudly-rendered override. That IS the
machinery proof: the committed golden shows the real failing check, the override
shouting its reason, and the honest low κ on the export's face. The banner is
mandatory: FakeJudge verdicts are hash-derived noise; nothing here is a shippable
dataset. The real export waits for ``data/labels/human_labels.jsonl`` and a κ that
clears the gate, wired by the future CLI behind explicit flags.

Volatility discipline: this module is the ONE place that reads the clock, git and
the platform (``_collect_volatile``) — every function under ``export/`` stays pure;
the volatile values land in meta.json's quarantined section and are never rendered.
"""

from __future__ import annotations

import hashlib
import platform
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from evalgen import __version__
from evalgen.cluster import HashingEmbedder, cluster_records, stratified_sample
from evalgen.config import get_settings
from evalgen.contracts import (
    TAXONOMY_V2,
    ExportGateOverride,
    ExportManifest,
    ExportOutcome,
    InputFileDigest,
    InputFileRole,
    SettingsSnapshot,
    VolatileProvenance,
)
from evalgen.dedup import run_dedup
from evalgen.export import (
    assemble_export,
    evaluate_export_gate,
    render_export_report,
    render_golden_jsonl,
    render_meta_json,
    sha256_hex,
    write_export,
)
from evalgen.ingest import GenericMapping, load_generic_jsonl, load_tracespan_jsonl, sanitize_text
from evalgen.label import FakeJudge, load_few_shots, run_labeling
from evalgen.validate import compute_agreement, load_human_labels

#: Same source-checkout resolution as ``demo.py`` (dev tool by declaration).
_REPO_ROOT = Path(__file__).resolve().parents[2]
_FIXTURES_DIR = _REPO_ROOT / "data" / "fixtures"
_FEWSHOTS_PATH = _REPO_ROOT / "data" / "fewshots" / "judge_v1.jsonl"
_ANNOTATIONS_NAME = "annotations_synthetic.jsonl"
#: Gitignored runtime output dir — the sanctioned write target (ADR-0005 context).
_OUT_DIR = _REPO_ROOT / "data" / "out"

#: Identical to ``demo.py``'s mapping — the pipelines must see the same records.
_GENERIC_MAPPING = GenericMapping(
    input_key="q",
    output_key="a",
    timestamp_key="meta.ts",
    id_key="meta.id",
    task_key="meta.conv",
    metadata_keys=("channel", "lang"),
)

_OVERRIDE = ExportGateOverride(
    reason=(
        "synthetic machinery proof: FakeJudge verdicts are hash-derived noise, the "
        "gate correctly blocks kappa=0.565581 < 0.6; overridden deliberately to "
        "exercise the full export path offline (never a real-data precedent)"
    )
)

_BANNER = (
    "!! SYNTHETIC — annotations_synthetic.jsonl + FakeJudge + a DELIBERATE gate\n"
    "!! override: machinery proof, NOT a shippable dataset. The real export waits\n"
    "!! for data/labels/human_labels.jsonl and a kappa that clears the gate."
)


def _collect_volatile() -> VolatileProvenance:
    """Composition-only: the sole clock/git/platform reads of the export path."""
    git_commit: str | None
    try:
        result = subprocess.run(  # noqa: S603 — fixed argv, no shell
            ["git", "rev-parse", "HEAD"],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        git_commit = result.stdout.strip() if result.returncode == 0 else None
        if git_commit == "":
            git_commit = None
    except OSError:
        git_commit = None  # no git available — recorded as unrecorded, never a crash
    return VolatileProvenance(
        git_commit=git_commit,
        generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
        environment={
            "evalgen": __version__,
            "platform": platform.system().lower(),
            "python": platform.python_version(),
        },
    )


def build_export_artifacts() -> tuple[ExportOutcome, ExportManifest]:
    """Re-run the fixture pipeline, gate it, assemble, and build the manifest.

    Deterministic except the manifest's quarantined ``volatile`` section — the
    golden.jsonl content and meta.json's deterministic section are byte-identical
    across runs (pinned by the Phase 5 goldens).
    """
    generic_path = _FIXTURES_DIR / "generic_demo.jsonl"
    cluster_path = _FIXTURES_DIR / "cluster_demo.jsonl"
    spans_path = _FIXTURES_DIR / "tracespans_demo.jsonl"
    annotations_path = _FIXTURES_DIR / _ANNOTATIONS_NAME

    generic_records, generic_report = load_generic_jsonl(generic_path, mapping=_GENERIC_MAPPING)
    cluster_fixture_records, cluster_fixture_report = load_generic_jsonl(
        cluster_path, mapping=_GENERIC_MAPPING
    )
    span_records, span_report = load_tracespan_jsonl(spans_path)
    records = generic_records + cluster_fixture_records + span_records

    settings = get_settings()
    embedder = HashingEmbedder(dim=settings.hash_embedding_dim)
    dedup_outcome = run_dedup(records, embedder=embedder, threshold=settings.near_dup_threshold)
    clustering = cluster_records(
        dedup_outcome.kept, embedder=embedder, min_cluster_size=settings.min_cluster_size
    )
    sampling = stratified_sample(clustering, sample_size=settings.sample_size, seed=settings.seed)

    by_id = {r.record_id: r for r in dedup_outcome.kept}
    sampled_ids = [rid for stratum in sampling.strata for rid in stratum.sampled_record_ids]
    sampled_records = [by_id[rid] for rid in sampled_ids]
    few_shots = load_few_shots(_FEWSHOTS_PATH, sanitizer=sanitize_text)
    judge = FakeJudge(taxonomy=TAXONOMY_V2, few_shots=few_shots)
    labeling = run_labeling(sampled_records, judge=judge, max_labels=settings.max_labels_per_run)

    # Composition-level drift assertion (ADR-0005 options §3): the store just loaded
    # must be byte-for-byte the store the judge saw. Structurally guaranteed here
    # (same objects), so this is documentation-by-assertion — a future wiring change
    # that lets the two diverge fails loudly at this seam, not silently at the gate.
    loaded_hashes = tuple(sorted(shot.content_hash for shot in few_shots))
    assert loaded_hashes == judge.fingerprint.few_shot_content_hashes, (
        "few-shot store drifted from the judge fingerprint — the store on disk is not "
        "the store the judge saw"
    )

    labels = load_human_labels(annotations_path)
    agreement = compute_agreement(
        labeling,
        labels,
        human_labels_source=_ANNOTATIONS_NAME,
        # Bind the report to the EXACT ground-truth bytes (ADR-0004 amendment M-1);
        # the manifest validator refuses any divergence from this digest.
        human_labels_sha256=hashlib.sha256(annotations_path.read_bytes()).hexdigest(),
        min_human_labels=settings.min_human_labels,
        min_class_support=settings.min_class_support,
        bootstrap_resamples=settings.bootstrap_resamples,
        seed=settings.seed,
    )

    decision = evaluate_export_gate(
        agreement,
        labeling.report,
        min_export_kappa=settings.min_export_kappa,
        override=_OVERRIDE,
    )
    outcome = assemble_export(dedup_outcome.kept, labeling, clustering, decision)

    input_files = tuple(
        sorted(
            (
                InputFileDigest(
                    name=path.name,
                    sha256=sha256_hex(path.read_bytes()),
                    role=role,
                )
                for path, role in (
                    (generic_path, InputFileRole.SOURCE_LOG),
                    (cluster_path, InputFileRole.SOURCE_LOG),
                    (spans_path, InputFileRole.SOURCE_LOG),
                    (_FEWSHOTS_PATH, InputFileRole.FEW_SHOT_STORE),
                    (annotations_path, InputFileRole.HUMAN_LABELS),
                )
            ),
            key=lambda digest: (digest.role.value, digest.name),
        )
    )
    golden_text = render_golden_jsonl(outcome)
    manifest = ExportManifest(
        settings=SettingsSnapshot(**settings.model_dump()),
        input_files=input_files,
        embedder=embedder.fingerprint,
        ingest=(generic_report, cluster_fixture_report, span_report),
        dedup=dedup_outcome.report,
        clustering=clustering,
        sampling=sampling,
        labeling=labeling.report,
        agreement=agreement,
        export=outcome.report,
        golden_jsonl_sha256=sha256_hex(golden_text),
        volatile=_collect_volatile(),
    )
    return outcome, manifest


def run_export_demo(out_dir: Path | None = None) -> str:
    """Build the artifacts; write them only when ``out_dir`` is given; return the
    banner + deterministic text report (golden-pinned)."""
    outcome, manifest = build_export_artifacts()
    if out_dir is not None:
        write_export(
            out_dir,
            golden_text=render_golden_jsonl(outcome),
            meta_text=render_meta_json(manifest),
        )
    return _BANNER + "\n\n" + render_export_report(outcome, manifest)


def main() -> int:
    # The report contains characters outside legacy console codepages ("∩", "∅");
    # a cp1252 stdout would UnicodeEncodeError *after* the artifacts are written.
    # Reconfigure here at the CLI boundary — the rendered string and the on-disk
    # artifacts (always UTF-8/LF via writer.py) are unaffected.
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    print(run_export_demo(_OUT_DIR), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
