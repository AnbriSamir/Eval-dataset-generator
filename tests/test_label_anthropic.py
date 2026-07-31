"""AnthropicJudge: pure mappers offline + call structure via an injected recorder.

STRICTLY offline: no network, no API key. Responses are duck-typed stand-ins (plain
dataclasses); SDK exceptions are constructed locally (object construction only); the
recording client captures the exact kwargs the CLAUDE.md §2 rules mandate — including
the ABSENCE of temperature/top_p/top_k/budget_tokens.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anthropic
import httpx
import pytest
from pydantic import ValidationError

from conftest import STUB_VERDICT
from evalgen.contracts import TAXONOMY_V1, JudgeVerdict
from evalgen.ingest import sanitize_text
from evalgen.label import FakeJudge, load_few_shots
from evalgen.label.anthropic_judge import (
    _MAX_OUTPUT_TOKENS,
    AnthropicJudge,
    _error_from_exception,
    _judgment_from_response,
)
from evalgen.label.errors import JudgeAPIError, JudgeParseError, JudgeRefusalError
from evalgen.label.prompt import render_system_prompt, render_user_message

FEWSHOTS = Path(__file__).resolve().parents[1] / "data" / "fewshots" / "judge_v1.jsonl"


@dataclass
class DuckStopDetails:
    explanation: str | None = None


@dataclass
class DuckResponse:
    """Duck-typed stand-in for a messages.parse response — no SDK mocks."""

    stop_reason: str | None = "end_turn"
    model: str = "claude-opus-4-8-served"
    parsed_output: Any = None
    stop_details: Any = None


class RecordingMessages:
    def __init__(self, result: Any) -> None:
        self._result = result
        self.calls: list[dict[str, Any]] = []

    def parse(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class RecordingClient:
    """Injected in place of anthropic.Anthropic — records the exact call structure."""

    def __init__(self, result: Any) -> None:
        self.messages = RecordingMessages(result)


def make_judge(result: Any) -> tuple[AnthropicJudge, RecordingClient]:
    client = RecordingClient(result)
    judge = AnthropicJudge(
        model="claude-opus-4-8",
        taxonomy=TAXONOMY_V1,
        few_shots=load_few_shots(FEWSHOTS, sanitizer=sanitize_text),
        client=client,  # type: ignore[arg-type]
    )
    return judge, client


# ------------------------------------------------------------- the pure mappers


def test_refusal_beats_a_present_parsed_output() -> None:
    # ORDER MATTERS: whatever a refused response carries is not a label.
    response = DuckResponse(stop_reason="refusal", parsed_output=STUB_VERDICT)
    with pytest.raises(JudgeRefusalError):
        _judgment_from_response(response)


def test_refusal_detail_carries_the_explanation_when_present() -> None:
    response = DuckResponse(
        stop_reason="refusal", stop_details=DuckStopDetails(explanation="policy category")
    )
    with pytest.raises(JudgeRefusalError, match="policy category"):
        _judgment_from_response(response)


def test_max_tokens_truncation_is_a_parse_failure() -> None:
    with pytest.raises(JudgeParseError, match="max_tokens"):
        _judgment_from_response(DuckResponse(stop_reason="max_tokens"))


def test_missing_parsed_output_is_a_parse_failure() -> None:
    with pytest.raises(JudgeParseError, match="parsed_output"):
        _judgment_from_response(DuckResponse(stop_reason="end_turn", parsed_output=None))


def test_happy_response_yields_the_served_model_id() -> None:
    judgment = _judgment_from_response(
        DuckResponse(stop_reason="end_turn", parsed_output=STUB_VERDICT)
    )
    assert judgment.verdict == STUB_VERDICT
    # response.model (the SERVING model), never the requested-model echo.
    assert judgment.model_id == "claude-opus-4-8-served"


def _api_status_error() -> anthropic.APIStatusError:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return anthropic.APIStatusError(
        "overloaded", response=httpx.Response(529, request=request), body=None
    )


def test_api_status_error_maps_to_judge_api_error() -> None:
    mapped = _error_from_exception(_api_status_error())
    assert isinstance(mapped, JudgeAPIError)
    assert "529" in mapped.detail


def test_connection_error_maps_to_judge_api_error() -> None:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    mapped = _error_from_exception(anthropic.APIConnectionError(request=request))
    assert isinstance(mapped, JudgeAPIError)


def test_validation_error_maps_to_judge_parse_error() -> None:
    # The client-side constraint check (e.g. rationale length) failing = parse failure.
    with pytest.raises(ValidationError) as excinfo:
        JudgeVerdict.model_validate({})
    mapped = _error_from_exception(excinfo.value)
    assert isinstance(mapped, JudgeParseError)


def test_unknown_exceptions_are_reraised_never_swallowed() -> None:
    with pytest.raises(ValueError, match="our own bug"):
        _error_from_exception(ValueError("our own bug"))


# --------------------------------------------- call structure (recording client)


def test_call_structure_honors_the_sdk_rules_letter_for_letter() -> None:
    judge, client = make_judge(DuckResponse(parsed_output=STUB_VERDICT))
    judge.judge("the input", "the output")

    assert len(client.messages.calls) == 1
    kwargs = client.messages.calls[0]
    assert kwargs["model"] == "claude-opus-4-8"
    assert kwargs["max_tokens"] == _MAX_OUTPUT_TOKENS
    assert kwargs["output_format"] is JudgeVerdict
    assert kwargs["thinking"] == {"type": "adaptive"}
    assert kwargs["output_config"] == {"effort": "high"}
    assert kwargs["messages"] == [
        {"role": "user", "content": render_user_message("the input", "the output")}
    ]
    system = kwargs["system"]
    assert system == [
        {
            "type": "text",
            "text": render_system_prompt(
                TAXONOMY_V1, load_few_shots(FEWSHOTS, sanitizer=sanitize_text)
            ),
            "cache_control": {"type": "ephemeral"},
        }
    ]
    # Forbidden on 4.7/4.8 (400) and forbidden by CLAUDE.md §2 regardless:
    for banned in ("temperature", "top_p", "top_k", "budget_tokens"):
        assert banned not in kwargs
    assert "budget_tokens" not in kwargs["thinking"]


def test_refusal_response_surfaces_as_the_typed_error() -> None:
    judge, _ = make_judge(DuckResponse(stop_reason="refusal"))
    with pytest.raises(JudgeRefusalError):
        judge.judge("in", "out")


def test_sdk_exception_in_the_call_surfaces_as_judge_api_error() -> None:
    judge, _ = make_judge(_api_status_error())
    with pytest.raises(JudgeAPIError):
        judge.judge("in", "out")


def test_non_sdk_exception_in_the_call_propagates() -> None:
    judge, _ = make_judge(ValueError("client bug"))
    with pytest.raises(ValueError, match="client bug"):
        judge.judge("in", "out")


def test_fingerprint_matches_the_fake_judge_prompt_hash() -> None:
    # Same taxonomy + few-shots => same rendered prompt => same hash: the offline demo
    # golden thereby pins the PRODUCTION prompt template (ADR-0003 rule 5).
    shots = load_few_shots(FEWSHOTS, sanitizer=sanitize_text)
    real, _ = make_judge(DuckResponse(parsed_output=STUB_VERDICT))
    fake = FakeJudge(taxonomy=TAXONOMY_V1, few_shots=shots)
    assert real.fingerprint.judge_name == "anthropic"
    assert real.fingerprint.model_id == "claude-opus-4-8"  # the REQUESTED model
    assert real.fingerprint.prompt_sha256 == fake.fingerprint.prompt_sha256
    assert real.fingerprint.few_shot_ids == fake.fingerprint.few_shot_ids
    assert real.fingerprint.few_shot_content_hashes == fake.fingerprint.few_shot_content_hashes
