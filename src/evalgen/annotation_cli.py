"""Annotation CLI: emit the template + instructions for the REAL labeling session
(Phase 5 roadmap wiring of the ADR-0004 rule-2 renderers).

Zero arguments, zero network, byte-identical output every run — pinned by
``tests/golden/annotation_template_output.txt`` and a double-run test. Composition
layer and sibling of ``demo.py``/``agreement_demo.py``: it re-runs the committed
fixture pipeline exactly as they do (ingest -> dedup -> cluster -> sample ->
FakeJudge labeling — the wiring is deliberately duplicated: composition layers may
repeat wiring, they own no logic), then writes the two human-facing artifacts to
``data/annotation/``:

- ``annotation_template.jsonl`` — one fillable line per LABELABLE record, rendered
  by the pure ``render_label_template`` (its signature cannot receive judgments, so
  no verdict, confidence or rationale can leak into what the human fills — re-pinned
  at this level by a CLI test);
- ``annotation_instructions.txt`` — ``render_annotator_instructions`` verbatim (the
  same questions and class definitions the judge prompt renders).

The FakeJudge run here exists ONLY to know the labelable set and the few-shot
collisions: its verdicts are computed and DISCARDED — nothing judge-derived reaches
either artifact. **Few-shot collisions are excluded from the template
deliberately**: the judge holds those records' answer keys (ADR-0003 rule 8), so
the engine will never label them, they structurally cannot enter the Phase 4 kappa
join (ADR-0004 corollary) and the export gate refuses them — asking a human to
annotate them would spend the scarcest resource on a record no measurement can use.

**The annotation directory is structurally apart from the judge's output directory
(red-team F-1 closure, ADR-0004 amendment 2026-08-04).** ``data/out/`` holds
``golden.jsonl`` / ``meta.json`` / agreement run reports — the judge's verdicts for
the very records the human must label blind. Writing the blank template next to
them would hand the annotator the answer key (anchoring — the bias ADR-0004
structurally refuses). So the template lives in ``data/annotation/``, and this CLI
REFUSES any target directory that contains judge artifacts (a structural guard,
not a printed warning; ``agreement_run`` enforces the mirror-image guard).

The filled file must be saved by the HUMAN, outside any agent, as
``data/labels/human_labels.jsonl`` — that basename is hook-protected (agents cannot
write it, and that is the point: writable ground truth would let an agent fabricate
its own kappa). The stdout banner says so on its face.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from evalgen.cluster import HashingEmbedder, cluster_records, stratified_sample
from evalgen.config import get_settings
from evalgen.contracts import TAXONOMY_V2, LabelingOutcome, LogRecord
from evalgen.dedup import run_dedup
from evalgen.ingest import GenericMapping, load_generic_jsonl, load_tracespan_jsonl, sanitize_text
from evalgen.label import FakeJudge, load_few_shots, run_labeling
from evalgen.validate import render_annotator_instructions, render_label_template

#: Same source-checkout resolution as ``demo.py`` (dev tool by declaration).
_REPO_ROOT = Path(__file__).resolve().parents[2]
_FIXTURES_DIR = _REPO_ROOT / "data" / "fixtures"
_FEWSHOTS_PATH = _REPO_ROOT / "data" / "fewshots" / "judge_v1.jsonl"
#: Gitignored runtime dir for the HUMAN-facing artifacts ONLY — deliberately NOT
#: ``data/out/``, which holds the judge's verdicts (red-team F-1 closure).
_OUT_DIR = _REPO_ROOT / "data" / "annotation"

TEMPLATE_BASENAME = "annotation_template.jsonl"
INSTRUCTIONS_BASENAME = "annotation_instructions.txt"

#: Judge-output artifacts that must NEVER share a directory with the blank
#: template — their presence means the answer key sits next to the exam.
_JUDGE_ARTIFACT_PATTERNS = ("golden.jsonl", "meta.json", "agreement_run_report*.json")


class JudgeArtifactsPresentError(RuntimeError):
    """The target directory holds judge verdicts — refusing to write the template.

    Writing the blank template next to ``golden.jsonl`` / ``meta.json`` / an
    agreement run report would send the annotator into a directory containing the
    judge's answers for the exact records they must label independently
    (ADR-0004: anchoring inflates agreement — an inflated kappa is precisely the
    number this repo refuses to ship).
    """


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
    "!! Fill the template OUTSIDE any agent (a plain text editor), then save it as\n"
    "!! data/labels/human_labels.jsonl — that path is hook-protected (agents cannot\n"
    "!! write it, and that is the point: writable ground truth would let an agent\n"
    "!! fabricate its own kappa)."
)


def build_annotation_artifacts() -> tuple[str, str, LabelingOutcome]:
    """Re-run the committed pipeline; return (template, instructions, labeling).

    Pure given the fixture files — every knob from ``get_settings()``, every stage
    deterministic, so two calls return byte-identical strings. The labeling outcome
    rides along so the CLI report (and tests) can account for the excluded records
    without re-deriving them.
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

    by_id: dict[str, LogRecord] = {r.record_id: r for r in outcome.kept}
    sampled_ids = [rid for stratum in sampling.strata for rid in stratum.sampled_record_ids]
    sampled_records = [by_id[rid] for rid in sampled_ids]
    # FakeJudge SOLELY to know the labelable set + collisions (offline, no API);
    # its verdicts are discarded — the renderers below cannot even receive them.
    judge = FakeJudge(
        taxonomy=TAXONOMY_V2, few_shots=load_few_shots(_FEWSHOTS_PATH, sanitizer=sanitize_text)
    )
    labeling = run_labeling(sampled_records, judge=judge, max_labels=settings.max_labels_per_run)

    labelable_records = [by_id[example.record_id] for example in labeling.labeled_examples]
    template = render_label_template(labelable_records, TAXONOMY_V2)
    instructions = render_annotator_instructions(TAXONOMY_V2)
    return template, instructions, labeling


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _render_report(template: str, instructions: str, labeling: LabelingOutcome) -> str:
    """The deterministic stdout report — basenames only, no clock, no judge info."""
    report = labeling.report
    title = "evalgen annotation — template + instructions for the real labeling session"
    lines = [title, "=" * len(title), "", _BANNER, ""]
    lines.append(
        f"pipeline    sampled={report.records_in}  labelable={report.labeled}  "
        f"fewshot_collisions={report.skipped_fewshot_collision}  (budget={report.max_labels})"
    )
    lines.extend(
        f"  excluded  {record_id}  [fewshot_collision]"
        for record_id in report.fewshot_collision_record_ids
    )
    if report.fewshot_collision_record_ids:
        lines.append(
            "            deliberate: the judge holds these records' answer keys, so they\n"
            "            can never be labeled, never enter the kappa join, never reach an\n"
            "            export — annotating them would waste human effort."
        )
    lines.append("")
    template_lines = len(template.splitlines())
    instruction_lines = len(instructions.splitlines())
    lines.append("files       written to data/annotation/ — kept apart from data/out/ on purpose:")
    lines.append("            data/out/ holds the judge's verdicts for these same records")
    lines.append("            (golden.jsonl, meta.json, agreement run reports); a blank template")
    lines.append("            must never sit next to the answer key, and this CLI refuses any")
    lines.append("            directory that contains those artifacts.")
    lines.append(
        f"  {TEMPLATE_BASENAME:<29} {template_lines:>3} lines   sha256={_sha256_hex(template)}"
    )
    lines.append(
        f"  {INSTRUCTIONS_BASENAME:<29} {instruction_lines:>3} lines   "
        f"sha256={_sha256_hex(instructions)}"
    )
    lines.append("")
    lines.append("next")
    lines.append("  1. answer every line per the instructions (task_type, outcome, annotator)")
    lines.append("  2. save as data/labels/human_labels.jsonl — outside any agent")
    lines.append("  3. python -m evalgen.agreement_run --labels data/labels/human_labels.jsonl")
    lines.append("       --judge anthropic")
    return "\n".join(lines) + "\n"


