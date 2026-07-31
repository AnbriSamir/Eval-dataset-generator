"""The closed label taxonomy: two unconditional axes, enums as schema (ADR-0003 rule 1).

Taxonomy size is a *statistics* decision, not an ontology exercise: per-class κ needs
per-class support, and at n = 30–50 human labels every extra class (and especially every
conditional axis) divides that support until the κ table is unreportable. So v1 is two
closed axes answered for EVERY record — ``task_type`` (what kind of exchange) and
``outcome`` (does the output address the input) — plus a confidence and a free rationale
that ride along for the disagreement drill-down and never enter a κ.

Three load-bearing choices live here:

1. **The StrEnums are the source of truth.** ``JudgeVerdict`` (contracts/labeling.py)
   compiles them into the ``output_format`` JSON schema, so an out-of-taxonomy label is
   unrepresentable at the API boundary. ``LabelTaxonomy`` carries the human-readable
   definitions; a test pins that its classes mirror the enums member-for-member —
   κ classes and schema enums must never drift apart silently.
2. **One questionnaire, two annotators.** The judge prompt AND the Phase 4 human-labeler
   instructions render from the same ``TAXONOMY_V1`` definitions — otherwise κ measures
   instruction drift, not agreement.
3. **The taxonomy id is content-derived and self-verified** (house style: ``rec-``,
   ``cl-``): a tampered taxonomy refuses to exist, and Phase 4 refuses to join judge and
   human labels across different ``taxonomy_id``s — agreement between different
   questionnaires is not agreement.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from evalgen.contracts.records import CANONICAL_SEP

_TAXONOMY_ID_PREFIX = "tax-"
_TAXONOMY_ID_HEX_LEN = 12


class TaskTypeLabel(StrEnum):
    """What kind of exchange this is — applies to every record (unconditional axis)."""

    FACTUAL_QUERY = "factual_query"
    PROCEDURAL_REQUEST = "procedural_request"
    TROUBLESHOOTING = "troubleshooting"
    PLANNING_OR_REASONING = "planning_or_reasoning"
    OTHER = "other"


class OutcomeLabel(StrEnum):
    """Does the output correctly address the input — the headline κ axis."""

    CORRECT = "correct"
    PARTIALLY_CORRECT = "partially_correct"
    INCORRECT = "incorrect"
    #: A LABEL, not an error: the judge succeeded at judging that judgment is impossible
    #: from the exchange alone — itself information Phase 4 measures agreement on.
    UNJUDGEABLE = "unjudgeable"


class JudgeConfidence(StrEnum):
    """Self-reported confidence — drill-down signal only, NEVER a κ filter (that would
    be self-grading, the exact κ-gaming CLAUDE.md forbids)."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class TaxonomyClass(BaseModel):
    """One labelable class: its name and the definition text shown VERBATIM to the
    judge and (Phase 4) to human labelers."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    definition: str = Field(min_length=1)


class TaxonomyAxis(BaseModel):
    """One labeling question and its closed set of answers."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    #: The question the labeler (judge or human) answers on this axis.
    question: str = Field(min_length=1)
    classes: tuple[TaxonomyClass, ...] = Field(min_length=2)

    @model_validator(mode="after")
    def _class_names_must_be_unique(self) -> TaxonomyAxis:
        names = [c.name for c in self.classes]
        if len(set(names)) != len(names):
            raise ValueError(
                f"axis {self.name!r} has duplicate class names — classes must be unique"
            )
        return self


def derive_taxonomy_id(axes: Sequence[TaxonomyAxis]) -> str:
    """``"tax-" + sha256(␟-joined axis name/question and class name/definition)[:12]``.

    Axes and classes in DECLARED order — reordering classes is a different questionnaire
    (position biases annotators) and must produce a different id. The separator prevents
    boundary-shift collisions (ADR-0001 rule 1 precedent).
    """
    parts: list[str] = []
    for axis in axes:
        parts.append(axis.name)
        parts.append(axis.question)
        for cls in axis.classes:
            parts.append(cls.name)
            parts.append(cls.definition)
    digest = hashlib.sha256(CANONICAL_SEP.join(parts).encode("utf-8")).hexdigest()
    return _TAXONOMY_ID_PREFIX + digest[:_TAXONOMY_ID_HEX_LEN]


