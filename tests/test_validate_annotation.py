"""The annotation renderers: fillable template + instructions, blind by signature
(ADR-0004 rule 2)."""

from __future__ import annotations

import json

from conftest import make_record
from evalgen.contracts import TAXONOMY_V1, record_sort_key
from evalgen.validate import render_annotator_instructions, render_label_template

RECORDS = [
    make_record(source_name="b_source.jsonl", line_no=2, input_text="deuxième question"),
    make_record(source_name="a_source.jsonl", line_no=9, input_text="première question"),
    make_record(source_name="b_source.jsonl", line_no=1, input_text="autre question"),
]

EXPECTED_KEYS = [
    "record_id",
    "taxonomy_id",
    "task_type",
    "outcome",
    "annotator",
    "note",
    "input_text",
    "output_text",
]


class TestLabelTemplate:
    def test_one_json_line_per_record_in_record_sort_key_order(self) -> None:
        template = render_label_template(RECORDS, TAXONOMY_V1)
        lines = template.strip().splitlines()
        assert len(lines) == 3
        expected_ids = [r.record_id for r in sorted(RECORDS, key=record_sort_key)]
        assert [json.loads(line)["record_id"] for line in lines] == expected_ids

    def test_label_fields_are_empty_and_keys_fixed(self) -> None:
        template = render_label_template(RECORDS, TAXONOMY_V1)
        for line in template.strip().splitlines():
            payload = json.loads(line)
            assert list(payload) == EXPECTED_KEYS  # fixed key ORDER — deterministic bytes
            assert payload["task_type"] == ""  # unfilled -> refuses to load (enum)
            assert payload["outcome"] == ""
            assert payload["annotator"] == ""
            assert payload["taxonomy_id"] == TAXONOMY_V1.taxonomy_id

    def test_contains_the_display_texts(self) -> None:
        template = render_label_template(RECORDS, TAXONOMY_V1)
        assert "première question" in template  # post-redaction record text, verbatim
        assert "deuxième question" in template

    def test_deterministic_bytes_across_calls(self) -> None:
        assert render_label_template(RECORDS, TAXONOMY_V1) == render_label_template(
            RECORDS, TAXONOMY_V1
        )

    def test_input_order_does_not_matter(self) -> None:
        assert render_label_template(RECORDS, TAXONOMY_V1) == render_label_template(
            list(reversed(RECORDS)), TAXONOMY_V1
        )

    def test_blindness_no_judge_tokens_representable(self) -> None:
        # The signature cannot receive judgments — this test documents the guarantee
        # at the artifact level: no verdict-shaped keys, no confidence, no rationale.
        template = render_label_template(RECORDS, TAXONOMY_V1)
        assert '"rationale"' not in template
        assert '"confidence"' not in template
        assert '"verdict"' not in template
        assert '"model_id"' not in template

    def test_empty_record_list_renders_empty(self) -> None:
        assert render_label_template([], TAXONOMY_V1) == ""


class TestAnnotatorInstructions:
    def test_opens_with_the_independence_instruction(self) -> None:
        instructions = render_annotator_instructions(TAXONOMY_V1)
        assert instructions.splitlines()[0].startswith("Label independently")

    def test_axis_questions_verbatim(self) -> None:
        instructions = render_annotator_instructions(TAXONOMY_V1)
        for axis in TAXONOMY_V1.axes:
            assert axis.question in instructions  # the judge answers the SAME question

    def test_every_class_definition_verbatim_both_axes(self) -> None:
        instructions = render_annotator_instructions(TAXONOMY_V1)
        for axis in TAXONOMY_V1.axes:
            for cls in axis.classes:
                assert f"- {cls.name}: {cls.definition}" in instructions

    def test_deterministic(self) -> None:
        assert render_annotator_instructions(TAXONOMY_V1) == render_annotator_instructions(
            TAXONOMY_V1
        )
