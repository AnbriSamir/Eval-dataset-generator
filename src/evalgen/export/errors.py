"""Typed refusals of the export stage (ADR-0005 rule 3).

A blocked gate, an unresolvable candidate, or an empty export is a NAMED error —
never a warning, never a partial artifact. ``ExportBlockedError`` carries the full
``ExportGateDecision`` so a caller can still render the gate table on refusal; it
never carries files, because nothing is written on the blocked path.
"""

from __future__ import annotations

from evalgen.contracts import ExportGateDecision


class ExportError(Exception):
    """Base class for every typed export failure."""


class ExportBlockedError(ExportError):
    """The gate blocked the export — nothing is written, the decision travels."""

    def __init__(self, decision: ExportGateDecision) -> None:
        failed = [check.name.value for check in decision.checks if not check.passed]
        super().__init__(
            f"export blocked by gate check(s) {failed!r} — nothing was written; "
            "the full decision rides this error"
        )
        self.decision = decision


class ExportInputError(ExportError):
    """A caller handed export incoherent inputs (unresolvable record_id, missing
    stratum, an override where none can apply) — a caller bug, never a statistic."""


class NothingToExportError(ExportError):
    """Every candidate was blocked — an empty golden.jsonl is not an export."""
