"""The demo is a committed byte-exact artifact (ADR-0002 rule 9).

Golden regeneration procedure (review the diff LINE BY LINE — a golden change is a
behavior change; commit fixture + golden together):

    PYTHONPATH=src python -m evalgen.demo > tests/golden/demo_output.txt

Run from the repo root on a UTF-8 stdout (Linux/CI default; on Windows set
``PYTHONUTF8=1`` or regenerate via ``run_demo()`` written with encoding="utf-8",
newline=""). ``.gitattributes`` forces LF for ``*.txt``, so the committed bytes are
newline-stable across platforms.
"""

from __future__ import annotations

import re
from pathlib import Path

from evalgen.demo import main, run_demo

GOLDEN = Path(__file__).resolve().parent / "golden" / "demo_output.txt"

#: Planted secrets from the fixtures — the demo output must contain none of them
#: (they die at the ingest boundary; the demo only ever sees post-redaction text).
PLANTED_SECRETS = (
    "sk-ant-api03-AbCdEf0123456789XyZ9",  # cluster_demo.jsonl redaction twin 1
    "sk-ant-api03-QrStUv9876543210KlM4",  # cluster_demo.jsonl redaction twin 2
    "marc.leroy@autoroutes-nord.fr",
    "sophie.bernard@autoroutes-sud.fr",
    "sk-ant-api03-Zx9yW8vU7tS6rQ5pO4nM3lK2",  # tracespans_demo.jsonl
    "jean.dupont@acme-corp.fr",
    "jane.smith@other-corp.com",
    "+33 6 12 34 56 78",
    "AKIAIOSFODNN7EXAMPLE",  # generic_demo.jsonl
)


def test_output_matches_the_committed_golden_byte_for_byte() -> None:
    with open(GOLDEN, encoding="utf-8", newline="") as fh:
        golden = fh.read()
    assert run_demo() == golden


def test_two_runs_are_byte_identical() -> None:
    assert run_demo() == run_demo()


def test_no_planted_secret_leaks_into_the_output() -> None:
    out = run_demo()
    for secret in PLANTED_SECRETS:
        assert secret not in out
    assert "sk-" not in out
    assert "AKIA" not in out
    # No email-shaped token anywhere in the report.
    assert not re.search(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", out)


def test_no_absolute_path_leaks_into_the_output() -> None:
    out = run_demo()
    assert "C:\\" not in out
    assert "/home/" not in out
    assert "/Users/" not in out


def test_main_prints_the_report_and_exits_zero(capsys) -> None:
    assert main() == 0
    captured = capsys.readouterr()
    assert captured.out == run_demo()


def test_label_stage_shows_the_planted_fewshot_collision() -> None:
    # The demo PROVES the leakage gate in a committed byte-exact artifact (ADR-0003
    # rule 10): the planted twin is skipped, counted, and named.
    out = run_demo()
    assert "fewshot_collisions=1" in out
    assert "collision  rec-d1087e0ca3da3367" in out


def test_label_stage_marks_fake_verdicts_as_synthetic() -> None:
    # FakeJudge distributions are hash-derived noise — the marker is mandatory so the
    # README can never quote them as findings.
    out = run_demo()
    assert "[synthetic fake-judge verdicts]" in out
    assert "judge=fake model=fake-judge-v1" in out
