"""Threshold-calibration contracts (ADR-0002 rule 4): the threshold is a measured artifact.

``near_dup_threshold = 0.92`` is a *sane default region*, not a measured number. The
measurement protocol (labeled pairs → similarity sweep → F1-argmax) emits a
:class:`ThresholdCalibrationReport` whose validator recomputes the argmax under the
tie rule — a report claiming a non-optimal choice refuses to exist, even when
deserialized from disk.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from evalgen.contracts.embeddings import EmbedderFingerprint


class LabeledPair(BaseModel):
    """One hand-labeled calibration pair (committed fixture line).

    Calibration texts never pass through ingest — they are secret-free by
    construction (redaction twins use already-redacted placeholders in both texts).
    """

    model_config = ConfigDict(frozen=True)

    pair_id: str = Field(min_length=1)
    text_a: str = Field(min_length=1)
    text_b: str = Field(min_length=1)
    label: Literal["duplicate", "distinct"]


class ThresholdCandidate(BaseModel):
    """One swept threshold with its duplicate-class metrics (all rounded to 6)."""

    model_config = ConfigDict(frozen=True)

    threshold: float = Field(ge=-1.0, le=1.0)
    precision: float = Field(ge=0.0, le=1.0)
    recall: float = Field(ge=0.0, le=1.0)
    f1: float = Field(ge=0.0, le=1.0)


class ThresholdCalibrationReport(BaseModel):
    """The sweep's full evidence; the chosen value must be the F1-argmax.

    Ties break to the HIGHEST threshold: prefer keeping data — a false drop destroys
    a real record, a false keep is caught by the next audit of the dedup report.
    """

    model_config = ConfigDict(frozen=True)

    embedder: EmbedderFingerprint
    pairs_duplicate: int = Field(ge=1)
    pairs_distinct: int = Field(ge=1)
    candidates: tuple[ThresholdCandidate, ...] = Field(min_length=1)
    chosen_threshold: float

    @model_validator(mode="after")
    def _chosen_must_be_the_argmax(self) -> ThresholdCalibrationReport:
        thresholds = [c.threshold for c in self.candidates]
        if thresholds != sorted(thresholds) or len(set(thresholds)) != len(thresholds):
            raise ValueError("candidates must be sorted by threshold ascending, unique")
        best = max(self.candidates, key=lambda c: (c.f1, c.threshold))
        if self.chosen_threshold != best.threshold:
            raise ValueError(
                f"chosen_threshold {self.chosen_threshold} is not the F1-argmax "
                f"(expected {best.threshold} at f1={best.f1}, ties to the highest "
                "threshold) — a report claiming a non-optimal choice refuses to exist"
            )
        return self
