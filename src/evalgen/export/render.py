"""Deterministic text rendering of an export run (demo/agreement output discipline).

Rules enforced by layout: κ never naked (n + CI + threshold ride the gate lines);
digests printed IN FULL (they are the point — the two sha256 lines make the text
golden transitively pin golden.jsonl and meta.json's deterministic section);
basenames only; volatile VALUES never printed (a fixed "recorded, not rendered"
note instead, so the golden stays byte-stable while meta.json stays honest).
"""

from __future__ import annotations

from evalgen.contracts import (
    ExportGateDecision,
    ExportGateVerdict,
    ExportManifest,
    ExportOutcome,
)
from evalgen.export.serialize import (
    canonical_deterministic_bytes,
    render_golden_jsonl,
    render_meta_json,
    sha256_hex,
)

_WRAP_COL = 98
_CHECK_COL = 18


def _wrap_reason(reason: str) -> list[str]:
    """Wrap the override reason (verbatim, quoted) at ~_WRAP_COL, demo wrap style."""
    prefix = '  override    "'
    indent = " " * len(prefix)
    words = reason.split(" ")
    lines: list[str] = []
    current = prefix + words[0]
    for word in words[1:]:
        if len(current) + 1 + len(word) > _WRAP_COL:
            lines.append(current)
            current = indent + word
        else:
            current = current + " " + word
    lines.append(current + '"')
    return lines


def _render_run_and_inputs(manifest: ExportManifest) -> list[str]:
    fingerprint = manifest.export.judge
    lines = [
        f"run         judge={fingerprint.judge_name}  model={fingerprint.model_id}  "
        f"taxonomy={fingerprint.taxonomy_id}  prompt={fingerprint.prompt_sha256[:12]}"
    ]
    files = sorted(manifest.input_files, key=lambda f: f.name)
    width = max(len(f.name) for f in files)
    for i, digest in enumerate(files):
        lead = "inputs      " if i == 0 else " " * 12
        lines.append(f"{lead}{digest.name:<{width}}   [{digest.role.value}]")
        lines.append(" " * 14 + f"sha256={digest.sha256}")
    return lines


def _render_gate(decision: ExportGateDecision, n_matched: int) -> list[str]:
    lines = [f"gate        min_export_kappa={decision.min_export_kappa!r}"]
    for check in decision.checks:
        marker = "pass" if check.passed else "FAIL"
        lines.append(f"  [{marker}] {check.name.value:<{_CHECK_COL}}  {check.detail}")
    if decision.verdict is ExportGateVerdict.PASSED:
        lines.append("  verdict     passed")
    else:  # PASSED_WITH_OVERRIDE — a BLOCKED decision never reaches a report.
        lines.append("  verdict     blocked -> OVERRIDDEN (deliberate)")
    if decision.ci_straddles_threshold:
        lines.append(
            f"  straddle    CI95 lower {decision.ci_lower!r} < min_export_kappa="
            f"{decision.min_export_kappa!r} <= kappa — gate passed STATED (ADR-0004 §6)"
        )
    if decision.kappa is not None and decision.ci_lower is None:
        lines.append("  ci          unavailable (all B resamples degenerate) — stated")
    if decision.override is not None:
        lines.extend(_wrap_reason(decision.override.reason))
    if decision.kappa is not None:
        if decision.ci_lower is not None:
            ci_text = f"CI95=[{decision.ci_lower!r}, {decision.ci_upper!r}]"
        else:
            ci_text = "CI95=unavailable"
        lines.append(
            f"  headline    kappa={decision.kappa!r} (n={n_matched})  {ci_text}  "
            "— carried on this export's face"
        )
    return lines


def _render_contamination(outcome: ExportOutcome, manifest: ExportManifest) -> list[str]:
    report = outcome.report
    lines = [
        "contamination  (export ∩ few-shots on canonical content_hash)",
        f"  candidates={report.candidates_in}  blocked_at_export={len(report.blocked)}",
    ]
    lines.extend(
        f"  blocked  {entry.cause.value}  {entry.record_id}  ({entry.detail})"
        for entry in report.blocked
    )
    labeling = manifest.labeling
    if labeling.skipped_fewshot_collision:
        ids = ", ".join(labeling.fewshot_collision_record_ids)
        noun = "collision" if labeling.skipped_fewshot_collision == 1 else "collisions"
        lines.append(
            f"  note: {labeling.skipped_fewshot_collision} {noun} already excluded at "
            f"labeling ({ids}) — never a candidate"
        )
    return lines


def _render_export(outcome: ExportOutcome, manifest: ExportManifest) -> list[str]:
    report = outcome.report
    golden_sha = sha256_hex(render_golden_jsonl(outcome))
    meta_sha = sha256_hex(canonical_deterministic_bytes(render_meta_json(manifest)))
    return [
        "export",
        f"  candidates={report.candidates_in}  exported={report.exported}  "
        f"blocked={len(report.blocked)}",
        f"  golden.jsonl   {report.exported} lines   sha256={golden_sha}",
        f"  meta.json    deterministic sha256={meta_sha}",
        "  volatile       git_commit + generated_at + environment recorded in meta.json "
        "— not rendered",
    ]


def render_export_report(outcome: ExportOutcome, manifest: ExportManifest) -> str:
    """The full deterministic report — byte-identical for equal inputs."""
    title = "evalgen export — golden set + provenance"
    sections = [
        [title, "=" * len(title)],
        _render_run_and_inputs(manifest),
        _render_gate(outcome.report.gate, manifest.agreement.accounting.n_matched),
        _render_contamination(outcome, manifest),
        _render_export(outcome, manifest),
    ]
    return "\n\n".join("\n".join(section) for section in sections) + "\n"
