"""The real judge: a thin shell over ``messages.parse`` + pure mappers (ADR-0003 rule 4).

This is the ONLY module in the repo that imports ``anthropic`` (pinned by a grep test),
and it is deliberately NOT re-exported from ``label/__init__`` — the real judge is
reached only by an explicit deep import at the composition layer, so no test or demo
path can construct it by accident.

SDK rules honored letter-for-letter (CLAUDE.md §2): ``messages.parse(output_format=
JudgeVerdict) → .parsed_output``, ``thinking={"type": "adaptive"}``, ``output_config=
{"effort": "high"}``, NO ``temperature``/``top_p``/``top_k``, NO ``budget_tokens``
(400 on these models), never raw ``requests``/``httpx``. The SDK's built-in retries
(``max_retries`` default 2) are the only retry layer — the engine makes ONE attempt per
record; re-rolling a judge until it answers is sampling bias (ADR-0003 consequences).

The response→``Judgment`` and exception→``JudgeError`` mappings are module-level PURE
functions tested offline with duck-typed stand-ins; the class itself is a thin shell
whose wiring is tested with an injected recording client (no network, no key).
"""

from __future__ import annotations

from typing import Any

import anthropic
import pydantic

from evalgen.contracts import (
    FewShotExample,
    JudgeFingerprint,
    JudgeVerdict,
    Judgment,
    LabelTaxonomy,
)
from evalgen.label.errors import JudgeAPIError, JudgeError, JudgeParseError, JudgeRefusalError
from evalgen.label.prompt import prompt_sha256, render_system_prompt, render_user_message

#: Module constant, deliberately NOT a config knob (ADR-0002 rule 6 philosophy:
#: unmeasured knobs are lies). Generous headroom for adaptive thinking + the verdict.
_MAX_OUTPUT_TOKENS = 16_000


class AnthropicJudge:
    """LLM judge over the official Anthropic SDK, blind by construction.

    ``model`` is passed EXPLICITLY (composition reads ``settings.judge_model`` —
    ``label/`` imports no config); ``client`` is injectable for composition and tests.
    """

    def __init__(
        self,
        *,
        model: str,
        taxonomy: LabelTaxonomy,
        few_shots: tuple[FewShotExample, ...] = (),
        client: anthropic.Anthropic | None = None,
    ) -> None:
        self._model = model
        self._system_prompt = render_system_prompt(taxonomy, few_shots)
        self._client = client if client is not None else anthropic.Anthropic()
        self._fingerprint = JudgeFingerprint(
            judge_name="anthropic",
            model_id=model,
            taxonomy_id=taxonomy.taxonomy_id,
            prompt_sha256=prompt_sha256(self._system_prompt),
            few_shot_ids=tuple(sorted(s.few_shot_id for s in few_shots)),
            few_shot_content_hashes=tuple(sorted(s.content_hash for s in few_shots)),
        )

    @property
    def fingerprint(self) -> JudgeFingerprint:
        return self._fingerprint

    def judge(self, input_text: str, output_text: str) -> Judgment:
        try:
            response = self._client.messages.parse(
                model=self._model,
                max_tokens=_MAX_OUTPUT_TOKENS,
                # cache_control: the system prompt is constant per run → cacheable
                # across the ≤ max_labels_per_run calls (below the model's minimum
                # cacheable prefix it silently doesn't cache — harmless).
                system=[
                    {
                        "type": "text",
                        "text": self._system_prompt,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[
                    {"role": "user", "content": render_user_message(input_text, output_text)}
                ],
                thinking={"type": "adaptive"},
                output_config={"effort": "high"},
                output_format=JudgeVerdict,
            )
        except Exception as exc:  # noqa: BLE001 — immediately re-typed below
            raise _error_from_exception(exc) from exc
        return _judgment_from_response(response)


def _judgment_from_response(response: Any) -> Judgment:
    """Map a (duck-typed) parse response to a ``Judgment`` or a typed error.

    ORDER MATTERS: ``stop_reason`` first — a refusal beats a present ``parsed_output``
    (whatever a refused response carries is not a label), and ``max_tokens`` means the
    verdict was truncated before it could be schema-complete.
    """
    stop_reason = getattr(response, "stop_reason", None)
    if stop_reason == "refusal":
        detail = "model declined to judge (stop_reason=refusal)"
        stop_details = getattr(response, "stop_details", None)
        explanation = getattr(stop_details, "explanation", None)
        if explanation:
            detail = f"model declined to judge: {explanation}"
        raise JudgeRefusalError(detail)
    if stop_reason == "max_tokens":
        raise JudgeParseError("output truncated at max_tokens before a complete verdict")
    parsed = getattr(response, "parsed_output", None)
    if parsed is None:
        raise JudgeParseError("response carries no parsed_output — schema parse failed")
    return Judgment(verdict=parsed, model_id=response.model)


def _error_from_exception(exc: Exception) -> JudgeError:
    """Map SDK/validation exceptions to the typed hierarchy; re-raise anything else.

    Our own bugs are never laundered into labeling statistics — only the SDK's typed
    failures and the client-side schema check become report entries.
    """
    if isinstance(exc, anthropic.APIStatusError):
        return JudgeAPIError(f"{type(exc).__name__}: HTTP {exc.status_code}")
    if isinstance(exc, anthropic.APIConnectionError):
        return JudgeAPIError(f"{type(exc).__name__}: connection to the API failed")
    if isinstance(exc, pydantic.ValidationError):
        return JudgeParseError(f"schema-invalid verdict: {exc.error_count()} validation error(s)")
    raise exc