class LabelTaxonomy(BaseModel):
    """A versioned, content-addressed questionnaire; a tampered one refuses to exist."""

    model_config = ConfigDict(frozen=True)

    taxonomy_id: str
    name: str = Field(min_length=1)
    #: Human-readable version tag ("v1") — identity lives in ``taxonomy_id``.
    version: str = Field(min_length=1)
    axes: tuple[TaxonomyAxis, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _axes_unique_and_id_must_match(self) -> LabelTaxonomy:
        names = [a.name for a in self.axes]
        if len(set(names)) != len(names):
            raise ValueError("axis names must be unique")
        expected = derive_taxonomy_id(self.axes)
        if self.taxonomy_id != expected:
            raise ValueError(
                f"taxonomy_id {self.taxonomy_id!r} does not match content "
                f"(expected {expected!r}) — taxonomy ids are content-derived (ADR-0003 rule 1)"
            )
        return self


#: v1 axes — class names mirror the enums member-for-member (names AND order, pinned by
#: a test). Definitions are decision-oriented and name the boundary cases; they are the
#: single questionnaire both the judge prompt and Phase 4's human instructions render.
_V1_AXES: tuple[TaxonomyAxis, ...] = (
    TaxonomyAxis(
        name="task_type",
        question="What kind of exchange is this?",
        classes=(
            TaxonomyClass(
                name=TaskTypeLabel.FACTUAL_QUERY.value,
                definition=(
                    "The input asks for a fact, status, or current value — opening hours, "
                    "prices, live traffic, weather. Choose this when the expected answer is "
                    "a piece of information, not instructions or a diagnosis."
                ),
            ),
            TaxonomyClass(
                name=TaskTypeLabel.PROCEDURAL_REQUEST.value,
                definition=(
                    "The input asks how to do something — activate a badge, order or cancel "
                    "a service, update account details. Choose this when the expected answer "
                    "is instructions or steps."
                ),
            ),
            TaxonomyClass(
                name=TaskTypeLabel.TROUBLESHOOTING.value,
                definition=(
                    "The input reports an error, failure, or incident and asks for help "
                    "resolving it. Choose this when something is broken or misbehaving, even "
                    "if the fix turns out to be a procedure."
                ),
            ),
            TaxonomyClass(
                name=TaskTypeLabel.PLANNING_OR_REASONING.value,
                definition=(
                    "The exchange is a multi-step plan, an analysis, or a verdict — "
                    "typically an orchestrator plan/execute/verdict span. Choose this when "
                    "the output's value lies in reasoning or decision-making, not in a "
                    "single fact or procedure."
                ),
            ),
            TaxonomyClass(
                name=TaskTypeLabel.OTHER.value,
                definition=(
                    "None of the other classes fits. Choose this rather than forcing a bad "
                    "fit — it is a closed-set escape valve, not a default."
                ),
            ),
        ),
    ),
    TaxonomyAxis(
        name="outcome",
        question="Does the output correctly address the input?",
        classes=(
            TaxonomyClass(
                name=OutcomeLabel.CORRECT.value,
                definition=(
                    "The output fully addresses the input with no visible factual or "
                    "logical error. Stylistic differences do not matter."
                ),
            ),
            TaxonomyClass(
                name=OutcomeLabel.PARTIALLY_CORRECT.value,
                definition=(
                    "The output addresses the input but is incomplete, or contains a minor "
                    "peripheral error. Any hard factual error on the asked question itself "
                    "makes the outcome incorrect, not partially_correct."
                ),
            ),
            TaxonomyClass(
                name=OutcomeLabel.INCORRECT.value,
                definition=(
                    "The output fails the task or contains a factual or logical error on "
                    "the very thing that was asked."
                ),
            ),
            TaxonomyClass(
                name=OutcomeLabel.UNJUDGEABLE.value,
                definition=(
                    "No grading is possible from the exchange alone — the input is "
                    "ambiguous or the answer depends on missing context. Choose this only "
                    "when judging is impossible, not when it is merely hard; it is a label, "
                    "not an error."
                ),
            ),
        ),
    ),
)

#: THE shared taxonomy artifact — judge and human labelers answer these exact questions
#: with these exact definitions (ADR-0003 decision driver: one questionnaire).
TAXONOMY_V1 = LabelTaxonomy(
    taxonomy_id=derive_taxonomy_id(_V1_AXES),
    name="evalgen-label-taxonomy",
    version="v1",
    axes=_V1_AXES,
)
