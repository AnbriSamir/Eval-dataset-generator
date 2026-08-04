"""The annotation renderers: fillable template + instructions, blind by signature
(ADR-0004 rule 2) — and the ADR-0006 pin that the v2 definitions render VERBATIM
and IDENTICALLY on both sides of the double-blind (judge prompt / annotator
instructions): one questionnaire, two annotators."""

from __future__ import annotations

import json

from conftest import make_record
from evalgen.contracts import TAXONOMY_V1, TAXONOMY_V2, record_sort_key
from evalgen.label.prompt import render_system_prompt
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


class TestOneQuestionnaireTwoAnnotators:
    """ADR-0006 / ADR-0003 rule 1: the judge and the human answer the SAME v2
    questions with the SAME definitions — otherwise κ measures instruction drift."""

    def test_v2_definitions_verbatim_in_the_annotator_instructions(self) -> None:
        instructions = render_annotator_instructions(TAXONOMY_V2)
        for axis in TAXONOMY_V2.axes:
            assert f"Axis '{axis.name}' — {axis.question}" in instructions
            for cls in axis.classes:
                assert f"- {cls.name}: {cls.definition}" in instructions

    def test_v2_definitions_verbatim_in_the_judge_prompt(self) -> None:
        prompt = render_system_prompt(TAXONOMY_V2, ())
        for axis in TAXONOMY_V2.axes:
            assert f"Axis '{axis.name}' — {axis.question}" in prompt
            for cls in axis.classes:
                assert f"- {cls.name}: {cls.definition}" in prompt

    def test_both_sides_render_the_identical_axis_blocks(self) -> None:
        # Byte-identical questionnaire text on both sides of the double-blind: the
        # exact "Axis …" and "- class: definition" lines, in the exact same order.
        def axis_block_lines(text: str) -> list[str]:
            return [
                line
                for line in text.splitlines()
                if line.startswith("Axis '") or line.startswith("- ")
            ]

        judge_side = axis_block_lines(render_system_prompt(TAXONOMY_V2, ()))
        human_side = axis_block_lines(render_annotator_instructions(TAXONOMY_V2))
        assert judge_side == human_side
        assert len(judge_side) == 2 + 5 + 4  # 2 axis headers + 5 + 4 classes

    def test_the_live_claim_convention_reaches_both_artifacts(self) -> None:
        for text in (
            render_system_prompt(TAXONOMY_V2, ()),
            render_annotator_instructions(TAXONOMY_V2),
        ):
            assert "grade the answer AS A RESPONSE" in text
            assert "ONLY when the INPUT is ambiguous or the exchange is incomplete" in text
            assert "never merely because the claim is live" in text
