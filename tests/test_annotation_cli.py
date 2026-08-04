"""``python -m evalgen.annotation_cli`` — the template/instructions emitter is a
committed byte-exact artifact whose products carry ZERO judge information.

Golden regeneration procedure (review the diff LINE BY LINE — a golden change is a
behavior change):

    PYTHONPATH=src PYTHONUTF8=1 python - <<'EOF'
    from pathlib import Path
    from evalgen.annotation_cli import run_annotation_cli
    Path("tests/golden/annotation_template_output.txt").write_bytes(
        run_annotation_cli().encode("utf-8"))
    EOF

Run from the repo root; ``.gitattributes`` forces LF for ``*.txt``. The stdout's
two sha256 lines transitively pin both written artifacts (the export_demo golden
discipline), so the single stdout golden freezes the template and the
instructions byte-for-byte.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from evalgen.annotation_cli import (
    INSTRUCTIONS_BASENAME,
    TEMPLATE_BASENAME,
    JudgeArtifactsPresentError,
    build_annotation_artifacts,
    main,
    run_annotation_cli,
)
from evalgen.contracts import TAXONOMY_V2
from evalgen.label import FAKE_JUDGE_MODEL_ID
from evalgen.validate import render_annotator_instructions

GOLDEN = Path(__file__).resolve().parent / "golden" / "annotation_template_output.txt"

#: The planted few-shot collision twin (demo/agreement pins, ADR-0003 rule 8).
COLLISION_RECORD_ID = "rec-5e3329f36f536ec4"

_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")


@pytest.fixture(scope="module")
def artifacts() -> tuple[str, str, object]:
    return build_annotation_artifacts()


@pytest.fixture(scope="module")
def cli_text() -> str:
    return run_annotation_cli()


# ------------------------------------------------------------------- determinism


def test_output_matches_the_committed_golden_byte_for_byte(cli_text: str) -> None:
    with open(GOLDEN, encoding="utf-8", newline="") as fh:
        assert cli_text == fh.read()


def test_two_runs_are_byte_identical(cli_text: str) -> None:
    assert run_annotation_cli() == cli_text


def test_two_builds_produce_byte_identical_artifacts(artifacts) -> None:
    template, instructions, _ = artifacts
    template_again, instructions_again, _ = build_annotation_artifacts()
    assert template_again == template
    assert instructions_again == instructions


# ----------------------------------------------------------- the written products


def test_written_files_are_exactly_the_rendered_bytes(tmp_path, artifacts) -> None:
    template, instructions, _ = artifacts
    run_annotation_cli(out_dir=tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        INSTRUCTIONS_BASENAME,
        TEMPLATE_BASENAME,
    ]
    assert (tmp_path / TEMPLATE_BASENAME).read_bytes() == template.encode("utf-8")
    assert (tmp_path / INSTRUCTIONS_BASENAME).read_bytes() == instructions.encode("utf-8")


def test_stdout_digests_transitively_pin_both_artifacts(cli_text: str, artifacts) -> None:
    template, instructions, _ = artifacts
    digests = re.findall(r"sha256=([0-9a-f]{64})", cli_text)
    assert digests == [
        hashlib.sha256(template.encode("utf-8")).hexdigest(),
        hashlib.sha256(instructions.encode("utf-8")).hexdigest(),
    ]


def test_main_writes_to_the_out_dir_and_prints(monkeypatch, tmp_path, capsys) -> None:
    import evalgen.annotation_cli as annotation_cli

    monkeypatch.setattr(annotation_cli, "_OUT_DIR", tmp_path / "out")
    assert main() == 0
    captured = capsys.readouterr()
    assert captured.out == run_annotation_cli()
    assert (tmp_path / "out" / TEMPLATE_BASENAME).exists()
    assert (tmp_path / "out" / INSTRUCTIONS_BASENAME).exists()


# --------------------------------------------------- the labelable set + collision


def test_template_covers_exactly_the_labelable_set(artifacts) -> None:
    template, _, labeling = artifacts
    template_ids = [json.loads(line)["record_id"] for line in template.splitlines()]
    labeled_ids = [example.record_id for example in labeling.labeled_examples]
    # Same set — the renderer re-orders by record_sort_key, so compare as sets and
    # pin uniqueness separately.
    assert len(template_ids) == len(set(template_ids))
    assert set(template_ids) == set(labeled_ids)
    assert len(template_ids) == labeling.report.labeled == 49


def test_the_fewshot_collision_is_excluded_and_documented(cli_text: str, artifacts) -> None:
    template, _, labeling = artifacts
    assert labeling.report.fewshot_collision_record_ids == (COLLISION_RECORD_ID,)
    assert COLLISION_RECORD_ID not in template
    # The exclusion is a deliberate, documented choice, printed on the CLI's face.
    assert f"excluded  {COLLISION_RECORD_ID}  [fewshot_collision]" in cli_text
    assert "never enter the kappa join" in cli_text
    assert "waste human effort" in cli_text


# ------------------------------------------------------------- zero judge leakage


def test_template_lines_carry_empty_label_fields_and_no_verdict(artifacts) -> None:
    template, _, _ = artifacts
    for line in template.splitlines():
        payload = json.loads(line)
        assert set(payload) == {
            "record_id",
            "taxonomy_id",
            "task_type",
            "outcome",
            "annotator",
            "note",
            "input_text",
            "output_text",
        }
        assert payload["task_type"] == ""
        assert payload["outcome"] == ""
        assert payload["annotator"] == ""
        assert payload["note"] == ""
        assert payload["taxonomy_id"] == TAXONOMY_V2.taxonomy_id


def test_no_judge_information_reaches_the_artifacts(artifacts) -> None:
    # The renderer signatures make judgments unrepresentable (ADR-0004 rule 2);
    # this re-pins the guarantee at the CLI level: nothing fingerprint- or
    # verdict-shaped survives into what the human sees.
    template, instructions, labeling = artifacts
    fingerprint = labeling.report.judge
    for text in (template, instructions):
        assert FAKE_JUDGE_MODEL_ID not in text
        assert fingerprint.prompt_sha256[:12] not in text
        assert "synthetic fake-judge verdict" not in text
    # The instructions legitimately NAME the judge's confidence/rationale in the
    # independence instruction ("do not consult ..."); the fillable template must
    # not even carry the words — there is no field a verdict could hide in.
    assert "confidence" not in template
    assert "rationale" not in template


def test_instructions_are_the_pure_renderer_output_verbatim(artifacts) -> None:
    _, instructions, _ = artifacts
    assert instructions == render_annotator_instructions(TAXONOMY_V2)


# ------------------------------------------------------------------ banner + leaks


def test_the_banner_names_the_protected_destination_and_the_hook(cli_text: str) -> None:
    assert "data/labels/human_labels.jsonl" in cli_text
    assert "OUTSIDE any agent" in cli_text
    assert "hook-protected" in cli_text


def test_the_next_steps_point_at_the_real_cli(cli_text: str) -> None:
    assert "python -m evalgen.agreement_run --labels data/labels/human_labels.jsonl" in cli_text
    assert "--judge anthropic" in cli_text


def test_no_absolute_path_or_secret_leaks_in_stdout(cli_text: str) -> None:
    assert "C:\\" not in cli_text
    assert "/home/" not in cli_text
    assert "/Users/" not in cli_text
    assert "sk-" not in cli_text
    assert "AKIA" not in cli_text
    assert not _EMAIL.search(cli_text)


# ------------------- F-1: the template never shares a directory with judge output


def test_the_annotation_dir_is_structurally_apart_from_judge_output_dirs() -> None:
    # The red-team F-1 root cause was co-location by DEFAULT: all three CLIs
    # wrote to data/out/. The annotation artifacts now have their own home.
    import evalgen.agreement_run as agreement_run
    import evalgen.annotation_cli as annotation_cli
    import evalgen.export_demo as export_demo

    assert annotation_cli._OUT_DIR.name == "annotation"
    assert annotation_cli._OUT_DIR != agreement_run._DEFAULT_OUT_DIR
    assert annotation_cli._OUT_DIR != export_demo._OUT_DIR


@pytest.mark.parametrize(
    "artifact",
    [
        "golden.jsonl",
        "meta.json",
        "agreement_run_report.json",
        "agreement_run_report.20260804T120000Z-deadbeef.json",
    ],
)
def test_a_directory_holding_judge_output_is_refused(tmp_path, artifact: str) -> None:
    # The red-team F-1 payload replayed: the target directory already holds a
    # verdict-bearing artifact -> the CLI refuses and writes NOTHING (the blank
    # exam must never land next to the answer key).
    (tmp_path / artifact).write_text("{}\n", encoding="utf-8")
    with pytest.raises(JudgeArtifactsPresentError) as excinfo:
        run_annotation_cli(out_dir=tmp_path)
    message = str(excinfo.value)
    assert artifact in message
    assert "answer key" in message
    assert not (tmp_path / TEMPLATE_BASENAME).exists()
    assert not (tmp_path / INSTRUCTIONS_BASENAME).exists()


def test_main_exits_2_when_the_out_dir_holds_judge_output(monkeypatch, tmp_path, capsys) -> None:
    import evalgen.annotation_cli as annotation_cli

    out = tmp_path / "out"
    out.mkdir()
    (out / "golden.jsonl").write_text("{}\n", encoding="utf-8")
    (out / "meta.json").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(annotation_cli, "_OUT_DIR", out)
    assert main() == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "golden.jsonl" in captured.err
    assert "meta.json" in captured.err
    assert not (out / TEMPLATE_BASENAME).exists()


def test_the_report_states_the_directory_separation(cli_text: str) -> None:
    assert "data/annotation/" in cli_text
    assert "kept apart from data/out/" in cli_text
    assert "answer key" in cli_text
