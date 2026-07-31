"""Deterministic text rendering of an ``AgreementReport`` (demo output discipline).

No timestamps, no absolute paths (the report itself carries basenames only), floats
via the rounded values' ``repr``. Every number printed here comes from the
self-validated report — the renderer formats, it never recomputes or filters.
The κ-never-naked rule is enforced by layout: a κ prints WITH its n, p_o/p_e, CI95,
degenerate-resample count and band, or with its typed status — never alone.
"""

from __future__ import annotations

from evalgen.contracts import (
    AgreementReport,
    AxisAgreement,
    BootstrapCI,
    KappaStatus,
    KappaValue,
    UnmatchedHumanCause,
)
from evalgen.validate.kappa import landis_koch_band

_WRAP_COL = 90
_CLASS_COL = 22
_RATIONALE_PREVIEW = 48


def _wrap_ids(prefix: str, ids: tuple[str, ...]) -> list[str]:
    """Wrap 'prefix id, id, id' deterministically at ~_WRAP_COL columns (demo style)."""
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


def _preview(text: str) -> str:
    flat = text.replace("\n", "␤")
    return flat if len(flat) <= _RATIONALE_PREVIEW else flat[: _RATIONALE_PREVIEW - 1] + "…"


def _ci_text(ci: BootstrapCI | None) -> str:
    if ci is None:
        return "CI95=unavailable"
    if ci.lower is None or ci.upper is None:
        return f"CI95=unavailable (all {ci.b_degenerate} of B={ci.b_total} resamples degenerate)"
    return f"CI95=[{ci.lower!r}, {ci.upper!r}] (B={ci.b_total}, degenerate={ci.b_degenerate})"


def _global_kappa_line(value: KappaValue, n: int) -> str:
    if value.status is not KappaStatus.OK:
        return (
            f"  kappa undefined — {value.status.value} (n={n}): both raters "
            "single-class on the same class, agreement above chance is unmeasurable"
        )
    assert value.kappa is not None  # status OK guarantees values (contract validator)
    return (
        f"  kappa={value.kappa!r} (n={n}, po={value.po!r}, pe={value.pe!r})  "
        f"{_ci_text(value.ci95)}  band={landis_koch_band(value.kappa)}"
    )


def _per_class_value_text(value: KappaValue, support: int, min_class_support: int) -> str:
    if value.status is KappaStatus.OK:
        assert value.kappa is not None  # status OK guarantees values (contract validator)
        return (
            f"kappa={value.kappa!r}  {_ci_text(value.ci95)}  "
            f"band={landis_koch_band(value.kappa)}"
        )
    if value.status is KappaStatus.INSUFFICIENT_SUPPORT:
        return (
            f"insufficient_support (h+j={support} < min_class_support="
            f"{min_class_support}) — kappa suppressed, supports shown"
        )
    if value.status is KappaStatus.ABSENT:
        return "absent (neither rater used this class)"
    return "undefined_single_class"


def _render_axis(axis: AxisAgreement, n: int) -> list[str]:
    lines = [f"[axis {axis.axis.value}]", _global_kappa_line(axis.global_kappa, n)]
    header = f"  {'class':<{_CLASS_COL}} {'human':>5} {'judge':>5} {'both':>4}   kappa"
    lines.append(header)
    for entry in axis.per_class:
        support = entry.human_support + entry.judge_support
        lines.append(
            f"  {entry.class_name:<{_CLASS_COL}} {entry.human_support:>5} "
            f"{entry.judge_support:>5} {entry.both:>4}   "
            + _per_class_value_text(entry.value, support, axis.min_class_support)
        )
    legend = " ".join(f"{i + 1}={name}" for i, name in enumerate(axis.class_order))
    lines.append(f"  confusion  rows=human, cols=judge  ({legend})")
    k = len(axis.class_order)
    lines.append("       " + " ".join(f"{i + 1:>4}" for i in range(k)))
    for i, row in enumerate(axis.confusion):
        lines.append(f"    {i + 1:>2} " + " ".join(f"{cell:>4}" for cell in row))
    lines.append(f"  disagreements ({len(axis.disagreements)}):")
    lines.extend(
        f"    {d.human_label} -> {d.judge_label}   {d.record_id}  "
        f'confidence={d.judge_confidence.value}  "{_preview(d.judge_rationale)}"'
        for d in axis.disagreements
    )
    return lines