def _write_atomic(path: Path, text: str) -> None:
    """Bytes only, UTF-8, LF preserved; temp-then-replace (writer.py discipline)."""
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_bytes(text.encode("utf-8"))
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    tmp.replace(path)


def _judge_artifacts_in(out_dir: Path) -> tuple[str, ...]:
    """Basenames of judge-output artifacts present in ``out_dir`` (sorted)."""
    if not out_dir.is_dir():
        return ()
    found: set[str] = set()
    for pattern in _JUDGE_ARTIFACT_PATTERNS:
        found.update(path.name for path in out_dir.glob(pattern))
    return tuple(sorted(found))


def run_annotation_cli(out_dir: Path | None = None) -> str:
    """Build both artifacts; write them only when ``out_dir`` is given; return the
    deterministic stdout report (golden-pinned; its sha256 lines transitively pin
    both artifacts).

    Refuses (typed ``JudgeArtifactsPresentError``, nothing written) when
    ``out_dir`` already holds judge output — the F-1 structural guard.
    """
    if out_dir is not None:
        judge_artifacts = _judge_artifacts_in(out_dir)
        if judge_artifacts:
            raise JudgeArtifactsPresentError(
                f"refusing to write the annotation template into '{out_dir}': it holds "
                f"judge output ({', '.join(judge_artifacts)}). The blank template must "
                "never share a directory with the judge's verdicts - an annotator sent "
                "there would find the answer key next to the exam (ADR-0004 "
                "double-blind). Judge artifacts belong in data/out/; give this CLI a "
                "directory that holds none."
            )
    template, instructions, labeling = build_annotation_artifacts()
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        _write_atomic(out_dir / TEMPLATE_BASENAME, template)
        _write_atomic(out_dir / INSTRUCTIONS_BASENAME, instructions)
    return _render_report(template, instructions, labeling)


def main() -> int:
    # Same cp1252 guard as export_demo.main(): the report is ASCII today, but the
    # CLI boundary must not depend on that staying true (legacy Windows consoles).
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    try:
        report = run_annotation_cli(_OUT_DIR)
    except JudgeArtifactsPresentError as error:
        sys.stderr.write(str(error) + "\n")
        return 2
    print(report, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
