"""End-to-end offline demo: ingest → dedup → cluster → stratified sample → label the
sample on the committed fixtures (ADR-0002 rule 9, extended by ADR-0003 rule 10).

Zero arguments (arguments are variance; the demo's job is to be identical every time),
zero network, byte-identical output every run — pinned by ``tests/golden/demo_output.txt``
and a double-run test. Composition layer: imports ``config``/``ingest``/``dedup``/
``cluster``/``label``; nothing imports ``demo``. The judge is the deterministic
``FakeJudge`` — its verdicts are hash-derived NOISE, marked as synthetic in the output,
and the README must never quote them as findings.

Output discipline: post-redaction record text only, truncated previews, no timestamps,
no absolute paths (basenames only — paths are PII per ADR-0001), no floats beyond the
rounded similarities.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from evalgen.cluster import HashingEmbedder, cluster_records, stratified_sample
from evalgen.config import get_settings
from evalgen.contracts import (
    NOISE_CLUSTER_ID,
    TAXONOMY_V2,
    ClusteringReport,
    DedupOutcome,
    IngestReport,
    LabelingOutcome,
    LogRecord,
    OutcomeLabel,
    SamplingReport,
    TaskTypeLabel,
    record_sort_key,
)
from evalgen.dedup import run_dedup
from evalgen.ingest import GenericMapping, load_generic_jsonl, load_tracespan_jsonl, sanitize_text
from evalgen.label import FakeJudge, load_few_shots, run_labeling

#: Repo root is two levels up from src/evalgen/demo.py; computed once, never printed.
#: Assumes a SOURCE CHECKOUT (dev tool by declaration — `make demo` runs from the
#: repo); installed as a wheel this would not resolve, and that is fine for v1.
_FIXTURES_DIR = Path(__file__).resolve().parents[2] / "data" / "fixtures"
_FEWSHOTS_PATH = Path(__file__).resolve().parents[2] / "data" / "fewshots" / "judge_v1.jsonl"

_GENERIC_MAPPING = GenericMapping(
    input_key="q",
    output_key="a",
    timestamp_key="meta.ts",
    id_key="meta.id",
    task_key="meta.conv",
    metadata_keys=("channel", "lang"),
)

_PREVIEW_LEN = 64
_WRAP_COL = 90
_NAME_COL = 22  # ingest source-name column
_STRATUM_COL = 15  # "cl-" + 12 hex, and "noise" padded to it


def _preview(text: str) -> str:
    """Truncate to the preview budget; newlines become a visible glyph."""
    flat = text.replace("\n", "␤")
    return flat if len(flat) <= _PREVIEW_LEN else flat[: _PREVIEW_LEN - 1] + "…"


def _wrap_ids(prefix: str, ids: tuple[str, ...]) -> list[str]:
    """Wrap 'prefix id, id, id' deterministically at ~_WRAP_COL columns."""
    if not ids:
        return [prefix.rstrip()]
    indent = " " * len(prefix)
    lines: list[str] = []
    current = prefix + ids[0] + ("," if len(ids) > 1 else "")
    for i, record_id in enumerate(ids[1:], start=1):
        token = record_id + ("," if i < len(ids) - 1 else "")
        if len(current) + 1 + len(token) > _WRAP_COL:
            lines.append(current)
            current = indent + token
        else:
            current = current + " " + token
    lines.append(current)
    return lines


def _render_ingest(reports: tuple[IngestReport, ...], total: int) -> list[str]:
    lines = ["[1/5] ingest"]
    lines.extend(
        f"  {report.source_name:<{_NAME_COL}} {report.lines_read} read | "
        f"{report.records_normalized} records | {report.lines_rejected} rejected | "
        f"{report.lines_skipped} skipped"
        for report in reports
    )
    lines.append(f"  total records: {total}")
    return lines


def _render_dedup(outcome: DedupOutcome) -> list[str]:
    report = outcome.report
    fp = report.embedder
    lines = [
        f"[2/5] dedup   threshold={report.threshold:g}  embedder={fp.name} "
        f"dim={fp.dim} {fp.analyzer}({fp.ngram_min},{fp.ngram_max})",
        f"  in={report.records_in}  out={report.records_out}  "
        f"id_collapsed={report.id_collapsed}  exact={report.exact_dropped}  "
        f"near={report.near_dropped} (via_chain={report.near_dropped_via_chain})",
    ]
    lines.extend(
        f"  exact  {e.dropped_record_id} -> kept {e.kept_record_id}" for e in report.exact_entries
    )
    lines.extend(
        f"  near   {e.dropped_record_id} -> kept {e.kept_record_id}  "
        f"sim={e.similarity!r}" + ("  [chain]" if e.via_chain else "")
        for e in report.near_entries
    )
    return lines


def _render_cluster(clustering: ClusteringReport, by_id: dict[str, LogRecord]) -> list[str]:
    lines = [
        f"[3/5] cluster   min_cluster_size={clustering.min_cluster_size}  "
        f"metric={clustering.metric}"
    ]
    for cluster in clustering.clusters:
        head = min(cluster.record_ids, key=lambda rid: record_sort_key(by_id[rid]))
        lines.append(
            f"  {cluster.cluster_id:<{_STRATUM_COL}}  size={cluster.size}  "
            f'"{_preview(by_id[head].cluster_text)}"'
        )
    lines.append(f"  {NOISE_CLUSTER_ID:<{_STRATUM_COL}}  size={len(clustering.noise_record_ids)}")
    return lines


def _render_sample(sampling: SamplingReport) -> list[str]:
    lines = [
        f"[4/5] sample   seed={sampling.seed}  requested={sampling.sample_size_requested}"
        f"  sampled={sampling.total_sampled}"
    ]
    for stratum in sampling.strata:
        prefix = (
            f"  {stratum.cluster_id:<{_STRATUM_COL}}  "
            f"quota {stratum.quota}/{stratum.stratum_size}   "
        )
        lines.extend(_wrap_ids(prefix, stratum.sampled_record_ids))
    return lines


def _render_label(labeling: LabelingOutcome) -> list[str]:
    report = labeling.report
    fp = report.judge
    lines = [
        f"[5/5] label   judge={fp.judge_name} model={fp.model_id}  "
        f"taxonomy={fp.taxonomy_id}  prompt={fp.prompt_sha256[:12]}",
        f"  in={report.records_in}  labeled={report.labeled}  refused={report.refused}  "
        f"failed={report.failed}  budget_skipped={report.skipped_budget}  "
        f"fewshot_collisions={report.skipped_fewshot_collision}  (budget={report.max_labels})",
    ]
    lines.extend(
        f"  collision  {record_id}  (canonical text matches a committed few-shot — "
        "never labeled, never exportable)"
        for record_id in report.fewshot_collision_record_ids
    )
    # Distributions over labeled examples, enum declaration order. The marker is
    # mandatory: FakeJudge verdicts are hash-derived noise, never findings.
    outcome_counts = Counter(e.verdict.outcome for e in labeling.labeled_examples)
    task_counts = Counter(e.verdict.task_type for e in labeling.labeled_examples)
    lines.append(
        "  outcome    "
        + "  ".join(f"{member.value}={outcome_counts[member]}" for member in OutcomeLabel)
        + "   [synthetic fake-judge verdicts]"
    )
    lines.append(
        "  task_type  "
        + "  ".join(f"{member.value}={task_counts[member]}" for member in TaskTypeLabel)
    )
    return lines


def run_demo() -> str:
    """Run the full pipeline on the fixed fixture list; return the report text.

    Pure given the fixture files — every knob from ``get_settings()``, every stage
    deterministic, so two calls return byte-identical strings.
    """
    generic_records, generic_report = load_generic_jsonl(
        _FIXTURES_DIR / "generic_demo.jsonl", mapping=_GENERIC_MAPPING
    )
    cluster_fixture_records, cluster_fixture_report = load_generic_jsonl(
        _FIXTURES_DIR / "cluster_demo.jsonl", mapping=_GENERIC_MAPPING
    )
    span_records, span_report = load_tracespan_jsonl(_FIXTURES_DIR / "tracespans_demo.jsonl")
    records = generic_records + cluster_fixture_records + span_records

    settings = get_settings()
    embedder = HashingEmbedder(dim=settings.hash_embedding_dim)
    outcome = run_dedup(records, embedder=embedder, threshold=settings.near_dup_threshold)
    clustering = cluster_records(
        outcome.kept, embedder=embedder, min_cluster_size=settings.min_cluster_size
    )
    sampling = stratified_sample(clustering, sample_size=settings.sample_size, seed=settings.seed)

    by_id = {r.record_id: r for r in outcome.kept}
    # Label the SAMPLE — the production shape (the sample is what gets labeled and
    # exported). FakeJudge: offline by declaration, injected at this composition layer
    # (ADR-0003 rule 3 — no factory, no env switch). The few-shot loader gets the
    # PRODUCTION sanitizer: a redactable few-shot refuses to load (rule 8 amendment).
    sampled_ids = [rid for stratum in sampling.strata for rid in stratum.sampled_record_ids]
    sampled_records = [by_id[rid] for rid in sampled_ids]
    judge = FakeJudge(
        taxonomy=TAXONOMY_V2, few_shots=load_few_shots(_FEWSHOTS_PATH, sanitizer=sanitize_text)
    )
    labeling = run_labeling(sampled_records, judge=judge, max_labels=settings.max_labels_per_run)

    title = "evalgen demo — offline pipeline on committed fixtures"
    sections = [
        [title, "=" * len(title)],
        _render_ingest((generic_report, cluster_fixture_report, span_report), len(records)),
        _render_dedup(outcome),
        _render_cluster(clustering, by_id),
        _render_sample(sampling),
        _render_label(labeling),
    ]
    return "\n\n".join("\n".join(section) for section in sections) + "\n"


def main() -> int:
    print(run_demo(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
