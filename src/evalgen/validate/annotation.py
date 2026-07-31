"""The human-annotation renderers — blindness by signature (ADR-0004 rule 2).

``render_label_template(records, taxonomy)`` and
``render_annotator_instructions(taxonomy)`` are pure functions of exactly those
arguments: judgments are UNREPRESENTABLE in their signatures, so no judge verdict,
confidence, or rationale can appear in what the human fills — the mirror of
ADR-0003's two-string ``Judge`` Protocol. Both render from the taxonomy VERBATIM:
one questionnaire, two annotators (the judge prompt renders the same definitions).

The template's label fields are EMPTY strings — an unfilled line fails enum
validation at load time naming its line, so an incomplete file cannot be measured
by accident. ``input_text``/``output_text`` ride along as display copies only; the
loader ignores them by declaration (``HumanLabel.extra="ignore"``).
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from evalgen.contracts import LabelTaxonomy, LogRecord, record_sort_key


def render_label_template(records: Sequence[LogRecord], taxonomy: LabelTaxonomy) -> str:
    """One fillable JSON line per record, in canonical ``record_sort_key`` order.

    Fixed key order, ``ensure_ascii=False`` — deterministic bytes. The human fills
    ``task_type`` / ``outcome`` / ``annotator`` (and optionally ``note``) in an
    editor OUTSIDE any agent, then saves as ``data/labels/human_labels.jsonl``
    (hook-protected; agents cannot write there — that is the point).
    """
    lines = []
    for record in sorted(records, key=record_sort_key):
        payload = {
            "record_id": record.record_id,
            "taxonomy_id": taxonomy.taxonomy_id,
            "task_type": "",
            "outcome": "",
            "annotator": "",
            "note": "",
            "input_text": record.input_text,
            "output_text": record.output_text,
        }
        lines.append(json.dumps(payload, ensure_ascii=False, sort_keys=False))
    return "\n".join(lines) + ("\n" if lines else "")


def render_annotator_instructions(taxonomy: LabelTaxonomy) -> str:
    """The annotator questionnaire — the SAME questions and definitions the judge sees.

    Opens with the independence instruction (the human half of the double-blind:
    the structural half is the template signature; the residual is stated honestly
    in ADR-0004 options §1).
    """
    lines = [
        "Label independently: do not consult the judge's output, labels, confidence,",
        "or rationale before or while labeling. Anchoring on the judge inflates the",
        "agreement this labeling exists to measure.",
        "",
        f"Annotation instructions — {taxonomy.name} {taxonomy.version} "
        f"({taxonomy.taxonomy_id})",
        "",
        "For every line of the template, read input_text and output_text, then fill",
        "the empty fields:",
        '  - "task_type" and "outcome": exactly one class name per axis, from the',
        "    lists below (copy the name verbatim).",
        '  - "annotator": your pseudonym (the same on every line; never an email).',
        '  - "note": optional free text.',
        "Do not edit record_id, taxonomy_id, input_text or output_text — the loader",
        "ignores the display texts and joins on record_id.",
        "Answer every axis for every record; judge only what the exchange itself",
        "shows.",
    ]
    for axis in taxonomy.axes:
        lines.append("")
        lines.append(f"Axis '{axis.name}' — {axis.question}")
        lines.extend(f"- {cls.name}: {cls.definition}" for cls in axis.classes)
    return "\n".join(lines) + "\n"
