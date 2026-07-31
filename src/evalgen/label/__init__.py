"""LLM judge via Anthropic SDK structured output, label taxonomy, few-shot store
(leakage-guarded). Blind to human ground truth by construction (ADR-0003 rule 9): this
package imports only ``contracts``, never ``validate``/``export``/mining modules, and
never opens the ground-truth store — all pinned by architecture tests.

``AnthropicJudge`` is deliberately NOT exported: the real judge is reached only by an
explicit deep import (``from evalgen.label.anthropic_judge import AnthropicJudge``) at
the composition layer, so no test or demo path can construct it by accident — and
importing this package never imports the SDK.
"""

from evalgen.label.engine import run_labeling
from evalgen.label.fake import FAKE_JUDGE_MODEL_ID, FakeJudge
from evalgen.label.fewshots import load_few_shots

__all__ = [
    "FAKE_JUDGE_MODEL_ID",
    "FakeJudge",
    "load_few_shots",
    "run_labeling",
]
