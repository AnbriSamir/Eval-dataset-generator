"""The strict human-label loader: refuse loudly, name the line (ADR-0004 rule 2)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from evalgen.contracts import TAXONOMY_V1, TAXONOMY_V2
from evalgen.validate import load_human_labels
from evalgen.validate.errors import (
    DuplicateHumanLabelError,
    HumanLabelFormatError,
    TaxonomyMismatchError,
)

FIXTURE = Path(__file__).resolve().parents[1] / "data" / "fixtures" / "annotations_synthetic.jsonl"


def _line(record_id: str, **overrides: str) -> str:
    payload = {
        "record_id": record_id,
        "taxonomy_id": TAXONOMY_V1.taxonomy_id,
        "task_type": "factual_query",
        "outcome": "correct",
        "annotator": "annotator-a",
        "note": "",
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


def _write(tmp_path: Path, *lines: str) -> Path:
    path = tmp_path / "filled_template.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_valid_file_loads_sorted_by_record_id(tmp_path: Path) -> None:
    path = _write(tmp_path, _line("rec-b"), _line("rec-a"), _line("rec-c"))
    labels = load_human_labels(path)
    assert [label.record_id for label in labels] == ["rec-a", "rec-b", "rec-c"]


def test_display_copies_are_ignored_on_load(tmp_path: Path) -> None:
    path = _write(tmp_path, _line("rec-a", input_text="edited!", output_text="edited!"))
    (label,) = load_human_labels(path)
    assert not hasattr(label, "input_text")  # text truth lives in LogRecord via record_id


def test_duplicate_record_id_refuses(tmp_path: Path) -> None:
    path = _write(tmp_path, _line("rec-a"), _line("rec-a"))
    with pytest.raises(DuplicateHumanLabelError, match="rec-a"):
        load_human_labels(path)


def test_mixed_taxonomy_id_refuses(tmp_path: Path) -> None:
    path = _write(tmp_path, _line("rec-a"), _line("rec-b", taxonomy_id="tax-000000000000"))
    with pytest.raises(TaxonomyMismatchError, match="mixed taxonomy_id"):
        load_human_labels(path)


def test_unfilled_template_line_refuses_naming_the_line(tmp_path: Path) -> None:
    path = _write(tmp_path, _line("rec-a"), _line("rec-b", task_type=""), _line("rec-c"))
    with pytest.raises(HumanLabelFormatError, match=r"line 2"):
        load_human_labels(path)


def test_non_json_line_refuses_naming_the_line(tmp_path: Path) -> None:
    path = _write(tmp_path, _line("rec-a"), "this is not json")
    with pytest.raises(HumanLabelFormatError, match=r"line 2.*not valid JSON"):
        load_human_labels(path)


def test_empty_file_refuses(tmp_path: Path) -> None:
    path = tmp_path / "filled_template.jsonl"
    path.write_text("", encoding="utf-8")
    with pytest.raises(HumanLabelFormatError, match="empty file cannot be measured"):
        load_human_labels(path)


def test_blank_lines_carry_no_data_and_are_skipped(tmp_path: Path) -> None:
    path = _write(tmp_path, _line("rec-a"), "", _line("rec-b"))
    assert len(load_human_labels(path)) == 2


def test_error_messages_name_the_basename_never_a_path(tmp_path: Path) -> None:
    path = _write(tmp_path, "broken")
    with pytest.raises(HumanLabelFormatError) as excinfo:
        load_human_labels(path)
    assert "filled_template.jsonl" in str(excinfo.value)
    assert str(tmp_path) not in str(excinfo.value)  # paths are PII (ADR-0001)


# ---------------------------------------------------- the committed synthetic fixture


def test_committed_synthetic_fixture_loads() -> None:
    labels = load_human_labels(FIXTURE)
    assert len(labels) == 42  # 40 matched + collision twin + not-in-run extra


def test_committed_fixture_is_marked_synthetic_on_every_line() -> None:
    # The anti-swap pin: real-looking labels cannot be quietly slipped into the
    # offline demo — every annotator reads exactly "synthetic" (ADR-0004 options §7).
    labels = load_human_labels(FIXTURE)
    assert {label.annotator for label in labels} == {"synthetic"}
    assert "not ground truth" in labels[0].note


def test_committed_fixture_targets_the_demo_taxonomy_and_id_shape() -> None:
    # The demo taxonomy is TAXONOMY_V2 since ADR-0006 — the fixture migrated with it
    # (a synthetic machinery fixture follows the pipeline default; the HISTORICAL v1
    # human labels do not and are refused by the anti-mix guard instead).
    labels = load_human_labels(FIXTURE)
    assert {label.taxonomy_id for label in labels} == {TAXONOMY_V2.taxonomy_id}
    for label in labels:
        assert re.fullmatch(r"rec-[0-9a-f]{16}", label.record_id)


def test_committed_fixture_lives_outside_the_protected_namespace() -> None:
    # The hook blocks any basename starting 'human_labels' (and golden*.jsonl under
    # data dirs); the synthetic fixture must never shadow that namespace, and must
    # not live in data/labels/ — the protection stays meaningful (ADR-0004 ctx §6).
    assert not FIXTURE.name.startswith("human_labels")
    assert not FIXTURE.name.startswith("golden")
    assert FIXTURE.parent.name == "fixtures"
