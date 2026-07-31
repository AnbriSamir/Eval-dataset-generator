"""STRICT loader for the filled human-label template (ADR-0004 rule 2).

Deliberately the opposite of ingest's tolerant bucketing: ingest reads hostile
production logs (tolerance + accounting); this loader reads a 30–50 line artifact a
human just curated — a malformed or unfilled line is a mistake to fix NOW, so any
invalid line, duplicate ``record_id``, mixed ``taxonomy_id``, or empty file refuses
the whole file with a typed error naming the line.

READ-only by module rule (ADR-0004 rule 7): file access goes through
``Path.read_text`` — ``validate/`` never writes anything, pinned by grep test.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from pydantic import ValidationError

from evalgen.contracts import HumanLabel
from evalgen.validate.errors import (
    DuplicateHumanLabelError,
    HumanLabelFormatError,
    TaxonomyMismatchError,
)


def load_human_labels(path: Path) -> tuple[HumanLabel, ...]:
    """Load and validate one human-label JSONL file; return labels sorted by record_id.

    Raises:
        HumanLabelFormatError: a line is not JSON, not a valid ``HumanLabel`` (an
            unfilled template line — ``"task_type": ""`` — fails enum validation
            here), or the file contains no labels at all. The error names the
            1-based line.
        DuplicateHumanLabelError: a ``record_id`` appears more than once.
        TaxonomyMismatchError: the file mixes ``taxonomy_id`` values — labels from
            different questionnaires cannot ride one file.
    """
    text = path.read_text(encoding="utf-8")
    labels: list[HumanLabel] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue  # blank lines carry no data; every non-blank line must validate
        try:
            payload = json.loads(line)
        except ValueError as exc:
            raise HumanLabelFormatError(
                f"{path.name} line {line_no}: not valid JSON ({exc})"
            ) from exc
        try:
            labels.append(HumanLabel.model_validate(payload))
        except ValidationError as exc:
            raise HumanLabelFormatError(
                f"{path.name} line {line_no}: invalid or unfilled human label — {exc}"
            ) from exc
    if not labels:
        raise HumanLabelFormatError(
            f"{path.name}: no human labels found — an empty file cannot be measured"
        )
    counts = Counter(label.record_id for label in labels)
    duplicates = sorted(record_id for record_id, count in counts.items() if count > 1)
    if duplicates:
        raise DuplicateHumanLabelError(
            f"{path.name}: record_id(s) {duplicates} are labeled more than once — "
            "ground truth must be single-valued"
        )
    taxonomy_ids = sorted({label.taxonomy_id for label in labels})
    if len(taxonomy_ids) > 1:
        raise TaxonomyMismatchError(
            f"{path.name}: mixed taxonomy_id values {taxonomy_ids} — labels from "
            "different questionnaires cannot ride one file"
        )
    return tuple(sorted(labels, key=lambda label: label.record_id))
