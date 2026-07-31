"""``make agreement`` is a committed byte-exact artifact (ADR-0004 options §7).

Golden regeneration procedure (review the diff LINE BY LINE — a golden change is a
behavior change; commit fixture + golden together):

    PYTHONPATH=src python -m evalgen.agreement_demo > tests/golden/agreement_output.txt

Run from the repo root on a UTF-8 stdout (Linux/CI default; on Windows set
``PYTHONUTF8=1`` or regenerate via ``run_agreement_demo()`` written with
encoding="utf-8", newline=""). ``.gitattributes`` forces LF for ``*.txt``.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from evalgen.agreement_demo import main, run_agreement_demo

GOLDEN = Path(__file__).resolve().parent / "golden" / "agreement_output.txt"
FIXTURE = Path(__file__).resolve().parents[1] / "data" / "fixtures" / "annotations_synthetic.jsonl"


def test_output_matches_the_committed_golden_byte_for_byte() -> None:
    with open(GOLDEN, encoding="utf-8", newline="") as fh:
        golden = fh.read()
    assert run_agreement_demo() == golden


def test_two_runs_are_byte_identical() -> None:
    assert run_agreement_demo() == run_agreement_demo()


def test_main_prints_the_report_and_exits_zero(capsys) -> None:
    assert main() == 0
    captured = capsys.readouterr()
    assert captured.out == run_agreement_demo()


def test_the_synthetic_banner_is_mandatory() -> None:
    out = run_agreement_demo()
    assert out.startswith("!! SYNTHETIC")
    assert "NOT a" in out and "measured kappa" in out
    assert "data/labels/human_labels.jsonl" in out  # where the real number waits


def test_kappa_never_travels_naked() -> None:
    out = run_agreement_demo()
    assert "n=" in out
    assert "CI95" in out
    assert "po=" in out and "pe=" in out
    assert "band=" in out
    assert "human judge both" in out  # per-class support columns


def test_the_fewshot_collision_corollary_is_printed() -> None:
    # ADR-0003 corollary made visible: the judge-seen record can never enter kappa.
    out = run_agreement_demo()
    assert re.search(r"human_only\s+fewshot_collision\s+rec-d1087e0ca3da3367", out)


def test_the_support_gate_is_visible() -> None:
    out = run_agreement_demo()
    assert "insufficient_support" in out


def test_the_report_is_bound_to_the_fixture_bytes() -> None:
    # Red-team M-1 regression: the kappa now travels with the sha256 of the EXACT
    # ground-truth bytes, recomputed here by an independent path (hashlib over the
    # committed fixture — LF-stable via .gitattributes). Editing one label byte
    # would break this assertion AND the golden.
    digest = hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    assert f"labels      sha256={digest}" in run_agreement_demo()


def test_the_gate_knobs_are_visible_in_the_header() -> None:
    # Red-team M-2 regression: the support gate is measurement protocol — as
    # visible as B and seed, even with no suppressed class in sight.
    assert "gates       min_human_labels=30  min_class_support=5" in run_agreement_demo()


def test_the_join_is_fully_accounted() -> None:
    out = run_agreement_demo()
    assert "judged_in=49" in out  # 49 labeled of the 50 sampled (1 collision)
    assert "human_in=42" in out  # 40 matched + collision twin + not-in-run extra
    assert "matched=40" in out
    assert "not_in_run" in out


def test_the_landis_koch_caveat_travels() -> None:
    out = run_agreement_demo()
    assert "a reading aid, not a test" in out


def test_headline_block_present_at_n_40() -> None:
    out = run_agreement_demo()
    assert "headline (outcome axis" in out
    assert "NOT REPORTABLE" not in out


def test_no_secret_or_path_leaks_into_the_output() -> None:
    out = run_agreement_demo()
    assert "sk-" not in out
    assert "AKIA" not in out
    assert not re.search(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", out)
    assert "C:\\" not in out
    assert "/home/" not in out
    assert "/Users/" not in out
    assert "Users" not in out
