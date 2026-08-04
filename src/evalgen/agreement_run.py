"""The real-data agreement CLI — EXPLICIT FLAGS ONLY, never autodetection
(README roadmap; ADR-0004 options §7: "a target that silently switches data
sources is invisible variance").

``python -m evalgen.agreement_run --labels PATH --judge {fake,anthropic}
[--model ID] [--out DIR]`` re-runs the committed fixture pipeline (ingest ->
dedup -> cluster -> sample — the wiring is deliberately duplicated from the demo
composition layers: they may repeat wiring, they own no logic), labels the sample
with the judge the CALLER chose, joins against the ``--labels`` file through the
strict Phase 4 loader, computes the full ``AgreementReport`` bound to the exact
ground-truth bytes (``human_labels_sha256`` — the ``agreement_demo.py`` pattern,
ADR-0004 amendment M-1), renders it, and writes the JSON run report under
``--out`` (basenames deliberately OUTSIDE the protect hook's namespace: run
outputs, not ground truth).

Honesty of the banner (ADR-0004 amendment 2026-08-04, red-team F-2): the
``!! SYNTHETIC`` banner prints IFF ANY independent trigger fires — the judge is
fake; ``--labels`` is byte-identical (sha256) to the committed synthetic fixture;
``--labels`` carries the fixture's LABEL CONTENT under any re-encoding (canonical
sorted ``(record_id, task_type, outcome)`` tuples — CRLF, trailing newlines,
field order, note edits and annotator renames cannot shed the banner); or the
fixture's pinned ``annotator == "synthetic"`` marker is present. Every reason is
printed AND recorded in the JSON run report (``synthetic_reasons``). Otherwise
the header states the real source, the real annotators, the judge and model, and
that a live-LLM run is NOT byte-deterministic (every number still binds to its
exact inputs through the sha256 digests it travels with).

Audit trail (ADR-0004 amendment 2026-08-04, red-team F-3): a real-judge run is
not byte-deterministic, so its report is written to a PER-RUN basename
(``agreement_run_report.<run_id>.json``) with a quarantined ``volatile`` stamp
(run id + UTC timestamp — the ``export_demo`` volatility discipline). Re-rolled
runs accumulate side by side instead of silently replacing each other:
cherry-picking a lucky kappa stays possible but becomes VISIBLE. The fake path
keeps the fixed basename and ``volatile: null`` — byte-deterministic as before.

Directory separation (ADR-0004 amendment 2026-08-04, red-team F-1): ``--out``
receives judge verdicts, so this CLI REFUSES an ``--out`` that contains the
human-facing annotation artifacts (the mirror of ``annotation_cli``'s guard) —
verdicts and the blank template never share a directory.

Cost is bounded and stated: ``max_labels_per_run`` binds in the engine exactly as
on the fake path, and the number of planned judge calls is printed BEFORE any call
is made. ``AnthropicJudge`` is reached only by the explicit deep import inside the
``--judge anthropic`` branch (ADR-0003 rule 4) — the fake path never loads the SDK.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from evalgen.cluster import HashingEmbedder, cluster_records, stratified_sample
from evalgen.config import Settings, get_settings
from evalgen.contracts import TAXONOMY_V1, FewShotExample, HumanLabel, Judge, LogRecord
from evalgen.dedup import run_dedup
from evalgen.ingest import GenericMapping, load_generic_jsonl, load_tracespan_jsonl, sanitize_text
from evalgen.label import FakeJudge, load_few_shots, run_labeling
from evalgen.validate import compute_agreement, load_human_labels, render_agreement_report

#: Same source-checkout resolution as ``demo.py`` (dev tool by declaration).
_REPO_ROOT = Path(__file__).resolve().parents[2]
_FIXTURES_DIR = _REPO_ROOT / "data" / "fixtures"
_FEWSHOTS_PATH = _REPO_ROOT / "data" / "fewshots" / "judge_v1.jsonl"
_SYNTHETIC_FIXTURE = _FIXTURES_DIR / "annotations_synthetic.jsonl"
#: Gitignored runtime output dir — the sanctioned write target (ADR-0005 context).
_DEFAULT_OUT_DIR = _REPO_ROOT / "data" / "out"

REPORT_BASENAME = "agreement_run_report.json"
REPORT_SCHEMA = "evalgen.agreement_run_report.v1"

#: The synthetic fixture's pinned annotator marker (ADR-0004 options §7).
SYNTHETIC_ANNOTATOR = "synthetic"

#: Human-facing annotation artifacts that must NEVER share a directory with the
#: judge's outputs (red-team F-1 — the mirror of ``annotation_cli``'s guard).
_ANNOTATION_BASENAMES = ("annotation_template.jsonl", "annotation_instructions.txt")

#: Identical to ``demo.py``'s mapping — the pipelines must see the same records.
_GENERIC_MAPPING = GenericMapping(
    input_key="q",
    output_key="a",
    timestamp_key="meta.ts",
    id_key="meta.id",
    task_key="meta.conv",
    metadata_keys=("channel", "lang"),
)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m evalgen.agreement_run",
        description=(
            "Compute the judge-vs-human agreement (Cohen's kappa + CI95) on the "
            "committed pipeline with an EXPLICITLY chosen judge and label file. "
            "No flag has an autodetected value: what you did not ask for does not run."
        ),
    )
    parser.add_argument(
        "--labels",
        type=Path,
        required=True,
        help="path to the filled human-label JSONL (strict loader; REQUIRED, no default)",
    )
    parser.add_argument(
        "--judge",
        choices=("fake", "anthropic"),
        required=True,
        help="judge implementation (REQUIRED): 'anthropic' calls the real API and costs money",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="judge model id for --judge anthropic (default: config judge_model)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help=f"directory for {REPORT_BASENAME} (default: data/out/)",
    )
    args = parser.parse_args(argv)
    if args.judge == "fake" and args.model is not None:
        parser.error(
            "--model applies only to --judge anthropic (the FakeJudge has a fixed model id)"
        )
    return args


def _run_pipeline(settings: Settings) -> list[LogRecord]:
    """The committed fixture pipeline up to the sampled records (demo wiring)."""
    generic_records, _ = load_generic_jsonl(
        _FIXTURES_DIR / "generic_demo.jsonl", mapping=_GENERIC_MAPPING
    )
    cluster_fixture_records, _ = load_generic_jsonl(
        _FIXTURES_DIR / "cluster_demo.jsonl", mapping=_GENERIC_MAPPING
    )
    span_records, _ = load_tracespan_jsonl(_FIXTURES_DIR / "tracespans_demo.jsonl")
    records = generic_records + cluster_fixture_records + span_records

    embedder = HashingEmbedder(dim=settings.hash_embedding_dim)
    outcome = run_dedup(records, embedder=embedder, threshold=settings.near_dup_threshold)
    clustering = cluster_records(
        outcome.kept, embedder=embedder, min_cluster_size=settings.min_cluster_size
    )
    sampling = stratified_sample(clustering, sample_size=settings.sample_size, seed=settings.seed)
    by_id = {r.record_id: r for r in outcome.kept}
    sampled_ids = [rid for stratum in sampling.strata for rid in stratum.sampled_record_ids]
    return [by_id[rid] for rid in sampled_ids]


def _build_judge(kind: str, model: str, few_shots: tuple[FewShotExample, ...]) -> Judge:
    """Constructor injection at the composition layer — no factory, no env switch."""
    if kind == "fake":
        return FakeJudge(taxonomy=TAXONOMY_V1, few_shots=few_shots)
    # The ONE sanctioned reach into the real judge: an explicit deep import inside
    # the --judge anthropic branch (ADR-0003 rule 4 — never via label/__init__,
    # never at module top, so the fake path never loads the SDK).
    from evalgen.label.anthropic_judge import AnthropicJudge

    return AnthropicJudge(model=model, taxonomy=TAXONOMY_V1, few_shots=few_shots)


def _canonical_label_content(labels: Sequence[HumanLabel]) -> tuple[tuple[str, str, str], ...]:
    """Encoding-independent label identity (red-team F-2): the sorted
    ``(record_id, task_type, outcome)`` tuples. Re-encoding the fixture (CRLF,
    trailing newlines, field order, note edits) or renaming its annotator cannot
    change this — only actually different labels can."""
    return tuple(
        sorted((label.record_id, label.task_type.value, label.outcome.value) for label in labels)
    )


def _synthetic_reasons(
    judge_kind: str,
    *,
    bytes_match: bool,
    content_match: bool,
    annotators: tuple[str, ...],
) -> tuple[str, ...]:
    """Every INDEPENDENT synthetic trigger that fires, each named (ADR-0004
    amendment 2026-08-04: byte identity may remain one trigger, never the only
    one). Empty ⟺ the run is genuinely real-data."""
    reasons = []
    if judge_kind == "fake":
        reasons.append("judge=fake (hash-derived noise verdicts)")
    if bytes_match:
        reasons.append("--labels is byte-identical to the committed synthetic fixture")
    elif content_match:
        reasons.append(
            "--labels carries the committed synthetic fixture's labels "
            "(re-encoded bytes cannot shed the banner)"
        )
    if SYNTHETIC_ANNOTATOR in annotators:
        reasons.append("annotator 'synthetic' present (the fixture's pinned marker)")
    return tuple(reasons)


def _banner(reasons: tuple[str, ...]) -> str | None:
    """SYNTHETIC banner text, or None when no trigger fired (genuinely real-data)."""
    if not reasons:
        return None
    lines = ["!! SYNTHETIC — machinery proof, NOT a measured kappa:"]
    lines.extend(f"!!   - {reason}" for reason in reasons)
    lines.append("!! The real number needs real human labels in data/labels/human_labels.jsonl")
    lines.append("!! and --judge anthropic.")
    return "\n".join(lines)


_REAL_HEADER = (
    "!! REAL DATA — live LLM judge: this run is NOT byte-deterministic (re-runs may\n"
    "!! differ); every number below stays bound to its exact inputs by the sha256\n"
    "!! digests it travels with."
)


def _render_preflight(
    *,
    judge: Judge,
    labels_basename: str,
    labels_sha256: str,
    annotators: tuple[str, ...],
    sampled: int,
    collisions: int,
    planned_calls: int,
    max_labels: int,
    synthetic_reasons: tuple[str, ...],
) -> str:
    """Everything printed BEFORE the first judge call — banner, provenance, cost."""
    banner = _banner(synthetic_reasons)
    fingerprint = judge.fingerprint
    lines = [
        banner if banner is not None else _REAL_HEADER,
        "",
        f"run         judge={fingerprint.judge_name}  model={fingerprint.model_id}  "
        f"taxonomy={fingerprint.taxonomy_id}",
        f"labels      {labels_basename}  sha256={labels_sha256}",
        "annotators  " + ", ".join(annotators),
        f"cost        planned_judge_calls={planned_calls}  sampled={sampled}  "
        f"fewshot_collisions={collisions}  max_labels_per_run={max_labels}",
        "            (printed before any judge call; each call is one API request",
        "            when the judge is anthropic)",
        "",
    ]
    return "\n".join(lines) + "\n"


def _collect_volatile() -> dict[str, str]:
    """Composition-only clock read (the ``export_demo`` volatility discipline),
    REAL-judge runs only: a per-run identity so re-rolled runs accumulate instead
    of silently replacing each other (red-team F-3 — cherry-picking must be
    visible, never invisible). The fake path stays byte-deterministic with None."""
    now = datetime.now(UTC)
    return {
        "run_id": now.strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(4),
        "generated_at": now.isoformat(timespec="seconds"),
    }


def _annotation_artifacts_in(out_dir: Path) -> tuple[str, ...]:
    """Human-facing annotation basenames present in ``out_dir`` (sorted)."""
    if not out_dir.is_dir():
        return ()
    return tuple(sorted(name for name in _ANNOTATION_BASENAMES if (out_dir / name).exists()))


def _render_run_report_json(
    *,
    judge_kind: str,
    requested_model_id: str,
    served_model_ids: tuple[str, ...],
    synthetic: bool,
    synthetic_reasons: tuple[str, ...],
    volatile: dict[str, str] | None,
    labels_basename: str,
    labels_sha256: str,
    labeling_report: dict[str, object],
    agreement_report: dict[str, object],
) -> str:
    """The JSON run report — canonical dump (sort_keys, indent=2, trailing LF)."""
    payload = {
        "schema": REPORT_SCHEMA,
        "judge_kind": judge_kind,
        "requested_model_id": requested_model_id,
        "served_model_ids": list(served_model_ids),
        "synthetic": synthetic,
        "synthetic_reasons": list(synthetic_reasons),
        "volatile": volatile,
        "labels_file": labels_basename,
        "labels_sha256": labels_sha256,
        "labeling_report": labeling_report,
        "agreement_report": agreement_report,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _write_atomic(path: Path, text: str) -> None:
    """Bytes only, UTF-8, LF preserved; temp-then-replace (writer.py discipline)."""
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_bytes(text.encode("utf-8"))
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    tmp.replace(path)


def _canonical_hash(record: LogRecord) -> str:
    """The exact-dedup content identity — the engine's collision key (ADR-0003)."""
    return hashlib.sha256(record.canonical_text.encode("utf-8")).hexdigest()


