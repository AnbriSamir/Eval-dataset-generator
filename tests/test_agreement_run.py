"""``python -m evalgen.agreement_run`` — explicit flags, honest banner, bounded cost.

STRICTLY offline: the ``--judge anthropic`` battery mocks the SDK CLIENT CLASS
(the ``test_label_anthropic`` discipline — duck-typed responses, a recording
``messages.parse``, never a network call, never a key), monkeypatched at the one
place the real judge constructs it. The ``--judge fake`` battery is the full
deterministic path: pipeline -> FakeJudge -> strict loader join -> AgreementReport
-> JSON run report, with sums pinned against the Phase 4 goldens (judged_in=49,
human_in=42, matched=40, headline kappa=0.513109).
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from conftest import STUB_VERDICT
from evalgen.agreement_run import (
    REPORT_BASENAME,
    REPORT_SCHEMA,
    check_labels_match_run_taxonomy,
    run,
)
from evalgen.contracts import TAXONOMY_V1, TAXONOMY_V2, HumanLabel, JudgeVerdict
from evalgen.label import FAKE_JUDGE_MODEL_ID
from evalgen.validate.errors import TaxonomyMismatchError

FIXTURE = Path(__file__).resolve().parents[1] / "data" / "fixtures" / "annotations_synthetic.jsonl"

_BANNED_KWARGS = ("temperature", "top_p", "top_k", "budget_tokens")


# ------------------------------------------------------------------ SDK mocking


@dataclass
class DuckResponse:
    """Duck-typed stand-in for a messages.parse response — no SDK mocks."""

    stop_reason: str | None = "end_turn"
    model: str = "claude-opus-4-8-served"
    parsed_output: Any = None
    stop_details: Any = None


class RecordingMessages:
    def __init__(self, response: Any, on_call: Callable[[], None] | None = None) -> None:
        self._response = response
        self._on_call = on_call
        self.calls: list[dict[str, Any]] = []

    def parse(self, **kwargs: Any) -> Any:
        if self._on_call is not None:
            self._on_call()
        self.calls.append(kwargs)
        return self._response


class RecordingClient:
    """Injected in place of anthropic.Anthropic — records the exact call structure."""

    def __init__(self, response: Any, on_call: Callable[[], None] | None = None) -> None:
        self.messages = RecordingMessages(response, on_call)


def _mock_sdk(monkeypatch: pytest.MonkeyPatch, client: RecordingClient) -> None:
    """Replace the SDK client class at the one place the real judge constructs it."""
    import evalgen.label.anthropic_judge as anthropic_judge

    monkeypatch.setattr(anthropic_judge.anthropic, "Anthropic", lambda: client)


def _labels_copy(tmp_path: Path, *, mutate: bool, name: str = "session_labels.jsonl") -> Path:
    """A runtime copy of the committed fixture; ``mutate`` appends a blank line —
    loader-invisible (blank lines are skipped) but sha256-visible, so the copy
    stops being byte-identical to the fixture without changing a single label."""
    data = FIXTURE.read_bytes()
    if mutate:
        data += b"\n"
    path = tmp_path / name
    path.write_bytes(data)
    return path


def _fixture_payloads() -> list[dict[str, Any]]:
    lines = FIXTURE.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _write_labels(tmp_path: Path, payloads: list[dict[str, Any]], name: str) -> Path:
    path = tmp_path / name
    path.write_text("".join(json.dumps(p) + "\n" for p in payloads), encoding="utf-8")
    return path


def _real_labels_copy(tmp_path: Path, name: str = "session_labels.jsonl") -> Path:
    """A GENUINELY different labels file: every annotator renamed AND one outcome
    flipped, so none of the three data-side synthetic triggers (byte identity,
    canonical label content, annotator marker) can fire — the honest REAL-DATA
    shape a true labeling session would have."""
    payloads = _fixture_payloads()
    for payload in payloads:
        payload["annotator"] = "annotator-a"
    payloads[0]["outcome"] = "correct" if payloads[0]["outcome"] != "correct" else "incorrect"
    return _write_labels(tmp_path, payloads, name)


def _read_run_report(out_dir: Path) -> tuple[str, dict[str, Any]]:
    """The single run report in ``out_dir`` — (basename, parsed payload)."""
    files = sorted(out_dir.glob("agreement_run_report*.json"))
    assert len(files) == 1, files
    return files[0].name, json.loads(files[0].read_bytes().decode("utf-8"))


# ------------------------------------------------------------- flag explicitness


def test_missing_labels_flag_is_refused_naming_the_flag(capsys) -> None:
    with pytest.raises(SystemExit) as excinfo:
        run(["--judge", "fake"])
    assert excinfo.value.code == 2
    assert "--labels" in capsys.readouterr().err


def test_missing_judge_flag_is_refused_naming_the_flag(tmp_path, capsys) -> None:
    labels = _labels_copy(tmp_path, mutate=False)
    with pytest.raises(SystemExit) as excinfo:
        run(["--labels", str(labels)])
    assert excinfo.value.code == 2
    assert "--judge" in capsys.readouterr().err


def test_model_flag_with_fake_judge_is_refused(tmp_path, capsys) -> None:
    labels = _labels_copy(tmp_path, mutate=False)
    with pytest.raises(SystemExit) as excinfo:
        run(["--labels", str(labels), "--judge", "fake", "--model", "claude-opus-4-8"])
    assert excinfo.value.code == 2
    assert "--model applies only to --judge anthropic" in capsys.readouterr().err


# ------------------------------------------------------------ fake judge, full path


@pytest.fixture(scope="module")
def fake_run(tmp_path_factory) -> tuple[str, bytes]:
    """One full --judge fake run (stdout text, report-file bytes), shared."""
    tmp_path = tmp_path_factory.mktemp("fake_run")
    labels = _labels_copy(tmp_path, mutate=True)
    out_dir = tmp_path / "out"
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = run(["--labels", str(labels), "--judge", "fake", "--out", str(out_dir)])
    assert code == 0
    return buf.getvalue(), (out_dir / REPORT_BASENAME).read_bytes()


def test_fake_path_is_green_with_exact_sums(fake_run) -> None:
    out, _ = fake_run
    # The join is the Phase 4 one (same pipeline, same labels): sums are exact.
    assert "judged_in=49" in out
    assert "human_in=42" in out
    assert "matched=40" in out
    assert "kappa=0.513109" in out  # the outcome-axis headline, agreement-golden pin


def test_fake_path_writes_the_json_run_report(fake_run) -> None:
    out, report_bytes = fake_run
    payload = json.loads(report_bytes.decode("utf-8"))
    assert payload["schema"] == REPORT_SCHEMA
    assert payload["judge_kind"] == "fake"
    assert payload["synthetic"] is True
    # All three data-side facts are named: fake judge, fixture content (the copy is
    # re-encoded, not byte-identical), and the pinned synthetic annotator (F-2).
    assert payload["synthetic_reasons"] == [
        "judge=fake (hash-derived noise verdicts)",
        "--labels carries the committed synthetic fixture's labels "
        "(re-encoded bytes cannot shed the banner)",
        "annotator 'synthetic' present (the fixture's pinned marker)",
    ]
    # The deterministic fake path carries no volatile stamp (F-3: real runs only).
    assert payload["volatile"] is None
    assert payload["requested_model_id"] == FAKE_JUDGE_MODEL_ID
    assert payload["served_model_ids"] == [FAKE_JUDGE_MODEL_ID]
    assert payload["labels_file"] == "session_labels.jsonl"
    expected_sha = hashlib.sha256(FIXTURE.read_bytes() + b"\n").hexdigest()
    assert payload["labels_sha256"] == expected_sha
    assert payload["agreement_report"]["human_labels_sha256"] == expected_sha
    assert payload["agreement_report"]["accounting"]["n_matched"] == 40
    # The headline is a derived property (not serialized): read the outcome axis.
    outcome_axis = next(a for a in payload["agreement_report"]["axes"] if a["axis"] == "outcome")
    assert outcome_axis["global_kappa"]["kappa"] == 0.513109
    assert payload["labeling_report"]["records_in"] == 50
    assert payload["labeling_report"]["labeled"] == 49
    assert payload["labeling_report"]["skipped_fewshot_collision"] == 1
    # The stdout binding travels too.
    assert f"sha256={expected_sha}" in out


def test_fake_double_run_is_byte_identical(tmp_path) -> None:
    labels = _labels_copy(tmp_path, mutate=True)
    outputs: list[str] = []
    reports: list[bytes] = []
    for name in ("a", "b"):
        out_dir = tmp_path / f"out_{name}"
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            assert run(["--labels", str(labels), "--judge", "fake", "--out", str(out_dir)]) == 0
        outputs.append(buf.getvalue())
        reports.append((out_dir / REPORT_BASENAME).read_bytes())
    assert outputs[0] == outputs[1]
    assert reports[0] == reports[1]


def test_fake_cost_line_prints_the_planned_calls(fake_run) -> None:
    out, _ = fake_run
    assert "planned_judge_calls=49" in out
    assert "sampled=50" in out
    assert "fewshot_collisions=1" in out
    assert "max_labels_per_run=500" in out


# ------------------------------------------------------------- banner: three cases


def test_banner_fake_judge_is_synthetic(fake_run) -> None:
    out, _ = fake_run
    assert "!! SYNTHETIC" in out
    assert "judge=fake (hash-derived noise verdicts)" in out
    assert "NOT a measured kappa" in out
    assert "data/labels/human_labels.jsonl" in out
    assert "!! REAL DATA" not in out


def test_banner_fixture_labels_are_synthetic_even_with_the_real_judge(
    monkeypatch, tmp_path, capsys
) -> None:
    client = RecordingClient(DuckResponse(parsed_output=STUB_VERDICT))
    _mock_sdk(monkeypatch, client)
    labels = _labels_copy(tmp_path, mutate=False)  # byte-identical to the fixture
    out_dir = tmp_path / "out"
    assert run(["--labels", str(labels), "--judge", "anthropic", "--out", str(out_dir)]) == 0
    out = capsys.readouterr().out
    assert "!! SYNTHETIC" in out
    assert "--labels is byte-identical to the committed synthetic fixture" in out
    assert "!! REAL DATA" not in out
    _, payload = _read_run_report(out_dir)
    assert payload["synthetic"] is True


def test_genuinely_real_labels_print_the_real_header_and_no_banner(
    monkeypatch, tmp_path, capsys
) -> None:
    client = RecordingClient(DuckResponse(parsed_output=STUB_VERDICT))
    _mock_sdk(monkeypatch, client)
    labels = _real_labels_copy(tmp_path)  # different labels AND annotator: real shape
    out_dir = tmp_path / "out"
    assert run(["--labels", str(labels), "--judge", "anthropic", "--out", str(out_dir)]) == 0
    out = capsys.readouterr().out
    assert "!! SYNTHETIC" not in out
    assert "!! REAL DATA" in out
    assert "NOT byte-deterministic" in out
    assert "run         judge=anthropic  model=claude-opus-4-8" in out
    assert "labels      session_labels.jsonl  sha256=" in out
    assert "annotators  annotator-a" in out
    _, payload = _read_run_report(out_dir)
    assert payload["synthetic"] is False
    assert payload["synthetic_reasons"] == []


# ------------------------------- F-2 payload replays: re-encoding cannot shed the banner


def test_crlf_reencoded_fixture_still_wears_the_synthetic_banner(
    monkeypatch, tmp_path, capsys
) -> None:
    # The red team's exact payload: CRLF re-encode -> different sha256, identical
    # HumanLabel objects. Byte identity misses; the content trigger must fire.
    client = RecordingClient(DuckResponse(parsed_output=STUB_VERDICT))
    _mock_sdk(monkeypatch, client)
    labels = tmp_path / "session_labels.jsonl"
    labels.write_bytes(FIXTURE.read_bytes().replace(b"\n", b"\r\n"))
    out_dir = tmp_path / "out"
    assert run(["--labels", str(labels), "--judge", "anthropic", "--out", str(out_dir)]) == 0
    out = capsys.readouterr().out
    assert "!! SYNTHETIC" in out
    assert "re-encoded bytes cannot shed the banner" in out
    assert "!! REAL DATA" not in out
    _, payload = _read_run_report(out_dir)
    assert payload["synthetic"] is True
    assert any("re-encoded" in reason for reason in payload["synthetic_reasons"])


def test_trailing_newline_reencode_still_wears_the_synthetic_banner(
    monkeypatch, tmp_path, capsys
) -> None:
    # The formerly BLESSED bypass (a lone appended newline used to buy the REAL
    # DATA header) — now inverted: same labels, same banner.
    client = RecordingClient(DuckResponse(parsed_output=STUB_VERDICT))
    _mock_sdk(monkeypatch, client)
    labels = _labels_copy(tmp_path, mutate=True)
    out_dir = tmp_path / "out"
    assert run(["--labels", str(labels), "--judge", "anthropic", "--out", str(out_dir)]) == 0
    out = capsys.readouterr().out
    assert "!! SYNTHETIC" in out
    assert "!! REAL DATA" not in out
    _, payload = _read_run_report(out_dir)
    assert payload["synthetic"] is True


def test_the_synthetic_annotator_marker_alone_triggers_the_banner(
    monkeypatch, tmp_path, capsys
) -> None:
    # Labels genuinely differ from the fixture (one outcome flipped) but the
    # pinned annotator marker remains: still an independent synthetic trigger.
    client = RecordingClient(DuckResponse(parsed_output=STUB_VERDICT))
    _mock_sdk(monkeypatch, client)
    payloads = _fixture_payloads()
    payloads[0]["outcome"] = "correct" if payloads[0]["outcome"] != "correct" else "incorrect"
    labels = _write_labels(tmp_path, payloads, "session_labels.jsonl")
    out_dir = tmp_path / "out"
    assert run(["--labels", str(labels), "--judge", "anthropic", "--out", str(out_dir)]) == 0
    out = capsys.readouterr().out
    assert "!! SYNTHETIC" in out
    assert "annotator 'synthetic' present (the fixture's pinned marker)" in out
    assert "!! REAL DATA" not in out
    _, payload = _read_run_report(out_dir)
    assert payload["synthetic"] is True
    assert payload["synthetic_reasons"] == [
        "annotator 'synthetic' present (the fixture's pinned marker)"
    ]


# ------------------------------------------- anthropic judge via the mocked client


def test_anthropic_call_structure_honors_the_sdk_rules(monkeypatch, tmp_path, capsys) -> None:
    client = RecordingClient(DuckResponse(parsed_output=STUB_VERDICT))
    _mock_sdk(monkeypatch, client)
    labels = _labels_copy(tmp_path, mutate=True)
    assert run(["--labels", str(labels), "--judge", "anthropic", "--out", str(tmp_path / "o")]) == 0
    capsys.readouterr()
    calls = client.messages.calls
    assert len(calls) == 49  # 50 sampled - 1 collision, under the 500 budget
    for kwargs in calls:
        assert kwargs["model"] == "claude-opus-4-8"  # config default, no --model given
        assert kwargs["thinking"] == {"type": "adaptive"}
        assert kwargs["output_config"] == {"effort": "high"}
        assert kwargs["output_format"] is JudgeVerdict
        for banned in _BANNED_KWARGS:
            assert banned not in kwargs
        assert "budget_tokens" not in kwargs["thinking"]


def test_model_flag_reaches_the_api_and_the_run_report(monkeypatch, tmp_path, capsys) -> None:
    client = RecordingClient(DuckResponse(parsed_output=STUB_VERDICT))
    _mock_sdk(monkeypatch, client)
    labels = _labels_copy(tmp_path, mutate=True)
    out_dir = tmp_path / "out"
    assert (
        run(
            [
                "--labels",
                str(labels),
                "--judge",
                "anthropic",
                "--model",
                "claude-sonnet-4-6",
                "--out",
                str(out_dir),
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert all(kwargs["model"] == "claude-sonnet-4-6" for kwargs in client.messages.calls)
    assert "model=claude-sonnet-4-6" in out
    _, payload = _read_run_report(out_dir)
    assert payload["requested_model_id"] == "claude-sonnet-4-6"


def test_the_served_model_id_is_stored_not_the_requested_echo(
    monkeypatch, tmp_path, capsys
) -> None:
    client = RecordingClient(DuckResponse(parsed_output=STUB_VERDICT))
    _mock_sdk(monkeypatch, client)
    labels = _labels_copy(tmp_path, mutate=True)
    out_dir = tmp_path / "out"
    assert run(["--labels", str(labels), "--judge", "anthropic", "--out", str(out_dir)]) == 0
    out = capsys.readouterr().out
    _, payload = _read_run_report(out_dir)
    # response.model (the SERVING model), never the requested-model echo (ADR-0003).
    assert payload["served_model_ids"] == ["claude-opus-4-8-served"]
    assert "model id(s) actually used: claude-opus-4-8-served" in out


def test_the_planned_call_count_prints_before_the_first_api_call(monkeypatch, tmp_path) -> None:
    buf = io.StringIO()
    stdout_at_call: list[str] = []

    def on_call() -> None:
        stdout_at_call.append(buf.getvalue())

    client = RecordingClient(DuckResponse(parsed_output=STUB_VERDICT), on_call=on_call)
    _mock_sdk(monkeypatch, client)
    labels = _labels_copy(tmp_path, mutate=True)
    with contextlib.redirect_stdout(buf):
        assert run(["--labels", str(labels), "--judge", "anthropic", "--out", str(tmp_path)]) == 0
    assert len(stdout_at_call) == 49
    # The cost statement was ALREADY on stdout when the first API call happened.
    assert "planned_judge_calls=49" in stdout_at_call[0]


# --------------------------------------------------------------- default --out dir


def test_out_defaults_to_data_out(monkeypatch, tmp_path, capsys) -> None:
    import evalgen.agreement_run as agreement_run

    monkeypatch.setattr(agreement_run, "_DEFAULT_OUT_DIR", tmp_path / "default_out")
    labels = _labels_copy(tmp_path, mutate=True)
    assert run(["--labels", str(labels), "--judge", "fake"]) == 0
    capsys.readouterr()
    assert (tmp_path / "default_out" / REPORT_BASENAME).exists()


# --------------------- F-1 payload replay: verdicts never land next to the template


def test_an_out_dir_holding_the_annotation_template_is_refused(tmp_path, capsys) -> None:
    # The mirror of annotation_cli's guard: judge output must never be written
    # into the directory holding the blank template the human fills blind.
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "annotation_template.jsonl").write_text("{}\n", encoding="utf-8")
    labels = _labels_copy(tmp_path, mutate=True)
    assert run(["--labels", str(labels), "--judge", "fake", "--out", str(out_dir)]) == 2
    err = capsys.readouterr().err
    assert "annotation_template.jsonl" in err
    assert "data/annotation/" in err
    assert not (out_dir / REPORT_BASENAME).exists()


def test_the_mixed_dir_refusal_happens_before_any_judge_call(monkeypatch, tmp_path, capsys) -> None:
    # The guard must fire BEFORE the pipeline and BEFORE any API spend.
    client = RecordingClient(DuckResponse(parsed_output=STUB_VERDICT))
    _mock_sdk(monkeypatch, client)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "annotation_instructions.txt").write_text("x\n", encoding="utf-8")
    labels = _labels_copy(tmp_path, mutate=True)
    assert run(["--labels", str(labels), "--judge", "anthropic", "--out", str(out_dir)]) == 2
    capsys.readouterr()
    assert client.messages.calls == []
    assert sorted(p.name for p in out_dir.iterdir()) == ["annotation_instructions.txt"]


# ------------------- F-3 payload replay: re-rolled real runs cannot erase each other


def test_real_judge_reports_are_per_run_and_never_overwrite(monkeypatch, tmp_path, capsys) -> None:
    # The red team's cherry-picking payload: re-run the real judge on the SAME
    # labels into the SAME --out. Before the fix the second run clobbered the
    # first with zero trace; now both runs sit side by side, each stamped.
    client = RecordingClient(DuckResponse(parsed_output=STUB_VERDICT))
    _mock_sdk(monkeypatch, client)
    labels = _real_labels_copy(tmp_path)
    out_dir = tmp_path / "out"
    assert run(["--labels", str(labels), "--judge", "anthropic", "--out", str(out_dir)]) == 0
    first_out = capsys.readouterr().out
    first_files = sorted(out_dir.glob("agreement_run_report.*.json"))
    assert len(first_files) == 1
    first_bytes = first_files[0].read_bytes()
    # The stdout footer names the exact per-run file — the trace is on the face.
    assert first_files[0].name in first_out

    assert run(["--labels", str(labels), "--judge", "anthropic", "--out", str(out_dir)]) == 0
    capsys.readouterr()
    files = sorted(out_dir.glob("agreement_run_report.*.json"))
    assert len(files) == 2  # the discarded run is still on disk
    assert first_files[0].read_bytes() == first_bytes  # and byte-untouched

    payloads = [json.loads(path.read_bytes().decode("utf-8")) for path in files]
    run_ids = {payload["volatile"]["run_id"] for payload in payloads}
    assert len(run_ids) == 2
    for path, payload in zip(files, payloads, strict=True):
        assert payload["volatile"]["generated_at"]
        assert path.name == f"agreement_run_report.{payload['volatile']['run_id']}.json"


def test_the_fixed_basename_is_never_used_by_the_real_judge(monkeypatch, tmp_path, capsys) -> None:
    client = RecordingClient(DuckResponse(parsed_output=STUB_VERDICT))
    _mock_sdk(monkeypatch, client)
    labels = _real_labels_copy(tmp_path)
    out_dir = tmp_path / "out"
    assert run(["--labels", str(labels), "--judge", "anthropic", "--out", str(out_dir)]) == 0
    capsys.readouterr()
    assert not (out_dir / REPORT_BASENAME).exists()


# ---------------- ADR-0006 anti-mix guard: v1 labels never meet the v2 instrument


def _v1_labels_copy(tmp_path: Path, name: str = "human_labels_v1_session.jsonl") -> Path:
    """The historical shape: the fixture's labels rewritten under the FROZEN v1
    taxonomy id (exactly what data/labels/human_labels.jsonl carries)."""
    payloads = _fixture_payloads()
    for payload in payloads:
        payload["taxonomy_id"] = TAXONOMY_V1.taxonomy_id
    return _write_labels(tmp_path, payloads, name)


def test_v1_labels_against_the_v2_run_are_refused_naming_both_ids(tmp_path, capsys) -> None:
    # The historical human labels answer the v1 questionnaire; the run's judge is
    # fingerprinted on v2 — measuring one against the other would be agreement
    # between two different questionnaires (ADR-0003 rule 1 / ADR-0006).
    labels = _v1_labels_copy(tmp_path)
    out_dir = tmp_path / "out"
    assert run(["--labels", str(labels), "--judge", "fake", "--out", str(out_dir)]) == 2
    err = capsys.readouterr().err
    assert TAXONOMY_V1.taxonomy_id in err  # the labels' id, named
    assert TAXONOMY_V2.taxonomy_id in err  # the run's id, named
    assert "make annotate" in err  # the remedy, named
    assert not out_dir.exists() or not list(out_dir.iterdir())  # nothing written


def test_the_taxonomy_guard_fires_before_any_api_call(monkeypatch, tmp_path, capsys) -> None:
    # The refusal must land BEFORE the first judge call: mixing questionnaires must
    # not cost 49 API calls before being detected (the F-1 pre-cost discipline).
    client = RecordingClient(DuckResponse(parsed_output=STUB_VERDICT))
    _mock_sdk(monkeypatch, client)
    labels = _v1_labels_copy(tmp_path)
    out_dir = tmp_path / "out"
    assert run(["--labels", str(labels), "--judge", "anthropic", "--out", str(out_dir)]) == 2
    err = capsys.readouterr().err
    assert client.messages.calls == []  # zero API spend
    assert TAXONOMY_V1.taxonomy_id in err
    assert TAXONOMY_V2.taxonomy_id in err


# ------- R-1 payload replays: loader refusals are clean exit-2, never a traceback


def _mixed_labels_copy(tmp_path: Path, name: str = "mixed_labels.jsonl") -> Path:
    """Red-team payload B: one file alternating v1 and v2 taxonomy ids."""
    payloads = _fixture_payloads()
    for index, payload in enumerate(payloads):
        if index % 2 == 0:
            payload["taxonomy_id"] = TAXONOMY_V1.taxonomy_id
    return _write_labels(tmp_path, payloads, name)


def test_a_mixed_taxonomy_file_is_refused_cleanly_not_a_traceback(tmp_path, capsys) -> None:
    # Red-team payload B replayed: before the fix, load_human_labels' own
    # TaxonomyMismatchError (mixed file) escaped run() as a traceback (exit 1);
    # the guard's try only covered check_labels_match_run_taxonomy. Now the strict
    # load shares the guard's refusal path: typed message, exit 2, nothing written.
    labels = _mixed_labels_copy(tmp_path)
    out_dir = tmp_path / "out"
    assert run(["--labels", str(labels), "--judge", "fake", "--out", str(out_dir)]) == 2
    err = capsys.readouterr().err
    assert "mixed taxonomy_id values" in err
    assert TAXONOMY_V1.taxonomy_id in err
    assert TAXONOMY_V2.taxonomy_id in err
    assert not out_dir.exists()  # --out never created on refusal


def test_the_mixed_file_refusal_costs_zero_api_calls(monkeypatch, tmp_path, capsys) -> None:
    # The B payload's cost claim, pinned: with --judge anthropic the refusal must
    # land before the first SDK call (the F-1 pre-cost discipline).
    client = RecordingClient(DuckResponse(parsed_output=STUB_VERDICT))
    _mock_sdk(monkeypatch, client)
    labels = _mixed_labels_copy(tmp_path)
    out_dir = tmp_path / "out"
    assert run(["--labels", str(labels), "--judge", "anthropic", "--out", str(out_dir)]) == 2
    assert "mixed taxonomy_id values" in capsys.readouterr().err
    assert client.messages.calls == []  # zero API spend
    assert not out_dir.exists()


def test_a_label_line_missing_taxonomy_id_is_refused_cleanly(tmp_path, capsys) -> None:
    # Red-team payload D replayed: a line without taxonomy_id used to escape as a
    # HumanLabelFormatError traceback; now it is a clean refusal naming the line.
    payloads = _fixture_payloads()
    del payloads[0]["taxonomy_id"]
    labels = _write_labels(tmp_path, payloads, "no_taxonomy_labels.jsonl")
    out_dir = tmp_path / "out"
    assert run(["--labels", str(labels), "--judge", "fake", "--out", str(out_dir)]) == 2
    err = capsys.readouterr().err
    assert "no_taxonomy_labels.jsonl line 1" in err  # the 1-based line, named
    assert "invalid or unfilled human label" in err
    assert not out_dir.exists()


def test_a_duplicated_record_id_is_refused_cleanly(tmp_path, capsys) -> None:
    # The third member of the loader's typed-refusal family rides the same path.
    payloads = _fixture_payloads()
    payloads.append(dict(payloads[0]))
    labels = _write_labels(tmp_path, payloads, "duplicated_labels.jsonl")
    out_dir = tmp_path / "out"
    assert run(["--labels", str(labels), "--judge", "fake", "--out", str(out_dir)]) == 2
    err = capsys.readouterr().err
    assert "labeled more than once" in err
    assert payloads[0]["record_id"] in err
    assert not out_dir.exists()


def _label_for(taxonomy_id: str) -> HumanLabel:
    return HumanLabel(
        record_id="rec-0000000000000000",
        taxonomy_id=taxonomy_id,
        task_type="factual_query",
        outcome="correct",
        annotator="annotator-a",
    )


def test_the_guard_is_symmetric_in_both_directions() -> None:
    # v1 labels vs v2 judge AND v2 labels vs v1 judge both refuse, each naming both
    # ids; matching ids pass. (The CLI only ever runs the v2 side; the symmetry is
    # the function's contract so a future default flip keeps the guard.)
    from conftest import make_stub_fingerprint

    v1_fingerprint = make_stub_fingerprint()  # conftest stubs are v1-fingerprinted
    assert v1_fingerprint.taxonomy_id == TAXONOMY_V1.taxonomy_id
    with pytest.raises(TaxonomyMismatchError) as excinfo:
        check_labels_match_run_taxonomy(
            [_label_for(TAXONOMY_V2.taxonomy_id)], v1_fingerprint, labels_basename="x.jsonl"
        )
    assert TAXONOMY_V1.taxonomy_id in str(excinfo.value)
    assert TAXONOMY_V2.taxonomy_id in str(excinfo.value)

    v2_fingerprint = v1_fingerprint.model_copy(update={"taxonomy_id": TAXONOMY_V2.taxonomy_id})
    with pytest.raises(TaxonomyMismatchError) as excinfo:
        check_labels_match_run_taxonomy(
            [_label_for(TAXONOMY_V1.taxonomy_id)], v2_fingerprint, labels_basename="x.jsonl"
        )
    assert TAXONOMY_V1.taxonomy_id in str(excinfo.value)
    assert TAXONOMY_V2.taxonomy_id in str(excinfo.value)

    # Matching questionnaires pass on both versions — the guard blocks mixing only.
    check_labels_match_run_taxonomy(
        [_label_for(TAXONOMY_V1.taxonomy_id)], v1_fingerprint, labels_basename="x.jsonl"
    )
    check_labels_match_run_taxonomy(
        [_label_for(TAXONOMY_V2.taxonomy_id)], v2_fingerprint, labels_basename="x.jsonl"
    )
