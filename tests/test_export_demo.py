"""``make export`` is a committed byte-exact artifact — three goldens, one truth
(ADR-0005 options §5).

Golden regeneration procedure (review the diffs LINE BY LINE — a golden change is
a behavior change; the dataset golden gets a full hand-read: redaction
placeholders visible, no secrets, French text intact):

    PYTHONPATH=src PYTHONUTF8=1 python - <<'EOF'
    from pathlib import Path
    from evalgen.export_demo import build_export_artifacts, run_export_demo
    from evalgen.export import render_golden_jsonl, render_meta_json, \
        canonical_deterministic_bytes
    out = run_export_demo()
    outcome, manifest = build_export_artifacts()
    golden = Path("tests/golden")
    (golden / "export_output.txt").write_bytes(out.encode("utf-8"))
    (golden / "export_dataset.txt").write_bytes(render_golden_jsonl(outcome).encode("utf-8"))
    (golden / "export_meta.txt").write_bytes(
        canonical_deterministic_bytes(render_meta_json(manifest)))
    EOF

Run from the repo root; ``.gitattributes`` forces LF for ``*.txt``. The goldens are
``.txt`` on purpose — no committed file may be named ``golden*.jsonl``/``meta.json``
(protect-hook namespace, ADR-0005 context).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from evalgen.export import (
    canonical_deterministic_bytes,
    render_golden_jsonl,
    render_meta_json,
    sha256_hex,
)
from evalgen.export_demo import run_export_demo
from test_demo import PLANTED_SECRETS

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"
GOLDEN_OUTPUT = GOLDEN_DIR / "export_output.txt"
GOLDEN_DATASET = GOLDEN_DIR / "export_dataset.txt"
GOLDEN_META = GOLDEN_DIR / "export_meta.txt"

_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")


@pytest.fixture(scope="module")
def demo_text() -> str:
    return run_export_demo()


class TestTextGolden:
    def test_output_matches_the_committed_golden_byte_for_byte(self, demo_text) -> None:
        with open(GOLDEN_OUTPUT, encoding="utf-8", newline="") as fh:
            assert demo_text == fh.read()

    def test_two_runs_are_byte_identical(self, demo_text) -> None:
        assert run_export_demo() == demo_text

    def test_the_synthetic_banner_is_first_and_mandatory(self, demo_text) -> None:
        assert demo_text.startswith("!! SYNTHETIC")
        assert "DELIBERATE gate" in demo_text
        assert "NOT a shippable dataset" in demo_text
        assert "data/labels/human_labels.jsonl" in demo_text  # where the real one waits

    def test_the_gate_actually_blocked_and_the_override_shouts(self, demo_text) -> None:
        assert re.search(
            r"\[FAIL\] kappa_threshold\s+kappa=0.565581 < min_export_kappa=0.6", demo_text
        )
        assert "verdict     blocked -> OVERRIDDEN (deliberate)" in demo_text
        assert "synthetic machinery proof" in demo_text  # the reason, verbatim
        assert "kappa=0.565581 (n=40)  CI95=[0.361881, 0.757581]" in demo_text

    def test_kappa_never_travels_naked(self, demo_text) -> None:
        assert "n=40" in demo_text
        assert "CI95=[" in demo_text
        assert "min_export_kappa=0.6" in demo_text

    def test_the_funnel_and_contamination_counts(self, demo_text) -> None:
        assert "candidates=49  blocked_at_export=0" in demo_text
        assert "candidates=49  exported=49  blocked=0" in demo_text
        assert "note: 1 collision already excluded at labeling (rec-5e3329f36f536ec4)" in demo_text

    def test_volatile_values_are_never_rendered(self, demo_text) -> None:
        assert "recorded in meta.json — not rendered" in demo_text
        assert not re.search(r"\d{4}-\d{2}-\d{2}", demo_text)  # no wall-clock date leaks


class TestArtifactGoldens:
    def test_dataset_matches_the_committed_golden(self, demo_export_artifacts) -> None:
        outcome, _ = demo_export_artifacts
        assert render_golden_jsonl(outcome).encode("utf-8") == GOLDEN_DATASET.read_bytes()

    def test_deterministic_meta_matches_the_committed_golden(self, demo_export_artifacts) -> None:
        _, manifest = demo_export_artifacts
        assert canonical_deterministic_bytes(render_meta_json(manifest)) == (
            GOLDEN_META.read_bytes()
        )

    def test_printed_digests_transitively_pin_the_other_two_goldens(self, demo_text) -> None:
        golden_match = re.search(r"golden\.jsonl\s+\d+ lines\s+sha256=([0-9a-f]{64})", demo_text)
        meta_match = re.search(r"meta\.json\s+deterministic sha256=([0-9a-f]{64})", demo_text)
        assert golden_match and meta_match
        assert golden_match.group(1) == sha256_hex(GOLDEN_DATASET.read_bytes())
        assert meta_match.group(1) == sha256_hex(GOLDEN_META.read_bytes())

    def test_dataset_line_count_matches_the_printed_count(self, demo_text) -> None:
        lines = GOLDEN_DATASET.read_bytes().decode("utf-8").splitlines()
        assert f"golden.jsonl   {len(lines)} lines" in demo_text


class TestFileWriting:
    def test_out_dir_gets_exactly_the_rendered_bytes(self, tmp_path) -> None:
        run_export_demo(out_dir=tmp_path)
        golden_path = tmp_path / "golden.jsonl"
        meta_path = tmp_path / "meta.json"
        assert sorted(p.name for p in tmp_path.iterdir()) == ["golden.jsonl", "meta.json"]
        assert golden_path.read_bytes() == GOLDEN_DATASET.read_bytes()
        meta_text = meta_path.read_bytes().decode("utf-8")
        assert canonical_deterministic_bytes(meta_text) == GOLDEN_META.read_bytes()
        volatile = json.loads(meta_text)["volatile"]
        assert set(volatile) == {"environment", "generated_at", "git_commit"}
        assert set(volatile["environment"]) == {"evalgen", "platform", "python"}

    def test_main_prints_and_writes_to_the_out_dir(self, monkeypatch, tmp_path, capsys) -> None:
        import evalgen.export_demo as export_demo

        monkeypatch.setattr(export_demo, "_OUT_DIR", tmp_path / "out")
        assert export_demo.main() == 0
        captured = capsys.readouterr()
        assert captured.out.startswith("!! SYNTHETIC")
        assert (tmp_path / "out" / "golden.jsonl").exists()
        assert (tmp_path / "out" / "meta.json").exists()


class TestLeakScan:
    def test_no_planted_secret_reaches_any_golden(self, demo_text) -> None:
        dataset = GOLDEN_DATASET.read_bytes().decode("utf-8")
        meta = GOLDEN_META.read_bytes().decode("utf-8")
        for text in (demo_text, dataset, meta):
            for secret in PLANTED_SECRETS:
                assert secret not in text
            assert "sk-ant" not in text
            assert "AKIA" not in text
            assert not _EMAIL.search(text)

    def test_no_absolute_path_leaks(self, demo_text) -> None:
        dataset = GOLDEN_DATASET.read_bytes().decode("utf-8")
        for text in (demo_text, dataset):
            assert "C:\\\\Users" not in text and "C:\\Users" not in text
            assert "/home/" not in text
            assert "/Users/" not in text

    def test_the_dataset_shows_its_redaction_placeholders(self) -> None:
        # The review rule made executable: post-redaction text is visibly redacted.
        dataset = GOLDEN_DATASET.read_bytes().decode("utf-8")
        assert "[REDACTED:api_key]" in dataset
        assert "[REDACTED:email]" in dataset
