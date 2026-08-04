"""TAXONOMY_V1/TAXONOMY_V2 mirror the enums member-for-member; a tampered taxonomy
refuses to exist (ADR-0003 rule 1). The mirror test is the drift guard: κ classes
(definitions) and schema enums (JudgeVerdict fields) must never diverge silently.
Both version ids are PINNED (ADR-0006): v1 is a frozen historical artifact the
committed run report references; v2 is the pipeline default.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from evalgen.contracts import (
    TAXONOMY_V1,
    TAXONOMY_V2,
    JudgeConfidence,
    LabelTaxonomy,
    OutcomeLabel,
    TaskTypeLabel,
    TaxonomyAxis,
    TaxonomyClass,
    derive_taxonomy_id,
)

#: The id the committed real-session report and the historical human labels reference
#: (docs/reports/agreement_run_report.20260804T002205Z-7c2b30d6.json) — FROZEN. If this
#: pin ever breaks, the repo's published provenance chain is broken with it.
V1_PINNED_ID = "tax-d9ca3b87b403"
#: The v2 (ADR-0006 bounded-plausibility convention) id — content-derived, pinned so
#: any silent definition drift fails here before it reaches a fingerprint.
V2_PINNED_ID = "tax-d8ba44dd70c7"


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


# ------------------------------------------------- ADR-0006: v2 + the frozen v1


def test_v1_id_is_pinned_and_frozen_forever() -> None:
    # The committed run report (kappa=0.263158) and the historical human labels
    # reference this exact id — TAXONOMY_V1 must stay importable and byte-stable.
    assert TAXONOMY_V1.taxonomy_id == V1_PINNED_ID
    assert TAXONOMY_V1.version == "v1"


def test_v2_id_is_pinned() -> None:
    assert TAXONOMY_V2.taxonomy_id == V2_PINNED_ID
    assert TAXONOMY_V2.taxonomy_id == derive_taxonomy_id(TAXONOMY_V2.axes)
    assert TAXONOMY_V2.version == "v2"
    assert TAXONOMY_V2.name == TAXONOMY_V1.name


def test_v1_and_v2_are_different_questionnaires() -> None:
    # Content-derived ids make the version change structurally visible everywhere
    # a taxonomy_id travels (labels, fingerprints, reports, exports).
    assert TAXONOMY_V1.taxonomy_id != TAXONOMY_V2.taxonomy_id


def test_v2_axes_mirror_the_enums_member_for_member() -> None:
    # Same drift guard as v1: kappa classes and schema enums never diverge — and
    # ADR-0006 adds/removes NO class (Phase 4 per-class support depends on it).
    assert [a.name for a in TAXONOMY_V2.axes] == ["task_type", "outcome"]
    assert [c.name for c in TAXONOMY_V2.axes[0].classes] == [m.value for m in TaskTypeLabel]
    assert [c.name for c in TAXONOMY_V2.axes[1].classes] == [m.value for m in OutcomeLabel]


def test_v2_task_type_axis_is_v1s_verbatim() -> None:
    # kappa_task_type = 0.861 measured no problem on that axis — v2 changes nothing there.
    assert TAXONOMY_V2.axes[0] == TAXONOMY_V1.axes[0]


def test_v2_amends_exactly_the_three_outcome_definitions() -> None:
    v1_outcome, v2_outcome = TAXONOMY_V1.axes[1], TAXONOMY_V2.axes[1]
    assert v2_outcome.question == v1_outcome.question
    changed = {
        v2.name
        for v1, v2 in zip(v1_outcome.classes, v2_outcome.classes, strict=True)
        if v1.definition != v2.definition
    }
    # correct / partially_correct / unjudgeable carry the live-claim convention;
    # incorrect is byte-identical to v1 (ADR-0006).
    assert changed == {"correct", "partially_correct", "unjudgeable"}


def test_v2_carries_the_live_claim_convention_and_v1_does_not() -> None:
    # The two decision phrases of ADR-0006, present in v2, absent from v1 — the
    # drift guard works in both directions.
    def definition(taxonomy: LabelTaxonomy, class_name: str) -> str:
        return next(c.definition for c in taxonomy.axes[1].classes if c.name == class_name)

    assert "grade the answer AS A RESPONSE" in definition(TAXONOMY_V2, "correct")
    assert "ONLY when the INPUT is ambiguous or the exchange is incomplete" in definition(
        TAXONOMY_V2, "unjudgeable"
    )
    assert "never merely because the claim is live" in definition(TAXONOMY_V2, "unjudgeable")
    assert "never unjudgeable" in definition(TAXONOMY_V2, "partially_correct")
    for class_name in ("correct", "partially_correct", "unjudgeable"):
        assert "live" not in definition(TAXONOMY_V1, class_name)


def test_v2_roundtrips_and_revalidates() -> None:
    restored = LabelTaxonomy.model_validate_json(TAXONOMY_V2.model_dump_json())
    assert restored == TAXONOMY_V2


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