def run(argv: Sequence[str] | None = None) -> int:
    """The full CLI flow. Returns the process exit code."""
    args = _parse_args(argv)
    settings = get_settings()
    requested_model = args.model if args.model is not None else settings.judge_model
    out_dir: Path = args.out if args.out is not None else _DEFAULT_OUT_DIR

    # F-1 mirror guard, BEFORE any work (and before any API cost): judge verdicts
    # must never land in a directory holding the blank annotation artifacts.
    annotation_artifacts = _annotation_artifacts_in(out_dir)
    if annotation_artifacts:
        sys.stderr.write(
            f"refusing --out '{out_dir}': it holds the human-facing annotation "
            f"artifact(s) {', '.join(annotation_artifacts)}. Judge verdicts must never "
            "share a directory with the blank template the human fills blind "
            "(ADR-0004 double-blind) - the template's home is data/annotation/, judge "
            "output goes to data/out/. Move the annotation artifacts (or choose "
            "another --out) and re-run.\n"
        )
        return 2

    sampled_records = _run_pipeline(settings)
    few_shots = load_few_shots(_FEWSHOTS_PATH, sanitizer=sanitize_text)
    judge = _build_judge(args.judge, requested_model, few_shots)

    # Bind the report to the EXACT ground-truth bytes (ADR-0004 amendment M-1,
    # the agreement_demo pattern): the composition layer that read the file hashes
    # it; the strict loader then validates the same file.
    labels_sha256 = hashlib.sha256(args.labels.read_bytes()).hexdigest()
    labels: tuple[HumanLabel, ...] = load_human_labels(args.labels)
    annotators = tuple(sorted({label.annotator for label in labels}))
    fixture_sha256 = hashlib.sha256(_SYNTHETIC_FIXTURE.read_bytes()).hexdigest()
    # Synthetic detection is content-based, never bytes-only (red-team F-2): a
    # re-encoded fixture carries the same labels and must wear the same banner.
    synthetic_reasons = _synthetic_reasons(
        args.judge,
        bytes_match=labels_sha256 == fixture_sha256,
        content_match=(
            _canonical_label_content(labels)
            == _canonical_label_content(load_human_labels(_SYNTHETIC_FIXTURE))
        ),
        annotators=annotators,
    )
    synthetic = bool(synthetic_reasons)

    collision_hashes = set(judge.fingerprint.few_shot_content_hashes)
    collisions = sum(1 for r in sampled_records if _canonical_hash(r) in collision_hashes)
    planned_calls = min(len(sampled_records) - collisions, settings.max_labels_per_run)

    # The cost statement MUST land before the first judge call (flush, then label).
    print(
        _render_preflight(
            judge=judge,
            labels_basename=args.labels.name,
            labels_sha256=labels_sha256,
            annotators=annotators,
            sampled=len(sampled_records),
            collisions=collisions,
            planned_calls=planned_calls,
            max_labels=settings.max_labels_per_run,
            synthetic_reasons=synthetic_reasons,
        ),
        end="",
        flush=True,
    )

    labeling = run_labeling(sampled_records, judge=judge, max_labels=settings.max_labels_per_run)
    report = compute_agreement(
        labeling,
        labels,
        human_labels_source=args.labels.name,
        human_labels_sha256=labels_sha256,
        min_human_labels=settings.min_human_labels,
        min_class_support=settings.min_class_support,
        bootstrap_resamples=settings.bootstrap_resamples,
        seed=settings.seed,
    )

    # A real-judge run is NOT byte-deterministic: stamp it and write a PER-RUN
    # basename so re-rolls accumulate instead of silently replacing each other
    # (red-team F-3). The fake path stays byte-deterministic: fixed basename,
    # volatile null.
    volatile: dict[str, str] | None
    if args.judge == "anthropic":
        run_stamp = _collect_volatile()
        volatile = run_stamp
        report_basename = f"agreement_run_report.{run_stamp['run_id']}.json"
    else:
        volatile = None
        report_basename = REPORT_BASENAME

    # The model id(s) ACTUALLY used — response-envelope values stored per label
    # (ADR-0003 rule 2), surfaced here and persisted in the JSON run report.
    served_model_ids = tuple(sorted({e.model_id for e in labeling.labeled_examples}))
    report_text = _render_run_report_json(
        judge_kind=args.judge,
        requested_model_id=judge.fingerprint.model_id,
        served_model_ids=served_model_ids,
        synthetic=synthetic,
        synthetic_reasons=synthetic_reasons,
        volatile=volatile,
        labels_basename=args.labels.name,
        labels_sha256=labels_sha256,
        labeling_report=labeling.report.model_dump(mode="json"),
        agreement_report=report.model_dump(mode="json"),
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_atomic(out_dir / report_basename, report_text)

    served_text = ", ".join(served_model_ids) if served_model_ids else "none"
    footer = [
        "",
        f"report      {report_basename}  written under the --out directory (default: data/out/)",
        f"served      model id(s) actually used: {served_text}",
    ]
    print(render_agreement_report(report) + "\n".join(footer) + "\n", end="")
    return 0


def main() -> int:
    # Same cp1252 guard as export_demo.main(): the agreement report renders "κ"-free
    # ASCII today, but the CLI boundary must not depend on that staying true.
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
