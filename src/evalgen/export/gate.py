"""The κ export gate: five named checks, pure, letter-for-letter ADR-0003/0004
(ADR-0005 rule 2).

The pass/fail arithmetic lives in ``contracts.export.expected_gate_facts`` — the
SAME derivation the ``ExportManifest`` validator re-runs, so the gate and the
manifest can never disagree. This module attaches the deterministic, path-free
``detail`` strings and enforces the override scope at the seam (check 5 only,
with a clear error rather than a downstream ``ValidationError``).

Knobs are injected by the composition layer, never imported from config (the
``validate/`` precedent).
"""

from __future__ import annotations

from evalgen.contracts import (
    AgreementReport,
    ExportGateDecision,
    ExportGateOverride,
    ExportGateVerdict,
    GateCheck,
    GateCheckName,
    JudgeFingerprint,
    LabelingReport,
)
from evalgen.contracts.export import GateFacts, expected_gate_facts
from evalgen.export.errors import ExportInputError

#: Fingerprint fields compared in declaration order — the FIRST divergence is named.
_FINGERPRINT_FIELDS = (
    "judge_name",
    "model_id",
    "taxonomy_id",
    "prompt_sha256",
    "few_shot_ids",
    "few_shot_content_hashes",
)


def _first_fingerprint_divergence(a: JudgeFingerprint, b: JudgeFingerprint) -> str:
    for field_name in _FINGERPRINT_FIELDS:
        if getattr(a, field_name) != getattr(b, field_name):
            return field_name
    return "unknown"  # unreachable when a != b (all fields equal ⟹ frozen models equal)


def _check_details(
    agreement: AgreementReport,
    labeling_fingerprint: JudgeFingerprint,
    facts: GateFacts,
    min_export_kappa: float,
) -> tuple[str, str, str, str, str]:
    """One deterministic, single-line, path-free detail per check (declared order)."""
    n_matched = agreement.accounting.n_matched
    ready, status_ok, binding, bound, threshold = facts.passed

    if ready:
        ready_detail = f"n_matched={n_matched} >= min_human_labels={agreement.min_human_labels}"
    else:
        ready_detail = (
            f"n_matched={n_matched} < min_human_labels={agreement.min_human_labels} — "
            "headline not reportable"
        )

    headline = agreement.headline
    if headline is None:
        status_detail = "no headline (not headline_ready)"
    elif status_ok:
        status_detail = "ok"
    else:
        status_detail = (
            f"status={headline.status.value} — no defined kappa to gate on "
            "(ADR-0004 amendment (d))"
        )

    if binding:
        binding_detail = "agreement fingerprint == labeling fingerprint"
    else:
        divergence = _first_fingerprint_divergence(agreement.judge, labeling_fingerprint)
        binding_detail = (
            f"agreement fingerprint != labeling fingerprint — first differing field: "
            f"{divergence}"
        )

    if bound:
        assert agreement.human_labels_sha256 is not None  # bound ⟹ present
        bound_detail = f"human_labels_sha256={agreement.human_labels_sha256[:12]}…"
    else:
        bound_detail = (
            "human_labels_sha256 unrecorded — export-grade kappa must bind to exact "
            "ground-truth bytes"
        )

    if facts.kappa is None:
        threshold_detail = "no defined headline kappa"
    elif threshold:
        threshold_detail = f"kappa={facts.kappa!r} >= min_export_kappa={min_export_kappa!r}"
    else:
        threshold_detail = f"kappa={facts.kappa!r} < min_export_kappa={min_export_kappa!r}"

    return (ready_detail, status_detail, binding_detail, bound_detail, threshold_detail)


def evaluate_export_gate(
    agreement: AgreementReport,
    labeling: LabelingReport,
    *,
    min_export_kappa: float,
    override: ExportGateOverride | None = None,
) -> ExportGateDecision:
    """Evaluate the five gate checks; return the self-coherent decision.

    Pure: reads only the two self-validated reports and the injected knob. The κ
    compared is the report-stored (rounded) headline κ; ``>=`` passes. The override
    covers check 5 ONLY — an override alongside any other failure (or alongside no
    failure at all) is an ``ExportInputError`` at this seam, with a message naming
    the check, rather than a less legible refusal from the decision's validator.
    """
    facts = expected_gate_facts(agreement, labeling.judge, min_export_kappa)
    details = _check_details(agreement, labeling.judge, facts, min_export_kappa)
    checks = tuple(
        GateCheck(name=name, passed=passed, detail=detail)
        for name, passed, detail in zip(GateCheckName, facts.passed, details, strict=True)
    )
    failed = {check.name for check in checks if not check.passed}
    if override is not None:
        if not failed:
            raise ExportInputError(
                "override provided but every gate check passed — an override must "
                "override something (ADR-0005 options §4: no ritual flags)"
            )
        non_overridable = sorted(
            name.value for name in failed if name is not GateCheckName.KAPPA_THRESHOLD
        )
        if non_overridable:
            raise ExportInputError(
                f"override cannot cover non-overridable check {non_overridable[0]!r} — "
                "checks 1-4 mean the kappa does not exist or does not apply; there is "
                "no honest low number to carry (ADR-0005 options §4)"
            )
    if not failed:
        verdict = ExportGateVerdict.PASSED
    elif failed == {GateCheckName.KAPPA_THRESHOLD} and override is not None:
        verdict = ExportGateVerdict.PASSED_WITH_OVERRIDE
    else:
        verdict = ExportGateVerdict.BLOCKED
    return ExportGateDecision(
        min_export_kappa=min_export_kappa,
        checks=checks,
        kappa=facts.kappa,
        ci_lower=facts.ci_lower,
        ci_upper=facts.ci_upper,
        ci_straddles_threshold=facts.ci_straddles_threshold,
        override=override,
        verdict=verdict,
    )
