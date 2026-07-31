"""Typed refusals of the agreement protocol (ADR-0004 rules 2–3).

Every way a measurement can be invalid is a NAMED error, never a silent skip and
never a report with a footnote: mixed questionnaires, duplicated ground truth, a
malformed or unfilled human-label line, an empty join. The message always names the
offending line/id — a curated 30–50 line artifact deserves a loud, precise refusal
(the deliberate opposite of ingest's tolerant bucketing).
"""

from __future__ import annotations


class AgreementError(Exception):
    """Base class for every typed agreement-protocol failure."""


class TaxonomyMismatchError(AgreementError):
    """Human labels and judge labels answer different questionnaires — agreement
    between different taxonomies is not agreement (ADR-0003 rule 1)."""


class DuplicateHumanLabelError(AgreementError):
    """The same record_id is human-labeled more than once — ground truth must be
    single-valued before it can be joined."""


class HumanLabelFormatError(AgreementError):
    """A human-label line is malformed or unfilled (wraps the pydantic error and
    names the 1-based line) — an incomplete file cannot be measured by accident."""


class NoMatchedPairsError(AgreementError):
    """The judge/human join produced zero matched pairs — a report with n = 0 is a
    lie machine; there is nothing to diagnose (ADR-0004 options §4)."""
