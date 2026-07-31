"""TAXONOMY_V1 mirrors the enums member-for-member; a tampered taxonomy refuses to
exist (ADR-0003 rule 1). The mirror test is the drift guard: κ classes (definitions)
and schema enums (JudgeVerdict fields) must never diverge silently.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from evalgen.contracts import (
    TAXONOMY_V1,
    JudgeConfidence,
    LabelTaxonomy,
    OutcomeLabel,
    TaskTypeLabel,
    TaxonomyAxis,
    TaxonomyClass,
    derive_taxonomy_id,
)


def axis(
    name: str = "quality",
    question: str = "Is it good?",
    class_names: tuple[str, ...] = ("good", "bad"),
) -> TaxonomyAxis:
    return TaxonomyAxis(
        name=name,
        question=question,
        classes=tuple(TaxonomyClass(name=n, definition=f"definition of {n}") for n in class_names),
    )


# ------------------------------------------------------ the enum <-> V1 mirror


def test_v1_axes_mirror_the_enums_member_for_member() -> None:
    assert [a.name for a in TAXONOMY_V1.axes] == ["task_type", "outcome"]
    assert [c.name for c in TAXONOMY_V1.axes[0].classes] == [m.value for m in TaskTypeLabel]
    assert [c.name for c in TAXONOMY_V1.axes[1].classes] == [m.value for m in OutcomeLabel]


def test_confidence_enum_is_the_three_level_scale() -> None:
    assert [m.value for m in JudgeConfidence] == ["high", "medium", "low"]


def test_v1_id_is_content_derived_and_self_verified() -> None:
    assert TAXONOMY_V1.taxonomy_id == derive_taxonomy_id(TAXONOMY_V1.axes)
    assert TAXONOMY_V1.taxonomy_id.startswith("tax-")
    assert len(TAXONOMY_V1.taxonomy_id) == len("tax-") + 12


def test_v1_roundtrips_and_revalidates() -> None:
    restored = LabelTaxonomy.model_validate_json(TAXONOMY_V1.model_dump_json())
    assert restored == TAXONOMY_V1


# -------------------------------------------------------------- id sensitivity


def test_id_changes_when_a_definition_changes() -> None:
    a = axis()
    b = TaxonomyAxis(
        name=a.name,
        question=a.question,
        classes=(a.classes[0], TaxonomyClass(name="bad", definition="a DIFFERENT definition")),
    )
    assert derive_taxonomy_id([a]) != derive_taxonomy_id([b])


def test_id_changes_when_class_order_changes() -> None:
    # Reordered classes are a different questionnaire (position biases annotators).
    a = axis(class_names=("good", "bad"))
    b = axis(class_names=("bad", "good"))
    assert derive_taxonomy_id([a]) != derive_taxonomy_id([b])


# ---------------------------------------------------------------- refuse cases


def test_tampered_taxonomy_id_refuses() -> None:
    with pytest.raises(ValidationError, match="content-derived"):
        LabelTaxonomy(taxonomy_id="tax-000000000000", name="t", version="v1", axes=(axis(),))


def test_duplicate_axis_names_refuse() -> None:
    with pytest.raises(ValidationError, match="axis names"):
        axes = (axis(name="same"), axis(name="same", class_names=("x", "y")))
        LabelTaxonomy(taxonomy_id=derive_taxonomy_id(axes), name="t", version="v1", axes=axes)


def test_duplicate_class_names_within_an_axis_refuse() -> None:
    with pytest.raises(ValidationError, match="duplicate class names"):
        axis(class_names=("good", "good"))


def test_single_class_axis_refuses() -> None:
    # A one-class axis is not a labeling question (min_length=2).
    with pytest.raises(ValidationError):
        axis(class_names=("only",))
