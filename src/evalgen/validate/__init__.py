"""Human-label subset workflow, Cohen's kappa (global + per-class), bootstrap CI95,
disagreement drill-down (ADR-0004).

The one module allowed to see BOTH raters — and it only ever measures: it never
modifies labels or judgments and it writes no files (pinned by grep test).
Import DAG: ``validate → contracts`` (+ numpy, stdlib) — no config, no pipeline
sibling, no anthropic; knobs are injected by the composition layer.
"""

from evalgen.validate import errors
from evalgen.validate.agreement import compute_agreement
from evalgen.validate.annotation import render_annotator_instructions, render_label_template
from evalgen.validate.bootstrap import bootstrap_kappa, draw_index_matrix, percentile_ci
from evalgen.validate.human_labels import load_human_labels
from evalgen.validate.kappa import confusion_matrix, landis_koch_band
from evalgen.validate.render import render_agreement_report

__all__ = [
    "bootstrap_kappa",
    "compute_agreement",
    "confusion_matrix",
    "draw_index_matrix",
    "errors",
    "landis_koch_band",
    "load_human_labels",
    "percentile_ci",
    "render_agreement_report",
    "render_annotator_instructions",
    "render_label_template",
]