def _render_accounting(report: AgreementReport) -> list[str]:
    accounting = report.accounting
    lines = [
        f"accounting  judged_in={accounting.judged_in}  human_in={accounting.human_in}  "
        f"matched={accounting.n_matched}  judge_only={len(accounting.judge_only_ids)}  "
        f"human_only={len(accounting.human_only)}"
    ]
    if accounting.judge_only_ids:
        lines.extend(_wrap_ids("  judge_only   ", accounting.judge_only_ids))
    for unmatched in accounting.human_only:
        suffix = ""
        if unmatched.cause is UnmatchedHumanCause.REFUSED:
            suffix = "  [coverage loss: refusals correlate with hard cases]"
        elif unmatched.cause is UnmatchedHumanCause.FEWSHOT_COLLISION:
            suffix = "  (judge saw its answer key — structurally excluded from kappa)"
        lines.append(f"  human_only   {unmatched.cause.value:<18} {unmatched.record_id}{suffix}")
    return lines


def _render_headline(report: AgreementReport) -> list[str]:
    headline = report.headline
    if headline is None:
        return [
            "headline",
            f"  NOT REPORTABLE: n={report.accounting.n_matched} < "
            f"min_human_labels={report.min_human_labels}",
        ]
    n = report.accounting.n_matched
    return ["headline (outcome axis, the export gate's number)", _global_kappa_line(headline, n)]


def _labels_binding_line(report: AgreementReport) -> str:
    """The sha256 binding to the exact ground-truth bytes (red-team M-1 closure).

    An unbound report says so on its face — "unrecorded" prints, absence never
    hides (the nothing-dropped-in-silence rule applied to provenance).
    """
    if report.human_labels_sha256 is None:
        return "labels      sha256=unrecorded — report not bound to the label-file bytes"
    return f"labels      sha256={report.human_labels_sha256}"


def render_agreement_report(report: AgreementReport) -> str:
    """The full deterministic report — byte-identical for equal reports."""
    title = "evalgen agreement — Cohen's kappa, judge vs human labels"
    header = [
        title,
        "=" * len(title),
        "",
        f"source      {report.human_labels_source}   annotators: " + ", ".join(report.annotators),
        _labels_binding_line(report),
        f"taxonomy    {report.taxonomy_id}",
        f"judge       {report.judge.judge_name}  model={report.judge.model_id}  "
        f"prompt={report.judge.prompt_sha256[:12]}",
        f"bootstrap   B={report.bootstrap_resamples}  seed={report.seed}  method=percentile",
        # The gate knobs are measurement protocol, as visible as B and seed even
        # when no class happens to be suppressed (red-team M-2 closure).
        f"gates       min_human_labels={report.min_human_labels}  "
        f"min_class_support={report.min_class_support}",
    ]
    degenerate_total = sum(
        value.ci95.b_degenerate
        for axis in report.axes
        for value in [axis.global_kappa] + [c.value for c in axis.per_class]
        if value.ci95 is not None
    )
    caveats = [
        "caveats",
        "  - Landis & Koch 1977 descriptive bands — a reading aid, not a test.",
        f"  - small n (matched={report.accounting.n_matched}): percentile bootstrap "
        "CI95 can undercover; read kappa with its supports and interval.",
    ]
    if degenerate_total:
        caveats.append(
            f"  - {degenerate_total} degenerate resamples (p_e=1) across all intervals "
            "were excluded from percentiles and counted per interval."
        )
    sections = [
        header,
        _render_accounting(report),
        *[_render_axis(axis, report.accounting.n_matched) for axis in report.axes],
        _render_headline(report),
        caveats,
    ]
    return "\n\n".join("\n".join(section) for section in sections) + "\n"
